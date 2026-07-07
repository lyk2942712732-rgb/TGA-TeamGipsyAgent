"""Subprocess-based worker for Week 1 demos."""

from __future__ import annotations

import subprocess
from pathlib import Path

from tga.contracts import Intent, TGATask, WorkerResult
from tga.evidence.artifacts import ArtifactStore
from tga.workers.output_parser import parse_markers


class SubprocessWorker:
    def __init__(self, artifact_store: ArtifactStore, timeout_s: int = 120):
        self.artifact_store = artifact_store
        self.timeout_s = timeout_s

    def run_command(self, *, task: TGATask, intent: Intent, workspace: str, command: list[str]) -> WorkerResult:
        Path(workspace).mkdir(parents=True, exist_ok=True)
        try:
            proc = subprocess.run(
                command,
                cwd=workspace,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_s,
            )
        except subprocess.TimeoutExpired as exc:
            text = (exc.stdout or "") + "\n" + (exc.stderr or "")
            artifact = self.artifact_store.save_text(
                task_id=task.id,
                intent_id=intent.id,
                kind="stdout",
                text=text,
                tool="subprocess",
                target=task.target,
            )
            return WorkerResult(
                task_id=task.id,
                intent_id=intent.id,
                status="failed",
                artifacts=[artifact],
                errors=["TOOL_TIMEOUT"],
            )

        combined = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
        artifact = self.artifact_store.save_text(
            task_id=task.id,
            intent_id=intent.id,
            kind="stdout",
            text=combined,
            tool="subprocess",
            target=task.target,
        )
        parsed = parse_markers(combined, task_id=task.id)
        status = "ok" if proc.returncode == 0 else "failed"
        return WorkerResult(
            task_id=task.id,
            intent_id=intent.id,
            status=status,
            artifacts=[artifact],
            facts=parsed.facts,
            leads=parsed.leads + parsed.deadends,
            findings=parsed.findings,
            flags=parsed.flags,
            errors=parsed.errors,
        )

    def run(self, *, task: TGATask, intent: Intent, workspace: str) -> WorkerResult:
        return self.run_command(
            task=task,
            intent=intent,
            workspace=workspace,
            command=["python", "-c", "print('DEADEND=no command configured for this intent')"],
        )

