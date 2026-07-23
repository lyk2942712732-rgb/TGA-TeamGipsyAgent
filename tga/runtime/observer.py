"""Observer sidecar: validates and applies board-only patches."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
import hashlib
import json
import re
from time import monotonic
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
    steer_message: str = Field(default="", max_length=280)

    @model_validator(mode="before")
    @classmethod
    def accept_legacy_reminder(cls, value):
        if isinstance(value, dict) and "reminder" in value and "steer_message" not in value:
            value = dict(value)
            value["steer_message"] = value.pop("reminder")
        return value

    @property
    def reminder(self) -> str:
        """Compatibility accessor for pre-advanced v2 observers."""
        return self.steer_message


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


class DeterministicObserver:
    """High-signal rules for the native path; it never owns tool execution."""

    def review(self, snapshot: dict) -> ObserverPatch:
        actions = snapshot.get("recent_actions") or []
        triggers = snapshot.get("triggers") or []
        artifact_ids = [
            artifact_id
            for item in actions[-3:]
            for artifact_id in ((item.get("result") or {}).get("artifact_ids") or [])
        ]
        memories: list[MemoryUpsert] = []
        steer = ""
        if "consecutive_failures" in triggers:
            summary = " | ".join(
                str((item.get("result") or {}).get("summary") or "")[:180]
                for item in actions[-3:]
                if item.get("status") in {"failed", "blocked"}
            )
            if artifact_ids and summary:
                memories.append(MemoryUpsert(
                    kind="failure_boundary",
                    content=("Consecutive failures require a new diagnosis before retry: " + summary)[:800],
                    source="observer",
                    artifact_ids=list(dict.fromkeys(artifact_ids))[:8],
                ))
            steer = "Pause the current repetition. State a new failure hypothesis and change evidence, parameters, or validation purpose before retrying."
        if "semantic_repeat" in triggers:
            steer = "This semantic action repeats an existing result. Supply a retry reason tied to new evidence, changed parameters, or an explicit verification purpose."
        if "marker_missing" in triggers:
            steer = "The declared success marker was not observed. Check request encoding, parameter assertions, and session prerequisites before changing the exploit path."
        if "http_session_anomaly" in triggers:
            steer = "The HTTP session profile was rebuilt or changed. Diagnose Cookie continuity before switching to a higher-side-effect path."
        if "context_budget" in triggers:
            steer = "Working context exceeded its budget. Use Artifact keyword/section retrieval and keep only source refs and durable conclusions."
        if "high_side_effect" in triggers:
            steer = "Before this persistent-state action, record expected side effects and compare a lower-impact evidence path."
        return ObserverPatch(memory_upserts=memories[:4], steer_message=steer[:280])


def native_observer_triggers(*, actions: list[dict], current: dict | None = None, context_chars: int = 0) -> list[str]:
    """Compute event triggers from safe action summaries, never raw secrets."""
    triggers: list[str] = []
    recent = [*actions[-5:], *([current] if current else [])]
    failures = [item for item in recent[-3:] if item.get("status") in {"failed", "blocked"}]
    if len(failures) >= 2:
        triggers.append("consecutive_failures")
    if current:
        result = current.get("result") or {}
        if any("expected marker not observed" in str(item).casefold() for item in result.get("leads") or []):
            triggers.append("marker_missing")
        if current.get("risk") == "destructive" or current.get("expected_side_effects"):
            triggers.append("high_side_effect")
    if context_chars > 80_000:
        triggers.append("context_budget")
    return list(dict.fromkeys(triggers))


class ObserverSidecar:
    """Run limited observer review off the Solver's execution path.

    The worker only receives an immutable snapshot and returns a Pydantic patch.
    Database writes are always applied by the manager thread through BoardStore.
    """

    def __init__(self, observer: Observer, *, cooldown_seconds: float = 30.0):
        self.observer = observer
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tga-observer")
        self._pending: Future[ObserverPatch] | None = None
        self._cooldown_seconds = max(0.0, cooldown_seconds)
        self._last_fingerprint = ""
        self._last_emitted_at = 0.0

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
        parsed = patch if isinstance(patch, ObserverPatch) else ObserverPatch.model_validate(patch)
        fingerprint = hashlib.sha256(
            json.dumps(parsed.model_dump(mode="json"), sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        now = monotonic()
        if fingerprint == self._last_fingerprint and now - self._last_emitted_at < self._cooldown_seconds:
            return None
        self._last_fingerprint = fingerprint
        self._last_emitted_at = now
        return parsed

    def close(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=True)


def build_observer_context(snapshot: dict) -> dict:
    """Expose only the bounded sidecar input defined by the runtime contract."""
    board = snapshot.get("board") or {}
    memory = board.get("memory") or []
    actions = snapshot.get("actions") or []
    task = snapshot.get("task") or {}
    session = snapshot.get("session") or {}
    return {
        "schema_version": 2,
        "task": {
            "id": task.get("id"),
            "name": _redact(task.get("name")),
            "mode": task.get("mode"),
            "goal": _redact(task.get("goal")),
        },
        "session": {
            "status": session.get("status"),
            "turn_count": session.get("turn_count"),
            "max_turns": session.get("max_turns"),
            "stop_reason": session.get("stop_reason"),
        },
        "recent_actions": [
            {
                "id": item.get("id"),
                "solver_id": item.get("solver_id"),
                "hypothesis_id": item.get("hypothesis_id"),
                "capability": item.get("capability"),
                "status": item.get("status"),
                "result": {
                    "summary": _redact((item.get("result") or {}).get("summary")),
                    "artifact_ids": (item.get("result") or {}).get("artifact_ids") or [],
                    "error": (item.get("result") or {}).get("error"),
                },
            }
            for item in actions[-6:]
        ],
        "active_hypotheses": [
            {
                "id": item.get("id"),
                "statement": _redact(item.get("statement")),
                "attack_class": item.get("attack_class"),
                "status": item.get("status"),
                "confidence": item.get("confidence"),
                "last_result": _redact(item.get("last_result")),
                "evidence_artifact_ids": item.get("evidence_artifact_ids") or [],
            }
            for item in board.get("hypotheses") or []
            if item.get("status") in {"pending", "testing", "inconclusive"}
        ],
        "recent_memory": [_redacted_memory(item) for item in memory[-12:]],
        "user_hints": [_redacted_memory(item) for item in memory[-12:] if item.get("kind") == "hint" and item.get("source") == "user"],
        "coverage_gaps": [
            gap
            for item in (snapshot.get("subagents") or [])
            for gap in ((item.get("output") or {}).get("coverage_gaps") or [])
        ][-8:],
        "challenge": {
            "status": (snapshot.get("challenge") or {}).get("status"),
            "status_reason": _redact((snapshot.get("challenge") or {}).get("status_reason")),
            "completion_proof_artifact_id": (snapshot.get("challenge") or {}).get("completion_proof_artifact_id"),
        },
    }


def _redacted_memory(item: dict) -> dict:
    return {
        "id": item.get("id"),
        "kind": item.get("kind"),
        "content": _redact(item.get("content")),
        "artifact_ids": item.get("artifact_ids") or [],
        "source": item.get("source"),
    }


def _redact(value: object) -> str:
    text = str(value or "")
    return re.sub(
        r"(?i)((?:authorization|cookie|set-cookie|token|secret|api[_-]?key|password)\s*[:=]\s*)([^\s;,]+)",
        r"\1[REDACTED]",
        text,
    )[:800]
