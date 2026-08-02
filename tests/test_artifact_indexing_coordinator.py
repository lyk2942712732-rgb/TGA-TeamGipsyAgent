from __future__ import annotations

import hashlib

from tga.contracts import TGATask
from tests.runtime_fixtures import task as v6_task
from tga.domain.evidence import Artifact
from tga.domain.retrieval import OwnerScope, RetrievalPolicy
from tga.domain.task.spec import TaskSpec
from tga.infrastructure.persistence import PersistenceBundle
from tga.runtime.retrieval import RetrievalService
from tga.application.services import ArtifactIndexingCoordinator


def _artifact(task_id: str, artifact_id: str, raw: bytes) -> Artifact:
    return Artifact(
        id=artifact_id,
        task_id=task_id,
        kind="tool_output",
        path=f"{artifact_id}.txt",
        sha256=hashlib.sha256(raw).hexdigest(),
        media_type="text/plain",
        created_at="2026-07-31T00:00:00Z",
    )


def _policy() -> RetrievalPolicy:
    return RetrievalPolicy(
        allowed_owner_scopes=("task", "solver"),
        allowed_trust_levels=("unverified",),
        task_artifact_access=True,
        max_results=10,
        max_context_tokens=2_000,
    )


def test_artifact_indexing_is_idempotent_and_advances_context_snapshot(tmp_path: Path) -> None:
    bundle = PersistenceBundle.open(tmp_path / "evidence.db")
    task = v6_task(id="task_auto_index", name="Auto index", mode="ctf", goal="find marker")
    bundle.tasks.create_task(task)
    bundle.tasks.save_task_spec(TaskSpec(task_id=task.id, objective=task.goal))
    raw_values: dict[str, bytes] = {}
    try:
        first = _artifact(task.id, "artifact_first", b"first marker")
        second = _artifact(task.id, "artifact_second", b"second marker")
        for item in (first, second):
            raw_values[item.id] = (
                b"first marker" if item.id == first.id else b"second marker"
            )
            bundle.evidence.add_artifact(item)
        coordinator = ArtifactIndexingCoordinator(
            repositories=bundle.retrieval,
            raw_loader=lambda item: raw_values[item.id],
            event_repository=bundle.events,
        )

        first_projection = coordinator.index(first, task_name=task.name)
        first_snapshot = bundle.retrieval.get_snapshot(first_projection.snapshot_id)
        assert first_projection.status == "indexed"
        assert first_snapshot is not None
        assert len(bundle.retrieval.list_sources()) == 1

        repeated = coordinator.index(first, task_name=task.name)
        assert repeated == first_projection
        assert repeated.attempt == 1
        assert len(bundle.retrieval.list_sources()) == 1

        second_projection = coordinator.index(second, task_name=task.name)
        second_snapshot = bundle.retrieval.get_snapshot(second_projection.snapshot_id)
        assert second_snapshot is not None
        assert second_snapshot.id != first_snapshot.id
        assert set(first_snapshot.chunk_ids).issubset(second_snapshot.chunk_ids)
        assert set(second_snapshot.chunk_ids) - set(first_snapshot.chunk_ids)
        binding = bundle.retrieval.get_snapshot_binding(
            OwnerScope(scope="task", task_id=task.id), "context"
        )
        assert binding is not None and binding.index_snapshot_id == second_snapshot.id

        pack = RetrievalService(bundle.retrieval).retrieve_for_context(
            task_id=task.id,
            solver_id="solver_auto_index",
            intent_id=None,
            query="second marker",
            policy=_policy(),
        )
        assert pack is not None
        assert any("second marker" in item.content for item in pack.items)
        event_types = [item.type for item in bundle.events.list_agent_events(task.id)]
        assert "ARTIFACT_INDEXED" in event_types
        assert "INDEX_SNAPSHOT_CREATED" in event_types
        assert "INDEX_BINDING_UPDATED" in event_types
    finally:
        bundle.close()


def test_artifact_indexing_failure_is_persisted_and_retryable(tmp_path: Path) -> None:
    bundle = PersistenceBundle.open(tmp_path / "evidence.db")
    task = v6_task(id="task_retry_index", name="Retry index", mode="ctf", goal="retry")
    bundle.tasks.create_task(task)
    bundle.tasks.save_task_spec(TaskSpec(task_id=task.id, objective=task.goal))
    raw = b"recoverable marker"
    artifact = _artifact(task.id, "artifact_retry", raw)
    bundle.evidence.add_artifact(artifact)
    available = False

    def load(_artifact):
        if not available:
            raise OSError("artifact bytes temporarily unavailable")
        return raw

    try:
        coordinator = ArtifactIndexingCoordinator(
            repositories=bundle.retrieval,
            raw_loader=load,
            event_repository=bundle.events,
        )
        failed = coordinator.index(artifact, task_name=task.name)
        assert failed.status == "failed"
        assert failed.error_code == "ARTIFACT_BYTES_UNAVAILABLE"
        assert failed.retryable is True
        assert failed.attempt == 1

        available = True
        recovered = coordinator.index(artifact, task_name=task.name)
        assert recovered.status == "indexed"
        assert recovered.attempt == 2
        assert recovered.snapshot_id is not None
        failures = [
            item for item in bundle.events.list_agent_events(task.id)
            if item.type == "ARTIFACT_INDEXING_FAILED"
        ]
        assert failures and failures[0].payload["retryable"] is True
    finally:
        bundle.close()
