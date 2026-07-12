"""Observer sidecar: validates and applies board-only patches."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from tga.contracts import HypothesisStatus, MemoryKind
from tga.runtime.board import BoardStore, HypothesisDraft


class HypothesisUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    status: HypothesisStatus | None = None
    last_result: str = Field(default="", max_length=800)
    evidence_artifact_ids: list[str] = Field(default_factory=list)


class MemoryUpsert(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: MemoryKind
    content: str = Field(min_length=1, max_length=800)
    source: str
    artifact_ids: list[str] = Field(default_factory=list)
    supersedes_id: str | None = None


class NewHypothesis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    statement: str = Field(min_length=1)
    attack_class: str = Field(min_length=1)
    entry_point: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    next_test: str = Field(min_length=1)
    confidence: float = Field(default=0.5, ge=0, le=1)


class ObserverPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    memory_upserts: list[MemoryUpsert] = Field(default_factory=list, max_length=4)
    hypothesis_updates: list[HypothesisUpdate] = Field(default_factory=list, max_length=4)
    new_hypotheses: list[NewHypothesis] = Field(default_factory=list, max_length=2)
    reminder: str = Field(default="", max_length=280)


class Observer(Protocol):
    def review(self, snapshot: dict) -> ObserverPatch: ...


class BoardObserver:
    """Default no-op observer.  It cannot produce actions by construction."""

    def review(self, snapshot: dict) -> ObserverPatch:
        return ObserverPatch()

    @staticmethod
    def apply(*, board: BoardStore, task_id: str, patch: ObserverPatch) -> None:
        for raw in patch.memory_upserts:
            board.add_memory(task_id=task_id, **raw.model_dump())
        for update in patch.hypothesis_updates:
            if update.status:
                # Observer is never allowed to assert a verified conclusion;
                # decisive verification belongs to the solver + Manager gate.
                if update.status == "verified":
                    raise ValueError("observer cannot verify a hypothesis")
                board.transition_hypothesis(update.id, status=update.status, last_result=update.last_result, evidence_artifact_ids=update.evidence_artifact_ids)
        for raw in patch.new_hypotheses:
            board.create_hypothesis(task_id=task_id, draft=HypothesisDraft(**raw.model_dump()))


class ObserverSidecar:
    """Run limited observer review off the Solver's execution path.

    The worker only receives an immutable snapshot and returns a Pydantic patch.
    Database writes are always applied by the manager thread through BoardStore.
    """

    def __init__(self, observer: Observer):
        self.observer = observer
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tga-observer")
        self._pending: Future[ObserverPatch] | None = None

    def request(self, snapshot: dict) -> bool:
        if self._pending and not self._pending.done():
            return False
        self._pending = self._executor.submit(self.observer.review, snapshot)
        return True

    def drain(self, *, wait: bool = False) -> ObserverPatch | None:
        if self._pending is None:
            return None
        if not wait and not self._pending.done():
            return None
        future, self._pending = self._pending, None
        patch = future.result()
        return patch if isinstance(patch, ObserverPatch) else ObserverPatch.model_validate(patch)

    def close(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=True)


def build_observer_context(snapshot: dict) -> dict:
    """Expose only the bounded sidecar input defined by the runtime contract."""
    board = snapshot.get("board") or {}
    memory = board.get("memory") or []
    actions = snapshot.get("actions") or []
    return {
        "task": snapshot.get("task") or {},
        "session": snapshot.get("session") or {},
        "recent_actions": actions[-6:],
        "active_hypotheses": [item for item in board.get("hypotheses") or [] if item.get("status") in {"pending", "testing", "inconclusive"}],
        "recent_memory": memory[-20:],
        "user_hints": [item for item in memory[-20:] if item.get("kind") == "hint" and item.get("source") == "user"],
    }
