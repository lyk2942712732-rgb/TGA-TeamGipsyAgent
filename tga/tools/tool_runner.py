"""Tool runner skeleton."""

from __future__ import annotations

from tga.contracts import ArtifactRecord, Intent, TGATask
from tga.evidence.artifacts import ArtifactStore
from tga.tools.tool_policy import is_allowed


class ToolRunner:
    def __init__(self, artifact_store: ArtifactStore):
        self.artifact_store = artifact_store

    def record_tool_error(self, *, task: TGATask, intent: Intent, tool: str, target: str, reason: str) -> ArtifactRecord:
        return self.artifact_store.save_text(
            task_id=task.id,
            intent_id=intent.id,
            kind="tool_output",
            text=f"TOOL_ERROR={tool}|{reason}\n",
            tool=tool,
            target=target,
        )

    def run_tool(self, *, task: TGATask, intent: Intent, tool: str, target: str, args: dict) -> ArtifactRecord:
        allowed, reason = is_allowed(task=task, tool=tool, target=target)
        if not allowed:
            return self.record_tool_error(task=task, intent=intent, tool=tool, target=target, reason=reason)
        return self.record_tool_error(
            task=task,
            intent=intent,
            tool=tool,
            target=target,
            reason="MCP_TOOL_RUNNER_NOT_CONFIGURED",
        )

