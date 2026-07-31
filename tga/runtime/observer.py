"""Read-only observer coordination for bounded execution advice."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
import hashlib
import json
import re
from time import monotonic
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from tga.evidence.store import EvidenceStore


class ObserverSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy_advice: str = Field(default="", max_length=280)


class Observer(Protocol):
    def review(self, snapshot: dict) -> ObserverSuggestion: ...


class DeterministicObserver:
    """Derive bounded advice from execution facts without asserting completion."""

    def review(self, snapshot: dict) -> ObserverSuggestion:
        triggers = snapshot.get("triggers") or []
        advice = ""
        if "consecutive_failures" in triggers:
            advice = (
                "Before retrying, change the evidence source, parameters, or "
                "validation objective."
            )
        if "semantic_repeat" in triggers:
            advice = (
                "Provide a retry reason tied to new evidence, changed parameters, "
                "or an explicit verification objective."
            )
        if "marker_missing" in triggers:
            advice = (
                "The expected success marker was not observed. Check encoding, "
                "parameters, and prerequisites."
            )
        if "http_session_anomaly" in triggers:
            advice = "Diagnose HTTP session continuity before increasing action impact."
        if "context_budget" in triggers:
            advice = (
                "Reduce evidence retrieval scope and retain only provenance references "
                "and reusable conclusions."
            )
        if "high_side_effect" in triggers:
            advice = (
                "Record the expected side effects and compare a lower-impact evidence "
                "path before execution."
            )
        return ObserverSuggestion(strategy_advice=advice[:280])


class ObserverCoordinator:
    """Run bounded observer review without writing Runtime state."""

    def __init__(
        self,
        *,
        observer: Observer,
        store: EvidenceStore,
        cooldown_seconds: float = 30.0,
    ) -> None:
        self.observer = observer
        self.store = store
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="tga-observer"
        )
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
        suggestion = (
            raw
            if isinstance(raw, ObserverSuggestion)
            else ObserverSuggestion.model_validate(raw)
        )
        fingerprint = hashlib.sha256(
            json.dumps(
                suggestion.model_dump(mode="json"),
                sort_keys=True,
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()
        now = monotonic()
        if (
            fingerprint == self._last_fingerprint
            and now - self._last_emitted_at < self._cooldown_seconds
        ):
            return None
        self._last_fingerprint = fingerprint
        self._last_emitted_at = now
        return suggestion

    def close(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=True)


def native_observer_triggers(
    *, actions: list[dict], current: dict | None = None, context_chars: int = 0
) -> list[str]:
    triggers: list[str] = []
    recent = [*actions[-5:], *([current] if current else [])]
    if len([
        item for item in recent[-3:]
        if item.get("status") in {"failed", "blocked"}
    ]) >= 2:
        triggers.append("consecutive_failures")
    if current:
        result = current.get("result") or {}
        if any(
            "expected marker not observed" in str(item).casefold()
            for item in result.get("leads") or []
        ):
            triggers.append("marker_missing")
        effect = current.get("effect") if isinstance(current.get("effect"), dict) else {}
        if (
            current.get("risk") == "destructive"
            or effect.get("persistence") == "persistent"
        ):
            triggers.append("high_side_effect")
    if context_chars > 80_000:
        triggers.append("context_budget")
    return list(dict.fromkeys(triggers))


def build_observer_context(snapshot: dict) -> dict:
    actions = snapshot.get("actions") or []
    task = snapshot.get("task") or {}
    session = snapshot.get("session") or {}
    return {
        "schema_version": 3,
        "task": {
            "id": task.get("id"),
            "name": _redact(task.get("name")),
            "mode": task.get("mode"),
            "goal": _redact(task.get("goal")),
        },
        "session": {
            key: session.get(key)
            for key in ("status", "turn_count", "max_turns", "stop_reason")
        },
        "recent_actions": [
            {
                "id": item.get("id"),
                "capability": item.get("capability"),
                "status": item.get("status"),
                "result": {
                    "summary": _redact((item.get("result") or {}).get("summary")),
                    "artifact_ids": (
                        (item.get("result") or {}).get("artifact_ids") or []
                    ),
                    "error": (item.get("result") or {}).get("error"),
                },
            }
            for item in actions[-6:]
        ],
        "challenge": {
            "status": (snapshot.get("challenge") or {}).get("status"),
            "status_reason": _redact(
                (snapshot.get("challenge") or {}).get("status_reason")
            ),
            "completion_proof_artifact_id": (
                (snapshot.get("challenge") or {}).get(
                    "completion_proof_artifact_id"
                )
            ),
        },
    }


def _redact(value: object) -> str:
    return re.sub(
        r"(?i)((?:authorization|cookie|set-cookie|token|secret|api[_-]?key|password)"
        r"\s*[:=]\s*)([^\s;,]+)",
        r"\1[REDACTED]",
        str(value or ""),
    )[:800]


__all__ = [
    "DeterministicObserver",
    "ObserverCoordinator",
    "ObserverSuggestion",
    "build_observer_context",
    "native_observer_triggers",
]
