"""Capabilities HTTP boundaries."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from tga.capabilities.mcp import health_snapshot, tool_catalog_snapshot
from tga.capabilities.registry import build_default_registry

from apps.api.routes.support import _catalog_runner

router = APIRouter(tags=["capabilities"])


@router.get("/capabilities")
def capabilities() -> dict[str, Any]:
    """Expose B's registry verbatim, plus its catalogued MCP methods."""
    snapshot = build_default_registry().snapshot()
    snapshot["tools"] = tool_catalog_snapshot(_catalog_runner())
    return snapshot


@router.get("/tools/health")
def tool_health() -> dict[str, Any]:
    runner = _catalog_runner()
    snapshot = health_snapshot(runner)
    snapshot["configured"] = runner is not None
    return snapshot
