"""Read-only observer coordination for strategy and evidence-memory advice."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
import hashlib
import json
import re
from time import monotonic
from typing import Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from tga.contracts import MemoryEntry, MemoryKind
from tga.evidence.store import EvidenceStore, utc_now


class MemorySuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: MemoryKind
    content: str = Field(min_length=1, max_length=800)
    source: str = "observer"
    artifact_ids: list[str] = Field(default_factory=list)
    supersedes_id: str | None = None


class ObserverSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    memory_suggestions: list[MemorySuggestion] = Field(default_factory=list, max_length=4)
    strategy_advice: str = Field(default="", max_length=280)


class Observer(Protocol):
    def review(self, snapshot: dict) -> ObserverSuggestion: ...


class DeterministicObserver:
    """Derive bounded advice from execution facts without asserting completion."""

    def review(self, snapshot: dict) -> ObserverSuggestion:
        actions = snapshot.get("recent_actions") or []
        triggers = snapshot.get("triggers") or []
        artifact_ids = list(dict.fromkeys(
            artifact_id
            for item in actions[-3:]
            for artifact_id in ((item.get("result") or {}).get("artifact_ids") or [])
        ))[:8]
        memories: list[MemorySuggestion] = []
        advice = ""
        if "consecutive_failures" in triggers:
            summary = " | ".join(
                str((item.get("result") or {}).get("summary") or "")[:180]
                for item in actions[-3:]
                if item.get("status") in {"failed", "blocked"}
            )
            if artifact_ids and summary:
                memories.append(MemorySuggestion(
                    kind="failure_boundary",
                    content=("连续失败，重试前必须重新诊断：" + summary)[:800],
                    artifact_ids=artifact_ids,
                ))
            advice = "重试前请更换证据、参数或验证目的。"
        if "semantic_repeat" in triggers:
            advice = "请提供与新证据、参数变化或明确验证目的相关的重试理由。"
        if "marker_missing" in triggers:
            advice = "未观察到成功标记；请检查编码、参数和前置条件。"
        if "http_session_anomaly" in triggers:
            advice = "提高操作影响前，请先诊断 HTTP 会话连续性。"
        if "context_budget" in triggers:
            advice = "请限制证据产物读取范围，仅保留来源引用和可复用结论。"
        if "high_side_effect" in triggers:
            advice = "请记录预期副作用，并优先比较影响更低的取证路径。"
        return ObserverSuggestion(memory_suggestions=memories, strategy_advice=advice[:280])


class ObserverCoordinator:
    """Runs observer review and persists only evidence-memory suggestions."""

    def __init__(self, *, observer: Observer, store: EvidenceStore, cooldown_seconds: float = 30.0):
        self.observer = observer
        self.store = store
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tga-observer")
        self._pending: Future[ObserverSuggestion] | None = None
        self._cooldown_seconds = max(0.0, cooldown_seconds)
        self._last_fingerprint = ""
        self._last_emitted_at = 0.0

    def request(self, snapshot: dict) -> bool:
        if self._pending and not self._pending.done():
            return False
        self._pending = self._executor.submit(self.observer.review, snapshot)
        return True

    def drain(self, *, wait: bool = False) -> ObserverSuggestion | None:
        if self._pending is None or (not wait and not self._pending.done()):
            return None
        future, self._pending = self._pending, None
        raw = future.result()
        suggestion = raw if isinstance(raw, ObserverSuggestion) else ObserverSuggestion.model_validate(raw)
        fingerprint = hashlib.sha256(
            json.dumps(suggestion.model_dump(mode="json"), sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        now = monotonic()
        if fingerprint == self._last_fingerprint and now - self._last_emitted_at < self._cooldown_seconds:
            return None
        self._last_fingerprint = fingerprint
        self._last_emitted_at = now
        return suggestion

    def apply(self, *, task_id: str, suggestion: ObserverSuggestion) -> None:
        with self.store.transaction():
            for raw in suggestion.memory_suggestions:
                now = utc_now()
                self.store.add_memory(MemoryEntry(
                    id=f"memory_{uuid4().hex[:12]}",
                    task_id=task_id,
                    kind=raw.kind,
                    content=raw.content,
                    artifact_ids=raw.artifact_ids,
                    source=raw.source,
                    supersedes_id=raw.supersedes_id,
                    created_at=now,
                    updated_at=now,
                ))

    def close(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=True)


def native_observer_triggers(*, actions: list[dict], current: dict | None = None, context_chars: int = 0) -> list[str]:
    triggers: list[str] = []
    recent = [*actions[-5:], *([current] if current else [])]
    if len([item for item in recent[-3:] if item.get("status") in {"failed", "blocked"}]) >= 2:
        triggers.append("consecutive_failures")
    if current:
        result = current.get("result") or {}
        if any("expected marker not observed" in str(item).casefold() for item in result.get("leads") or []):
            triggers.append("marker_missing")
        effect = current.get("effect") if isinstance(current.get("effect"), dict) else {}
        if current.get("risk") == "destructive" or effect.get("persistence") == "persistent":
            triggers.append("high_side_effect")
    if context_chars > 80_000:
        triggers.append("context_budget")
    return list(dict.fromkeys(triggers))


def build_observer_context(snapshot: dict) -> dict:
    runtime = snapshot.get("runtime") or {}
    memory = runtime.get("memory") or []
    actions = snapshot.get("actions") or []
    task = snapshot.get("task") or {}
    session = snapshot.get("session") or {}
    return {
        "schema_version": 2,
        "task": {"id": task.get("id"), "name": _redact(task.get("name")), "mode": task.get("mode"), "goal": _redact(task.get("goal"))},
        "session": {key: session.get(key) for key in ("status", "turn_count", "max_turns", "stop_reason")},
        "recent_actions": [
            {
                "id": item.get("id"),
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
        "recent_memory": [_redacted_memory(item) for item in memory[-12:]],
        "strategy_cards": runtime.get("strategy_cards") or [],
        "challenge": {
            "status": (snapshot.get("challenge") or {}).get("status"),
            "status_reason": _redact((snapshot.get("challenge") or {}).get("status_reason")),
            "completion_proof_artifact_id": (snapshot.get("challenge") or {}).get("completion_proof_artifact_id"),
        },
    }


def _redacted_memory(item: dict) -> dict:
    return {"id": item.get("id"), "kind": item.get("kind"), "content": _redact(item.get("content")), "artifact_ids": item.get("artifact_ids") or [], "source": item.get("source")}


def _redact(value: object) -> str:
    return re.sub(
        r"(?i)((?:authorization|cookie|set-cookie|token|secret|api[_-]?key|password)\s*[:=]\s*)([^\s;,]+)",
        r"\1[REDACTED]",
        str(value or ""),
    )[:800]
