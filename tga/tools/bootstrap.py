"""ToolRunner bootstrap helpers."""

from __future__ import annotations

import os
from pathlib import Path

from tga.evidence.artifacts import ArtifactStore
from tga.tools.mcp_catalog import discover_mcp_security_hub
from tga.tools.tool_runner import ToolRunner


def build_tool_runner_from_env(artifact_store: ArtifactStore) -> ToolRunner | None:
    """Create a ToolRunner when mcp-security-hub is available.

    Operators can set TGA_MCP_SECURITY_HUB_ROOT to an existing local checkout.
    If unset, we also try a repo-local ./mcp-security-hub directory.
    """
    candidates: list[Path] = []
    if os.environ.get("TGA_MCP_SECURITY_HUB_ROOT"):
        candidates.append(Path(os.environ["TGA_MCP_SECURITY_HUB_ROOT"]))
    candidates.append(Path.cwd() / "mcp-security-hub")

    for root in candidates:
        if not root.exists():
            continue
        try:
            catalog = discover_mcp_security_hub(root)
        except Exception:
            continue
        return ToolRunner(catalog=catalog, artifact_store=artifact_store)
    return None
