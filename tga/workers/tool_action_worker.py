"""Worker for LLM-planned MCP/security-tool actions."""

from __future__ import annotations

import json
import re

from tga.agent.http_action_planner import ToolAction
from tga.contracts import Intent, TGATask, WorkerResult
from tga.evidence.artifacts import ArtifactStore
from tga.tools.tool_runner import ToolRunner
from tga.workers.output_parser import parse_markers


class ToolActionWorker:
    def __init__(
        self,
        *,
        artifact_store: ArtifactStore,
        tool_runner: ToolRunner | None,
        tool_actions: list[ToolAction],
    ):
        self.artifact_store = artifact_store
        self.tool_runner = tool_runner
        self.tool_actions = tool_actions

    def run(self, *, task: TGATask, intent: Intent, workspace: str) -> WorkerResult:
        if self.tool_runner is None:
            artifact = self.artifact_store.save_text(
                task_id=task.id,
                intent_id=intent.id,
                kind="tool_output",
                text=json.dumps(
                    {
                        "status": "failed",
                        "error": {
                            "code": "TOOL_RUNNER_UNAVAILABLE",
                            "message": "Set TGA_MCP_SECURITY_HUB_ROOT and build the requested MCP Docker images.",
                        },
                        "tool_actions": [action.model_dump() for action in self.tool_actions],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                tool="tga-tool-actions",
                target=task.target,
                suffix=".json",
            )
            return WorkerResult(
                task_id=task.id,
                intent_id=intent.id,
                status="blocked",
                artifacts=[artifact],
                errors=["TOOL_RUNNER_UNAVAILABLE"],
            )

        artifacts = []
        facts = []
        leads = []
        flags = []
        errors = []
        for action in self.tool_actions:
            try:
                artifact = self.tool_runner.run_tool(
                    task=task,
                    intent=intent,
                    tool=action.tool,
                    target=action.target or task.target,
                    args=action.args,
                )
            except Exception as exc:  # noqa: BLE001
                artifact = self.artifact_store.save_text(
                    task_id=task.id,
                    intent_id=intent.id,
                    kind="tool_output",
                    text=f"TOOL_ERROR={action.tool}|{exc}\n",
                    tool=action.tool,
                    target=action.target or task.target,
                )
                errors.append(f"{action.tool}: {exc}")
            artifacts.append(artifact)
            parsed = _parse_tool_output(
                self.artifact_store.read_text(artifact.id),
                task_id=task.id,
                flag_format=task.flag_format,
            )
            facts.extend(parsed.facts)
            leads.extend(parsed.leads)
            flags.extend(parsed.flags)
            errors.extend(parsed.errors)

        return WorkerResult(
            task_id=task.id,
            intent_id=intent.id,
            status="failed" if errors else "ok",
            artifacts=artifacts,
            facts=_unique(facts),
            leads=_unique(leads),
            flags=_unique(flags),
            errors=_unique(errors),
        )


def _parse_tool_output(text: str, *, task_id: str, flag_format: str | None = None) -> WorkerResult:
    parsed = parse_markers(text, task_id=task_id)
    facts = list(parsed.facts)
    leads = list(parsed.leads) + list(parsed.deadends)
    flags = list(parsed.flags) + _extract_flags(text, flag_format)
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
        flags.extend(_extract_flags(output, flag_format))
        if payload.get("error"):
            errors.append(str(payload["error"]))
    return WorkerResult(task_id="", intent_id="", status="ok", facts=facts, leads=leads, flags=flags, errors=errors)


def _extract_flags(text: str, flag_format: str | None) -> list[str]:
    pattern_texts = [flag_format or r"flag\{[^}]+\}"]
    if not flag_format or flag_format in {r"flag\{[^}]+\}", r"FLAG\{[^}]+\}"}:
        pattern_texts.append(r"[A-Za-z0-9_]{2,32}\{[^{}\s]{4,200}\}")
    result: list[str] = []
    seen = set()
    for pattern_text in pattern_texts:
        try:
            pattern = re.compile(pattern_text)
        except re.error:
            pattern = re.compile(r"flag\{[^}]+\}")
        for match in pattern.finditer(text):
            value = match.group(0)
            if value not in seen:
                result.append(value)
                seen.add(value)
    return result


def _unique(values: list[str]) -> list[str]:
    result = []
    seen = set()
    for value in values:
        if value and value not in seen:
            result.append(value)
            seen.add(value)
    return result
