"""Subprocess-based worker for Week 1 demos."""

from __future__ import annotations

import subprocess
import json
from pathlib import Path

from tga.contracts import Intent, TGATask, WorkerResult
from tga.agent.http_action_planner import plan_http_actions
from tga.ctf.llm_http_agent import execute_http_actions
from tga.ctf.flag_hunter import WebFlagHunter
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
        if intent.kind == "exploit_ctf" and task.target.startswith(("http://", "https://")):
            actions, plan_meta = plan_http_actions(task=task, instruction=intent.goal, snapshot={})
            if actions:
                return execute_http_actions(
                    task=task,
                    intent=intent,
                    artifact_store=self.artifact_store,
                    actions=actions,
                    plan_meta=plan_meta,
                    timeout_s=min(self.timeout_s, 12),
                )
            return WebFlagHunter(self.artifact_store, timeout_s=min(self.timeout_s, 12), max_requests=28).run(
                task=task,
                intent=intent,
                workspace=workspace,
            )
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
        facts = []
        leads = []
        flags = []
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
            parsed = _parse_tool_artifact(self.artifact_store.read_text(artifact.id), task_id=task.id)
            facts.extend(parsed.facts)
            leads.extend(parsed.leads)
            flags.extend(parsed.flags)
            errors.extend(parsed.errors)
        return WorkerResult(
            task_id=task.id,
            intent_id=intent.id,
            status="failed" if errors else "ok",
            artifacts=artifacts,
            facts=facts,
            leads=leads,
            flags=flags,
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


def _parse_tool_artifact(text: str, *, task_id: str):
    parsed = parse_markers(text, task_id=task_id)
    facts = list(parsed.facts)
    leads = list(parsed.leads) + list(parsed.deadends)
    flags = list(parsed.flags)
    errors = list(parsed.errors)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = {}
    if isinstance(payload, dict):
        status = payload.get("status")
        tool = payload.get("tool")
        target = payload.get("target")
        if status:
            facts.append(f"{tool or 'tool'} on {target or 'target'} -> {status}")
        output = "\n".join(str(payload.get(key) or "") for key in ("stdout", "stderr"))
        flags.extend(parse_markers(output, task_id=task_id).flags)
        if payload.get("error"):
            errors.append(str(payload["error"]))
    return WorkerResult(
        task_id="",
        intent_id="",
        status="ok",
        facts=_unique(facts),
        leads=_unique(leads),
        flags=_unique(flags),
        errors=_unique(errors),
    )


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    seen = set()
    for value in values:
        if value and value not in seen:
            result.append(value)
            seen.add(value)
    return result
