"""Process-local event fan-out backed by the authoritative SQLite log."""

from __future__ import annotations

import asyncio
import threading
import time
from collections import defaultdict, deque

from tga.domain.events import AgentEvent


class InProcessEventBus:
    """Wake subscribers without becoming an event store.

    A small bounded buffer avoids a read when the subscriber is current.  Any
    gap is repaired by the SQLite cursor query in the application backend.
    """

    def __init__(self, *, buffer_size: int = 512) -> None:
        self._condition = threading.Condition()
        self._events: dict[str, deque[AgentEvent]] = defaultdict(
            lambda: deque(maxlen=buffer_size)
        )

    def publish(self, event: AgentEvent) -> None:
        with self._condition:
            self._events[event.task_id].append(event.model_copy(deep=True))
            self._condition.notify_all()

    def events_after(self, task_id: str, after_seq: int) -> list[AgentEvent]:
        with self._condition:
            return [
                item.model_copy(deep=True)
                for item in self._events.get(task_id, ())
                if item.seq > after_seq
            ]

    async def wait(
        self, task_id: str, *, after_seq: int, timeout: float = 15.0
    ) -> list[AgentEvent]:
        return await asyncio.to_thread(
            self._wait_sync, task_id, after_seq, max(0.01, timeout)
        )

    def _wait_sync(
        self, task_id: str, after_seq: int, timeout: float
    ) -> list[AgentEvent]:
        deadline = time.monotonic() + timeout
        with self._condition:
            while True:
                available = [
                    item.model_copy(deep=True)
                    for item in self._events.get(task_id, ())
                    if item.seq > after_seq
                ]
                if available:
                    return available
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return []
                self._condition.wait(remaining)


runtime_event_bus = InProcessEventBus()


__all__ = ["InProcessEventBus", "runtime_event_bus"]
