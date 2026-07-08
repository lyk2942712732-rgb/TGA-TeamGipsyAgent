"""Subprocess-based worker for Week 1 demos."""

from __future__ import annotations

import subprocess
from pathlib import Path

from tga.contracts import Intent, TGATask, WorkerResult
from tga.evidence.artifacts import ArtifactStore
from tga.tools.tool_runner import ToolRunner
from tga.workers.output_parser import parse_markers


class SubprocessWorker:
    def __init__(
        self,
        artifact_store: ArtifactStore,
        timeout_s: int = 120,
        tool_runner: ToolRunner | None = None,
    ):
        self.artifact_store = artifact_store
        self.timeout_s = timeout_s
        self.tool_runner = tool_runner

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
        if self.tool_runner is None and intent.required_tools:
            artifact = self.artifact_store.save_text(
                task_id=task.id,
                intent_id=intent.id,
                kind="tool_output",
                text=(
                    "TOOL_RUNNER_UNAVAILABLE="
                    f"{','.join(intent.required_tools)}\n"
                    "Set TGA_MCP_SECURITY_HUB_ROOT and build the required MCP Docker images.\n"
                ),
                tool="tga",
                target=task.target,
            )
            return WorkerResult(
                task_id=task.id,
                intent_id=intent.id,
                status="blocked",
                artifacts=[artifact],
                errors=["TOOL_RUNNER_UNAVAILABLE"],
            )
        if self.tool_runner is not None and intent.required_tools:
            return self._run_required_tools(task=task, intent=intent, workspace=workspace)
        return self.run_command(
            task=task,
            intent=intent,
            workspace=workspace,
            command=["python", "-c", "print('DEADEND=no command configured for this intent')"],
        )

    def _run_required_tools(self, *, task: TGATask, intent: Intent, workspace: str) -> WorkerResult:
        Path(workspace).mkdir(parents=True, exist_ok=True)
        artifacts = []
        errors = []
        for tool in intent.required_tools:
            try:
                artifact = self.tool_runner.run_tool(
                    task=task,
                    intent=intent,
                    tool=tool,
                    target=task.target,
                    args=_default_tool_args(tool=tool, task=task),
                )
            except Exception as exc:  # noqa: BLE001
                artifact = self.artifact_store.save_text(
                    task_id=task.id,
                    intent_id=intent.id,
                    kind="tool_output",
                    text=f"TOOL_ERROR={tool}|{exc}\n",
                    tool=tool,
                    target=task.target,
                )
                errors.append(f"{tool}: {exc}")
            artifacts.append(artifact)
        return WorkerResult(
            task_id=task.id,
            intent_id=intent.id,
            status="failed" if errors else "ok",
            artifacts=artifacts,
            errors=errors,
        )


def _default_tool_args(*, tool: str, task: TGATask) -> dict:
    args: dict = {"timeout_seconds": 120}
    if task.target.startswith(("http://", "https://")):
        args["url"] = task.target
        args["target"] = task.target
    else:
        args["target"] = task.target
        args["path"] = task.target
        args["directory"] = task.target
    return args
