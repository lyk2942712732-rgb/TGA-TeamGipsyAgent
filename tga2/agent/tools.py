"""Expose real capabilities as LangChain tools through one governance gateway."""

from __future__ import annotations

import json
from collections.abc import Sequence

from langchain_core.tools import BaseTool, StructuredTool

from tga2.core.models import AgentEvent
from tga2.core.store import TaskStore
from tga2.core.workspace import TaskWorkspace
from tga2.skills import SkillRepository


class ToolRegistry:
    def __init__(
        self,
        *,
        store: TaskStore,
        workspace: TaskWorkspace,
        task_id: str,
        intent_id: str,
        model_read_max_bytes: int,
        skills: SkillRepository | None = None,
        external_tools: Sequence[BaseTool] = (),
    ) -> None:
        self.store = store
        self.workspace = workspace
        self.task_id = task_id
        self.intent_id = intent_id
        self.model_read_max_bytes = model_read_max_bytes
        self.skills = skills
        self.external_tools = list(external_tools)

    def tools(self) -> list[BaseTool]:
        builtins = [
            StructuredTool.from_function(
                func=self._list_inputs,
                name="list_inputs",
                description="List input files explicitly supplied to this authorized task.",
            ),
            StructuredTool.from_function(
                func=self._read_input,
                name="read_input",
                description="Read a task input file and publish the exact output as an immutable artifact.",
            ),
            StructuredTool.from_function(
                func=self._save_note,
                name="save_note",
                description="Publish a textual analysis note as an immutable artifact.",
            ),
        ]
        if self.skills is not None:
            builtins.append(
                StructuredTool.from_function(
                    func=self._read_skill,
                    name="read_skill",
                    description=(
                        "Read SKILL.md or another Markdown document from one shared "
                        "Skill package. Use LangChain glob_search/grep_search to discover "
                        "packages first, then load only what the current intent needs."
                    ),
                )
            )
        return [*builtins, *self.external_tools]

    def _list_inputs(self) -> list[str]:
        """List available task input paths."""
        return self.workspace.list_inputs()

    def _read_skill(self, name: str, path: str = "SKILL.md") -> str:
        """Read one Markdown document from an installed Skill package."""
        assert self.skills is not None
        normalized_path = path.replace("\\", "/").strip("/")
        content = self.skills.read_document(name, normalized_path)
        package = self.skills.get_required(name)
        document = next(
            item for item in package.documents if item.path == normalized_path
        )
        self.store.append_event(
            AgentEvent(
                task_id=self.task_id,
                type="SKILL_DOCUMENT_READ",
                solver_id="worker",
                intent_id=self.intent_id,
                payload={
                    "skill_name": name,
                    "path": normalized_path,
                    "sha256": document.sha256,
                    "bytes": document.size,
                },
            )
        )
        return json.dumps(
            {
                "skill": name,
                "path": normalized_path,
                "sha256": document.sha256,
                "content": content,
                "notice": "Knowledge only; task authorization and tool policy still apply.",
            },
            ensure_ascii=False,
        )

    def _read_input(self, path: str) -> str:
        """Read one UTF-8-compatible task input path."""

        def handler() -> str:
            source = self.workspace.resolve_input(path)
            raw = source.read_bytes()
            if len(raw) > self.model_read_max_bytes:
                raise ValueError(
                    "input exceeds the configured model-reading limit "
                    f"({self.model_read_max_bytes} bytes)"
                )
            content = raw.decode("utf-8", errors="replace")
            artifact, _ = self.workspace.publish_text(
                task_id=self.task_id,
                content=content,
                kind="input_read",
                tool_name="read_input",
                intent_id=self.intent_id,
            )
            self.store.save_artifact(artifact)
            self.store.append_event(
                AgentEvent(
                    task_id=self.task_id,
                    type="ARTIFACT_CREATED",
                    solver_id="worker",
                    intent_id=self.intent_id,
                    payload={
                        "artifact_id": artifact.id,
                        "kind": artifact.kind,
                        "sha256": artifact.sha256,
                    },
                )
            )
            return json.dumps(
                {
                    "artifact_id": artifact.id,
                    "path": path,
                    "content": content[:100_000],
                    "truncated": len(content) > 100_000,
                },
                ensure_ascii=False,
            )

        return handler()

    def _save_note(self, content: str) -> str:
        """Save a worker-authored analysis note."""

        def handler() -> str:
            artifact, _ = self.workspace.publish_text(
                task_id=self.task_id,
                content=content,
                kind="analysis_note",
                tool_name="save_note",
                intent_id=self.intent_id,
            )
            self.store.save_artifact(artifact)
            self.store.append_event(
                AgentEvent(
                    task_id=self.task_id,
                    type="ARTIFACT_CREATED",
                    solver_id="worker",
                    intent_id=self.intent_id,
                    payload={
                        "artifact_id": artifact.id,
                        "kind": artifact.kind,
                        "sha256": artifact.sha256,
                        "source_tool": "save_note",
                    },
                )
            )
            return json.dumps({"artifact_id": artifact.id, "sha256": artifact.sha256})

        return handler()


__all__ = ["ToolRegistry"]
