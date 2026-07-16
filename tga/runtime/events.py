"""Append-only v2 event facade."""

from __future__ import annotations

from typing import Any

from tga.contracts import AgentEvent
from tga.evidence.store import EvidenceStore


class EventStore:
    def __init__(self, store: EvidenceStore):
        self.store = store

    def append(self, task_id: str, event_type: str, payload: dict[str, Any], *, solver_id: str | None = None) -> AgentEvent:
        return self.store.append_agent_event(task_id=task_id, type=event_type, payload=payload, solver_id=solver_id)

    def list(self, task_id: str, *, after_seq: int = 0, limit: int = 200) -> list[AgentEvent]:
        return self.store.list_agent_events(task_id, after_seq=after_seq, limit=limit)
