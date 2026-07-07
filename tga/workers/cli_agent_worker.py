"""Placeholder for CLI-agent workers such as Claude/Codex."""

from __future__ import annotations

from tga.contracts import Intent, TGATask, WorkerResult


class CliAgentWorker:
    def run(self, *, task: TGATask, intent: Intent, workspace: str) -> WorkerResult:
        return WorkerResult(
            task_id=task.id,
            intent_id=intent.id,
            status="blocked",
            errors=["CLI_AGENT_WORKER_NOT_CONFIGURED"],
        )

