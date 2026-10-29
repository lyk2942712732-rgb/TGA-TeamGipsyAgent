"""Read-only task activity events."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import UUID

from .domain import (
    SYSTEM_ACTOR,
    Actor,
    EventKind,
    TaskEvent,
)
from .storage import Storage

EventHook = Callable[[UUID, int], Awaitable[None]]


class TaskEvents:
    def __init__(self, storage: Storage, on_change: EventHook | None = None) -> None:
        self.storage = storage
        self.on_change = on_change

    async def emit(
        self,
        task_id: UUID,
        *,
        actor: Actor,
        kind: EventKind,
        text: str,
        channel_agent_id: str = "system",
        payload: dict | None = None,
    ) -> TaskEvent:
        message = await self.storage.append_event(
            task_id,
            actor,
            kind,
            text,
            channel_agent_id=channel_agent_id,
            payload=payload,
        )
        if self.on_change:
            await self.on_change(task_id, message.seq)
        return message

    async def announce_status(self, task_id: UUID, agent: Actor, state: str, detail: str = "") -> TaskEvent:
        text = f"{agent.display_name} 状态变更为 {state}" + (f"：{detail}" if detail else "")
        return await self.emit(
            task_id,
            actor=SYSTEM_ACTOR,
            kind=EventKind.AGENT_STATUS,
            text=text,
            channel_agent_id=agent.agent_id,
            payload={"agent_id": agent.agent_id, "state": state, "detail": detail},
        )

    async def history(self, task_id: UUID, *, after_seq: int = 0, limit: int = 500) -> list[TaskEvent]:
        return await self.storage.list_events(task_id, after_seq=after_seq, limit=limit)


__all__ = ["TaskEvents"]
