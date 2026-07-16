from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from tga.capabilities.models import (
    ArtifactInspectInput,
    CapabilityDescriptor,
    HTTPRequestInput,
    ToolInvokeInput,
    WorkspaceBinaryInput,
    WorkspacePythonInput,
)
from tga.tools.mcp_catalog import discover_mcp_security_hub


INPUT_MODELS = {
    "http.request": HTTPRequestInput,
    "tool.invoke": ToolInvokeInput,
    "workspace.python": WorkspacePythonInput,
    "workspace.binary": WorkspaceBinaryInput,
    "artifact.inspect": ArtifactInspectInput,
}


class CapabilityRegistry:
    def __init__(self, *, project_root: str | Path | None = None, hub_root: str | Path | None = None):
        self.project_root = Path(project_root).resolve() if project_root else default_project_root()
        self.hub_root = Path(hub_root).resolve() if hub_root else default_hub_root(self.project_root)

    def descriptors(self) -> list[CapabilityDescriptor]:
        tool_available, tool_reason = self._tool_invoke_availability()
        return [
            CapabilityDescriptor(
                name="http.request",
                input_schema=HTTPRequestInput.model_json_schema(),
                risk="active",
                supported_modes=["ctf", "web_audit"],
                max_output_bytes=1048576,
                timeout_seconds=120,
                scope_validator="origin allowlist with redirect validation",
                budget_key="origin+method",
                redacted_summary="method/url/status/headers without secrets",
            ),
            CapabilityDescriptor(
                name="tool.invoke",
                input_schema=ToolInvokeInput.model_json_schema(),
                risk="active",
                supported_modes=["ctf", "web_audit", "code_audit", "binary_ctf"],
                max_output_bytes=1048576,
                timeout_seconds=900,
                scope_validator="TGA tool policy and MCP input schema",
                budget_key="tool+target",
                redacted_summary="tool/mcp_tool/status/artifact ids",
                available=tool_available,
                unavailable_reason=tool_reason,
            ),
            CapabilityDescriptor(
                name="workspace.python",
                input_schema=WorkspacePythonInput.model_json_schema(),
                risk="active",
                supported_modes=["ctf", "code_audit", "binary_ctf"],
                max_output_bytes=1048576,
                timeout_seconds=120,
                scope_validator="solver workspace only, sanitized env, no shell",
                budget_key="task+solver+python",
                redacted_summary="exit code/stdout tail/stderr tail",
            ),
            CapabilityDescriptor(
                name="workspace.binary",
                input_schema=WorkspaceBinaryInput.model_json_schema(),
                risk="passive",
                supported_modes=["ctf", "binary_ctf", "code_audit"],
                max_output_bytes=1048576,
                timeout_seconds=120,
                scope_validator="solver workspace or local scoped attachment roots",
                budget_key="task+solver+binary",
                redacted_summary="file metadata/strings/hexdump excerpt",
            ),
            CapabilityDescriptor(
                name="artifact.inspect",
                input_schema=ArtifactInspectInput.model_json_schema(),
                risk="passive",
                supported_modes=["ctf", "web_audit", "code_audit", "binary_ctf"],
                max_output_bytes=1048576,
                timeout_seconds=30,
                scope_validator="current solver run artifacts only",
                budget_key="task+solver+artifact",
                redacted_summary="artifact path/range/keyword hit counts",
            ),
        ]

    def snapshot(self) -> dict[str, Any]:
        return {
            "project_root": str(self.project_root),
            "hub_root": str(self.hub_root),
            "capabilities": [descriptor.model_dump() for descriptor in self.descriptors()],
        }

    def _tool_invoke_availability(self) -> tuple[bool, str | None]:
        if not self.hub_root.exists():
            return False, f"mcp-security-hub checkout not found: {self.hub_root}"
        try:
            catalog = discover_mcp_security_hub(self.hub_root)
        except Exception as exc:
            return False, str(exc)
        if not catalog.servers:
            return False, "mcp-security-hub catalog is empty"
        return True, None


def default_project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_hub_root(project_root: Path | None = None) -> Path:
    override = os.environ.get("TGA_MCP_SECURITY_HUB_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    return (project_root or default_project_root()).joinpath("mcp-security-hub").resolve()
