"""ToolRunner bootstrap helpers."""

from __future__ import annotations

import os
from pathlib import Path

from tga.evidence.artifacts import ArtifactStore
from tga.tools.mcp_catalog import discover_mcp_security_hub
from tga.tools.tool_runner import ToolRunner


def mcp_security_hub_candidates() -> list[Path]:
    """Return explicit override first, then the checkout bundled with TGA.

    The repository carries ``mcp-security-hub`` as a project-relative
    dependency.  Do not infer a developer-specific Desktop path: that made
    the runtime depend on where one workstation happened to clone the hub.
    """
    project_root = Path(__file__).resolve().parents[2]
    raw_candidates = [
        Path(os.environ["TGA_MCP_SECURITY_HUB_ROOT"])
        if os.environ.get("TGA_MCP_SECURITY_HUB_ROOT")
        else None,
        project_root / "mcp-security-hub",
        Path.cwd() / "mcp-security-hub",
    ]
    candidates: list[Path] = []
    seen: set[Path] = set()
    for candidate in raw_candidates:
        if candidate is None:
            continue
        resolved = candidate.expanduser().resolve()
        if resolved not in seen:
            seen.add(resolved)
            candidates.append(resolved)
    return candidates


def discover_mcp_security_hub_root() -> Path | None:
    for root in mcp_security_hub_candidates():
        if root.exists():
            return root
    return None


def build_tool_runner_from_env(artifact_store: ArtifactStore) -> ToolRunner | None:
    """Create a ToolRunner when mcp-security-hub is available."""
    candidates = mcp_security_hub_candidates()

    for root in candidates:
        if not root.exists():
            continue
        try:
            catalog = discover_mcp_security_hub(root)
        except Exception:
            continue
        return ToolRunner(catalog=catalog, artifact_store=artifact_store)
    return None
