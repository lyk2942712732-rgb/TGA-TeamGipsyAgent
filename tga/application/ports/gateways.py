"""External-service ports used by application/runtime code."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, Sequence


class ModelGateway(Protocol):
    model: str

    def chat_tools(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        tools: Sequence[dict[str, Any]],
        temperature: float,
    ) -> dict[str, Any]: ...


class SchedulerPort(Protocol):
    def schedule(self, task_id: str) -> bool: ...


class WorkspacePort(Protocol):
    def task_workspace(self, task_id: str) -> Path: ...

