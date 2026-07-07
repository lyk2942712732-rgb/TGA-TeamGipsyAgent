"""Worker protocol."""

from __future__ import annotations

from typing import Protocol

from tga.contracts import Intent, TGATask, WorkerResult


class Worker(Protocol):
    def run(self, *, task: TGATask, intent: Intent, workspace: str) -> WorkerResult:
        ...

