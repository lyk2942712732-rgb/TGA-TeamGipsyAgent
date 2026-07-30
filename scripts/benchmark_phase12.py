"""Offline Phase-12 latency baseline; never contacts an external target."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import statistics
import tempfile
import time
from pathlib import Path
from typing import Callable

from tga.contracts import SessionRecord, TGATask
from tga.domain.evidence import Artifact
from tga.domain.evidence.legacy_models import AgentEvent
from tga.domain.retrieval import (
    ChunkLocator,
    CorpusDocument,
    CorpusSource,
    DocumentChunk,
    DocumentRevision,
    IndexSnapshot,
    KnowledgeBase,
    OwnerScope,
    RetrievalPolicy,
    RetrievalRequest,
)
from tga.domain.task.spec import TaskSpec
from tga.evidence.database import utc_now
from tga.evidence.store import EvidenceStore
from tga.infrastructure.events import InProcessEventBus
from tga.infrastructure.persistence import PersistenceBundle
from tga.runtime.context import ContextBuilder
from tga.runtime.orchestration import TaskOrchestrator
from tga.runtime.retrieval import RetrievalService
from tga.runtime.service import TaskRuntimeService


def _measure(call: Callable[[], object], *, iterations: int = 20) -> dict[str, float]:
    for _ in range(2):
        call()
    samples: list[float] = []
    for _ in range(iterations):
        start = time.perf_counter()
        call()
        samples.append((time.perf_counter() - start) * 1_000)
    ordered = sorted(samples)
    return {
        "median_ms": round(statistics.median(ordered), 3),
        "p95_ms": round(ordered[max(0, int(len(ordered) * 0.95) - 1)], 3),
        "iterations": float(iterations),
    }


async def _sse_wake_samples(task_id: str, *, iterations: int = 20) -> dict[str, float]:
    bus = InProcessEventBus()
    values: list[float] = []
    for seq in range(1, iterations + 1):
        waiter = asyncio.create_task(bus.wait(task_id, after_seq=seq - 1, timeout=1))
        await asyncio.sleep(0)
        start = time.perf_counter()
        bus.publish(AgentEvent(
            id=f"bench_bus_{seq}", task_id=task_id, seq=seq,
            type="BENCH_EVENT", payload={}, created_at=utc_now(),
        ))
        assert await waiter
        values.append((time.perf_counter() - start) * 1_000)
    ordered = sorted(values)
    return {
        "median_ms": round(statistics.median(ordered), 3),
        "p95_ms": round(ordered[max(0, int(len(ordered) * 0.95) - 1)], 3),
        "iterations": float(iterations),
    }


def _seed_retrieval(bundle: PersistenceBundle, now: str):
    owner = OwnerScope(scope="global")
    kb = KnowledgeBase(id="bench_kb", name="Benchmark KB", owner=owner, created_at=now)
    source = CorpusSource(
        id="bench_source", knowledge_base_id=kb.id, name="Benchmark source",
        kind="documentation", channel="reference", owner=owner,
        trust_level="trusted", created_at=now,
    )
    document = CorpusDocument(
        id="bench_document", source_id=source.id, knowledge_base_id=kb.id,
        owner=owner, title="Benchmark document", current_revision_id="bench_revision",
        created_at=now,
    )
    content = "bounded retrieval benchmark needle with provenance"
    revision = DocumentRevision(
        id="bench_revision", document_id=document.id, source_id=source.id,
        owner=owner, revision=1, content_sha256=hashlib.sha256(content.encode()).hexdigest(),
        extraction_status="parsed", media_type="text/plain", created_at=now,
    )
    chunk = DocumentChunk(
        id="bench_chunk", knowledge_base_id=kb.id, source_id=source.id,
        document_id=document.id, revision_id=revision.id, channel="reference",
        owner=owner, trust_level="trusted", content=content,
        content_sha256=hashlib.sha256(content.encode()).hexdigest(), token_count=12,
        locator=ChunkLocator(kind="text_range", char_start=0, char_end=len(content)),
        created_at=now,
    )
    snapshot = IndexSnapshot(
        id="bench_index", owner=owner, knowledge_base_ids=(kb.id,),
        source_ids=(source.id,), document_hashes={document.id: chunk.content_sha256},
        chunk_ids=(chunk.id,), chunking_version="structured-v1", index_version=1,
        created_at=now,
    )
    bundle.retrieval.add_knowledge_base(kb)
    bundle.retrieval.add_source(source)
    bundle.retrieval.add_document(document)
    bundle.retrieval.add_revision(revision)
    bundle.retrieval.add_chunks((chunk,))
    bundle.retrieval.save_snapshot(snapshot)
    return owner, kb, snapshot


def run_baseline() -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="tga-phase12-benchmark-") as directory:
        run_root = Path(directory) / "runs"
        task_id = "phase12_benchmark"
        task_root = run_root / task_id
        store = EvidenceStore(task_root / "evidence.db")
        task = TGATask(
            id=task_id, name="Offline benchmark", mode="ctf", goal="measure local paths",
            execution_budget={"max_active_workers": 2, "max_total_solvers": 8},
        )
        try:
            store.create_task(task)
            store.create_session(SessionRecord(task_id=task.id, schema_version=6))
            bundle = PersistenceBundle(store)
            bundle.tasks.save_task_spec(TaskSpec(task_id=task.id, objective=task.goal))
            orchestrator = TaskOrchestrator(task=task, repositories=bundle)
            state = orchestrator.bootstrap()
            orchestrator.create_intent(
                supervisor_solver_id=state.supervisor_solver_id or "",
                kind="validation", title="Independent validation",
                objective="validate the offline benchmark fixture",
            )
            start = time.perf_counter()
            assignments = orchestrator.dispatch_ready(limit=2)
            two_worker_ms = round((time.perf_counter() - start) * 1_000, 3)
            assert len(assignments) == 2

            for seq in range(1_000):
                bundle.events.append_agent_event(
                    task.id, "BENCHMARK_EVENT", {"index": seq},
                    solver_id=assignments[seq % 2].solver_id,
                    intent_id=assignments[seq % 2].intent_id,
                )
            artifact = Artifact(
                id="bench_artifact", task_id=task.id, kind="fixture",
                path="bench.txt", sha256="a" * 64, created_at=utc_now(),
            )
            bundle.evidence.add_artifact(artifact)
            owner, kb, index = _seed_retrieval(bundle, utc_now())
            retrieval = RetrievalService(bundle.retrieval)
            policy = RetrievalPolicy(
                allowed_owner_scopes=("global",),
                allowed_trust_levels=("trusted",), max_results=10,
                max_context_tokens=500,
            )
            retrieval_samples: list[float] = []
            for number in range(10):
                request = RetrievalRequest(
                    id=f"bench_request_{number}", owner=owner, query="needle provenance",
                    index_snapshot_id=index.id, channels=("reference",),
                    knowledge_base_ids=(kb.id,), created_at=utc_now(),
                )
                started = time.perf_counter()
                retrieval.retrieve(request, policy)
                retrieval_samples.append((time.perf_counter() - started) * 1_000)

            audit_messages = [{"role": "system", "content": "benchmark"}]
            for number in range(2_000):
                audit_messages.append({
                    "role": "assistant" if number % 2 == 0 else "tool",
                    "content": f"bounded transcript message {number} " + "x" * 200,
                })
            context = ContextBuilder(
                task=task, solver_id=assignments[0].solver_id,
                repositories=bundle, audit_messages=audit_messages,
            )
            service = TaskRuntimeService(run_root=run_root)
            retrieval_ordered = sorted(retrieval_samples)
            results: dict[str, object] = {
                "environment": {
                    "storage": "temporary local SQLite",
                    "external_network": False,
                    "event_fixture_count": 1_000,
                    "transcript_message_count": 2_001,
                },
                "snapshot_query": _measure(lambda: service.runtime_snapshot(task.id), iterations=20),
                "event_page_200": _measure(lambda: service.event_page(task.id, after_seq=100, limit=200), iterations=50),
                "sse_bus_wake": asyncio.run(_sse_wake_samples(task.id)),
                "two_worker_dispatch": {"elapsed_ms": two_worker_ms, "workers": 2},
                "long_transcript_context": _measure(context.build, iterations=20),
                "artifact_lookup": _measure(lambda: bundle.evidence.get_artifact(artifact.id), iterations=100),
                "rag_retrieval": {
                    "median_ms": round(statistics.median(retrieval_ordered), 3),
                    "p95_ms": round(retrieval_ordered[-1], 3),
                    "iterations": 10,
                },
            }
            return results
        finally:
            store.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run_baseline()
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
