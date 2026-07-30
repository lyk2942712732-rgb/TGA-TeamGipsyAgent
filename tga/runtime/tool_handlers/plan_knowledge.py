"""Controlled plan mutation and candidate Knowledge creation."""

from __future__ import annotations

import hashlib
from pathlib import Path

from tga.domain.knowledge.items import KnowledgeItem
from tga.domain.planning.global_plan import GlobalPlan
from tga.domain.planning.proposals import IntentProposal
from tga.evidence.database import utc_now
from tga.infrastructure.persistence import PersistenceBundle, PersistenceConflict
from tga.domain.retrieval import KnowledgeBase, OwnerScope
from tga.infrastructure.retrieval import StructuredDocumentParser
from tga.runtime.retrieval import RetrievalIndexService
from tga.inputs import task_artifact_root


class PlanKnowledgeHandler:
    def __init__(self, state) -> None:
        self.state = state
        self.task = state.task
        self.solver_id = state.solver_id
        self.repositories = PersistenceBundle(state.store)

    def record_action_result(self, result) -> list[KnowledgeItem]:
        """Persist facts/leads as Solver candidate Knowledge, never verified state."""
        self.index_artifacts(result)
        values = []
        if result.status == "succeeded":
            values.append(("hypothesis", str(result.summary)))
        values.extend([
            *(('fact', str(value)) for value in result.facts),
            *(('hypothesis', str(value)) for value in result.leads),
        ])
        created: list[KnowledgeItem] = []
        now = utc_now()
        for kind, content in values[:64]:
            text = content.strip()[:8_000]
            if not text:
                continue
            digest = hashlib.sha256(
                f"{result.action_id}:{kind}:{text}".encode("utf-8")
            ).hexdigest()[:20]
            item = KnowledgeItem(
                id=f"knowledge_{digest}",
                task_id=self.task.id,
                scope="solver",
                target_id=self.solver_id,
                status="candidate",
                kind=kind,
                content=text,
                evidence_claim_ids=[],
                created_by_solver_id=self.solver_id,
                created_at=now,
                provenance={
                    "action_id": result.action_id,
                    "legacy_artifact_ids": list(result.artifact_ids),
                    "evidence_confirmation_inferred": False,
                },
            )
            if any(
                existing.id == item.id
                for existing in self.repositories.knowledge.list_knowledge(self.task.id)
            ):
                continue
            self.repositories.knowledge.add_knowledge(item)
            self.repositories.events.append_agent_event(
                self.task.id,
                "CANDIDATE_KNOWLEDGE_ADDED",
                {
                    "knowledge_id": item.id,
                    "scope": item.scope,
                    "status": item.status,
                    "kind": item.kind,
                    "action_id": result.action_id,
                },
                solver_id=self.solver_id,
            )
            self.repositories.events.append_agent_event(
                self.task.id,
                "KNOWLEDGE_CANDIDATE_CREATED",
                {
                    "knowledge_id": item.id,
                    "scope": item.scope,
                    "kind": item.kind,
                    "action_id": result.action_id,
                },
                solver_id=self.solver_id,
            )
            created.append(item)
        return created

    def index_artifacts(self, result) -> None:
        """Best-effort derived indexing; never changes the authoritative tool result."""
        try:
            self._index_artifacts(result)
        except Exception as exc:
            self.repositories.events.append_agent_event(
                self.task.id,
                "RETRIEVAL_INDEX_FAILED",
                {
                    "artifact_ids": list(result.artifact_ids),
                    "error": str(exc)[:1_000],
                },
                solver_id=self.solver_id,
            )

    def _index_artifacts(self, result) -> None:
        artifact_ids = tuple(dict.fromkeys(str(item) for item in result.artifact_ids))
        if not artifact_ids:
            return
        owner = OwnerScope(scope="task", task_id=self.task.id)
        knowledge_base_id = f"kb_task_artifacts_{self.task.id}"
        knowledge_base = (
            self.repositories.retrieval.get_knowledge_base(knowledge_base_id)
            or KnowledgeBase(
                id=knowledge_base_id,
                name=f"Task Artifacts: {self.task.name}",
                owner=owner,
                description="Immutable tool outputs indexed as candidate evidence only.",
                created_at=utc_now(),
            )
        )
        indexer = RetrievalIndexService(
            self.repositories.retrieval,
            parser=StructuredDocumentParser(),
        )
        indexed = 0
        for artifact_id in artifact_ids:
            artifact = self.repositories.evidence.get_artifact(artifact_id)
            if artifact is None:
                continue
            try:
                raw = self._artifact_path(artifact).read_bytes()
                revision, chunks = indexer.ingest_task_artifact(
                    knowledge_base=knowledge_base,
                    artifact=artifact,
                    raw=raw,
                )
                indexed += len(chunks)
                self.repositories.events.append_agent_event(
                    self.task.id,
                    "RETRIEVAL_DOCUMENT_INDEXED",
                    {
                        "artifact_id": artifact.id,
                        "document_revision_id": revision.id,
                        "extraction_status": revision.extraction_status,
                        "chunk_count": len(chunks),
                        "semantics": "candidate_evidence_only",
                    },
                    solver_id=self.solver_id,
                )
            except Exception as exc:
                self.repositories.events.append_agent_event(
                    self.task.id,
                    "RETRIEVAL_INDEX_FAILED",
                    {
                        "artifact_id": artifact.id,
                        "error": str(exc)[:1_000],
                    },
                    solver_id=self.solver_id,
                )
        if indexed:
            visible_knowledge_bases = [
                item for item in self.repositories.retrieval.list_knowledge_bases()
                if item.status == "active"
                and (
                    item.owner.scope == "global"
                    or item.owner.scope == "task" and item.owner.task_id == self.task.id
                )
            ]
            knowledge_base_ids = tuple(sorted(
                {item.id for item in visible_knowledge_bases} | {knowledge_base.id}
            ))
            versions = [
                item.index_version
                for item in self.repositories.retrieval.list_snapshots()
                if item.owner == owner
                and knowledge_base.id in item.knowledge_base_ids
            ]
            snapshot = indexer.create_snapshot(
                owner=owner,
                knowledge_base_ids=knowledge_base_ids,
                index_version=max(versions, default=0) + 1,
            )
            for attempt in range(4):
                binding = self.repositories.retrieval.get_snapshot_binding(owner, "context")
                try:
                    updated_binding = self.repositories.retrieval.bind_snapshot(
                        owner=owner,
                        purpose="context",
                        snapshot_id=snapshot.id,
                        expected_snapshot_id=(
                            binding.index_snapshot_id if binding is not None else None
                        ),
                        updated_at=utc_now(),
                    )
                    break
                except PersistenceConflict:
                    if attempt == 3:
                        raise
            self.repositories.events.append_agent_event(
                self.task.id,
                "INDEX_SNAPSHOT_CREATED",
                {
                    "index_snapshot_id": snapshot.id,
                    "index_version": snapshot.index_version,
                    "knowledge_base_ids": list(snapshot.knowledge_base_ids),
                    "chunk_count": len(snapshot.chunk_ids),
                    "binding_version": updated_binding.version,
                    "previous_index_snapshot_id": (
                        binding.index_snapshot_id if binding is not None else None
                    ),
                },
                solver_id=self.solver_id,
            )

    def _artifact_path(self, artifact) -> Path:
        task_root = Path(self.state.run_root) / self.task.id
        roots = (
            task_artifact_root(task_root, self.task),
            (task_root / "workspace" / "shared" / "artifacts").resolve(),
        )
        for root in roots:
            candidate = (root / artifact.path).resolve()
            try:
                candidate.relative_to(root)
            except ValueError as exc:
                raise PermissionError("Artifact path escapes its immutable store") from exc
            if candidate.is_file():
                return candidate
        raise FileNotFoundError(f"Artifact bytes are unavailable: {artifact.id}")


