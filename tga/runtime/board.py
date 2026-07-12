"""Hypothesis and durable-memory rules for the v2 runtime."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from tga.contracts import Hypothesis, HypothesisStatus, MemoryEntry
from tga.evidence.store import EvidenceStore, utc_now


ACTIVE_MEMORY_LIMIT = 20


@dataclass(frozen=True)
class HypothesisDraft:
    statement: str
    attack_class: str
    entry_point: str
    rationale: str
    next_test: str
    confidence: float = 0.5


class BoardStore:
    def __init__(self, store: EvidenceStore):
        self.store = store

    def create_hypothesis(self, *, task_id: str, draft: HypothesisDraft, owner_solver_id: str | None = None) -> Hypothesis:
        self._validate_draft(draft)
        fingerprint = self._fingerprint(draft)
        for current in self.store.list_hypotheses(task_id, active_only=True):
            if self._fingerprint_from_model(current) == fingerprint:
                return current
        now = utc_now()
        hypothesis = Hypothesis(
            id=f"hyp_{uuid4().hex[:12]}", task_id=task_id, statement=draft.statement.strip(),
            attack_class=draft.attack_class.strip(), entry_point=draft.entry_point.strip(),
            rationale=draft.rationale.strip(), next_test=draft.next_test.strip(), confidence=draft.confidence,
            owner_solver_id=owner_solver_id, created_at=now, updated_at=now,
        )
        self.store.add_hypothesis(hypothesis)
        return hypothesis

    def transition_hypothesis(
        self, hypothesis_id: str, *, status: HypothesisStatus, last_result: str = "",
        evidence_artifact_ids: list[str] | None = None, proposed_by_solver: bool = False,
    ) -> Hypothesis:
        current = self.store.get_hypothesis(hypothesis_id)
        if current is None:
            raise KeyError(f"hypothesis not found: {hypothesis_id}")
        allowed = {
            "pending": {"testing", "superseded"},
            "testing": {"testing", "verified", "rejected", "inconclusive", "superseded"},
            "inconclusive": {"testing", "superseded"},
            "verified": {"superseded"}, "rejected": {"superseded"}, "superseded": set(),
        }
        if status not in allowed[current.status]:
            raise ValueError(f"invalid hypothesis transition {current.status} -> {status}")
        evidence_ids = evidence_artifact_ids if evidence_artifact_ids is not None else current.evidence_artifact_ids
        self._validate_artifact_ids(current.task_id, evidence_ids)
        if status == "verified" and (not evidence_ids or not proposed_by_solver):
            raise ValueError("verified hypothesis requires solver-proposed decisive evidence artifacts")
        if status == "rejected" and not last_result.strip():
            raise ValueError("rejected hypothesis must state the eliminated prerequisite")
        return self.store.update_hypothesis(
            hypothesis_id, status=status, last_result=last_result[:800], evidence_artifact_ids=evidence_ids,
            attempt_count=current.attempt_count + (1 if status == "testing" and current.status == "testing" else 0),
        )

    def add_memory(
        self, *, task_id: str, kind: str, content: str, source: str, artifact_ids: list[str] | None = None,
        supersedes_id: str | None = None,
    ) -> MemoryEntry:
        artifact_ids = artifact_ids or []
        self._validate_artifact_ids(task_id, artifact_ids)
        if source not in {"user", "system"} and not artifact_ids:
            raise ValueError("non-user memory requires an evidence artifact reference")
        if len(self.store.list_memory(task_id)) >= ACTIVE_MEMORY_LIMIT:
            self._compact_memory(task_id)
        now = utc_now()
        entry = MemoryEntry(
            id=f"mem_{uuid4().hex[:12]}", task_id=task_id, kind=kind, content=content,
            artifact_ids=artifact_ids, source=source, supersedes_id=supersedes_id, created_at=now, updated_at=now,
        )
        self.store.add_memory(entry)
        if supersedes_id:
            self.store.supersede_memory(supersedes_id, entry.id)
        return entry

    def _compact_memory(self, task_id: str) -> None:
        """Keep active memory bounded without discarding provenance."""
        active = self.store.list_memory(task_id)
        if len(active) < ACTIVE_MEMORY_LIMIT:
            return
        replaced = active[:2]
        now = utc_now()
        summary = MemoryEntry(
            id=f"mem_{uuid4().hex[:12]}", task_id=task_id, kind="decision",
            content=("Compacted prior memory: " + " | ".join(item.content[:180] for item in replaced))[:800],
            artifact_ids=list(dict.fromkeys(artifact_id for item in replaced for artifact_id in item.artifact_ids)),
            source="system", created_at=now, updated_at=now,
        )
        self.store.add_memory(summary)
        for item in replaced:
            self.store.supersede_memory(item.id, summary.id)

    def _validate_artifact_ids(self, task_id: str, artifact_ids: list[str]) -> None:
        for artifact_id in artifact_ids:
            artifact = self.store.get_artifact(artifact_id)
            if artifact is None or artifact.task_id != task_id:
                raise ValueError(f"unknown artifact reference: {artifact_id}")

    @staticmethod
    def _validate_draft(draft: HypothesisDraft) -> None:
        for label, value in (("statement", draft.statement), ("attack_class", draft.attack_class), ("entry_point", draft.entry_point), ("rationale", draft.rationale), ("next_test", draft.next_test)):
            if not value.strip():
                raise ValueError(f"hypothesis {label} is required")

    @staticmethod
    def _fingerprint(draft: HypothesisDraft) -> str:
        return "|".join((draft.attack_class, draft.entry_point, draft.statement)).casefold().strip()

    @classmethod
    def _fingerprint_from_model(cls, hypothesis: Hypothesis) -> str:
        return cls._fingerprint(HypothesisDraft(
            statement=hypothesis.statement, attack_class=hypothesis.attack_class, entry_point=hypothesis.entry_point,
            rationale=hypothesis.rationale, next_test=hypothesis.next_test, confidence=hypothesis.confidence,
        ))
