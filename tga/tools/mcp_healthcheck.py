"""Healthcheck helpers for MVP tools."""

from __future__ import annotations

import shutil

from tga.tools.mcp_catalog import all_tools


def local_tool_healthcheck() -> list[dict]:
    rows = []
    for tool in all_tools():
        path = shutil.which(tool)
        rows.append({"tool": tool, "status": "ok" if path else "missing", "detail": path or ""})
    return rows