class SingleSolverPlanService:
    """The phase-5 main Solver may update GlobalPlan only through CAS."""

    def __init__(self, repositories: PersistenceBundle, *, solver_id: str) -> None:
        self.repositories = repositories
        self.solver_id = solver_id

    def update_global_plan(
        self, replacement: GlobalPlan, *, expected_version: int
    ) -> None:
        solver = self.repositories.solvers.get_solver(self.solver_id)
        if solver is None or solver.task_id != replacement.task_id:
            raise PermissionError("Solver does not own this task plan boundary")
        if solver.completion_authority != "task":
            raise PermissionError("Solver lacks supervisor plan authority")
        self.repositories.plans.compare_and_swap_global_plan(
            replacement, expected_version=expected_version
        )
        self.repositories.events.append_agent_event(
            replacement.task_id,
            "PLAN_UPDATED",
            {
                "operation": "single_solver_plan_cas",
                "old_version": expected_version,
                "new_version": replacement.version,
            },
            solver_id=self.solver_id,
        )

    def propose_intent(self, proposal: IntentProposal) -> IntentProposal:
        if proposal.proposed_by_solver_id != self.solver_id:
            raise PermissionError("IntentProposal author does not match Solver")
        return proposal


__all__ = ["PlanKnowledgeHandler", "SingleSolverPlanService"]
