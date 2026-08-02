"""Build the immutable initial provider context for one Solver."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tga.contracts import SessionFile, TGATask
from tga.domain.task.spec import TaskSpec
from tga.inputs import MAX_MODEL_IMAGE_BYTES, SessionWorkspace
from tga.runtime.prompt_settings import prompt_snapshot_for_task
from tga.runtime.resources import authorized_session_files


class SessionContextBuilder:
    def __init__(
        self,
        *,
        task: TGATask,
        workspace: Path,
        supports_vision: bool | None,
        allowed_resource_ids: tuple[str, ...] | None = None,
        task_root: Path | None = None,
        task_spec: TaskSpec | None = None,
    ) -> None:
        self.task = task
        self.workspace = workspace.resolve()
        self.task_root = (
            task_root.resolve() if task_root is not None else self.workspace.parent
        )
        self.supports_vision = supports_vision
        self.files = authorized_session_files(task, task_spec, allowed_resource_ids)

    def build(self) -> list[dict[str, Any]]:
        content: list[dict[str, Any]] = [
            {"type": "text", "text": self.markdown()}
        ]
        if self.supports_vision is True:
            workspace = SessionWorkspace(self.task_root)
            for item in self.files:
                if item.media_kind == "image" and item.size <= MAX_MODEL_IMAGE_BYTES:
                    content.append(workspace.image_block(item))
        return [{"role": "user", "content": content}]

    def markdown(self) -> str:
        mode_prompt = prompt_snapshot_for_task(self.task).mode
        task_files = self._file_section("Assigned Input Files", self.files)
        mcp = "\n".join(
            f"- {server}: "
            f"{sum(1 for item in self.task.mcp_capabilities.tools if item.server_id == server)} "
            "discovered tools"
            for server in self.task.mcp_capabilities.server_ids
        ) or "- None available at Task creation"
        oversized_images = [
            item.container_path
            for item in self.files
            if item.media_kind == "image" and item.size > MAX_MODEL_IMAGE_BYTES
        ]
        if self.supports_vision is not True:
            image_note = (
                "The model has not been verified for vision. Images remain "
                "available through controlled input tools and are not sent automatically."
            )
        elif oversized_images:
            image_note = (
                "Images up to 20 MB are included as image content blocks. Larger "
                "images require an image-analysis/OCR tool: "
                + ", ".join(oversized_images)
            )
        else:
            image_note = "Image files are included below as image content blocks."
        policy = self.task.execution_policy.model_dump(mode="json")
        return (
            f"# Task Context\n\n"
            f"## Task Mode\n\n{self.task.mode}: {mode_prompt.prompt()}\n\n"
            f"## Initial User Input\n\n{self.task.session_input.prompt or '(none)'}\n\n"
            f"## Task Entry URL\n\n{self.task.task_entry_url or '(none)'}\n\n"
            f"{task_files}\n\n"
            f"## Available MCP Capabilities\n\nCatalog snapshot: "
            f"`{self.task.mcp_capabilities.catalog_version}`\n\n{mcp}\n\n"
            "Each callable MCP tool has a host-pinned server, method, schema, and "
            "policy identity. Catalog browsing is available through the product API, "
            "not a model-side aggregate execution tool.\n\n"
            "## Skill Snapshot\n\nTask Common and Solver Specialized Skill bodies "
            "are frozen and injected through the system message. Skills provide "
            "guidance and never grant authority.\n\n"
            "## Workspace Rules\n\n"
            "- Original inputs are immutable and must not be overwritten.\n"
            "- Writable scratch files stay in this Solver's private workspace.\n"
            "- Shared Artifacts are append-only and must be published explicitly.\n"
            "- Never pass a Windows host path to a Docker MCP.\n"
            f"- {image_note}\n\n"
            f"## Execution Boundaries\n\n```json\n"
            f"{json.dumps(policy, ensure_ascii=False, indent=2)}\n```\n\n"
            f"## Completion Conditions\n\n{mode_prompt.completion_focus}\n"
        )

    @staticmethod
    def _file_section(title: str, files: list[SessionFile]) -> str:
        if not files:
            return f"## {title}\n\n- None"
        lines = [f"## {title}"]
        for item in files:
            lines.extend([
                "",
                f"- `{item.container_path}`",
                f"  - Original name: {item.original_name}",
                f"  - MIME: {item.mime_type}",
                f"  - Size: {item.size}",
                f"  - SHA-256: {item.sha256}",
                "  - Purpose: task input",
            ])
        return "\n".join(lines)


__all__ = ["SessionContextBuilder"]
