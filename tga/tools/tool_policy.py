"""Tool authorization policy."""

from __future__ import annotations

from tga.contracts import TGATask
from tga.core.scope import is_in_scope
from tga.tools.mcp_catalog import ACTIVE_TOOLS


def is_allowed(*, task: TGATask, tool: str, target: str) -> tuple[bool, str]:
    if not is_in_scope(target, task.scope):
        return False, "OUT_OF_SCOPE"
    if tool in ACTIVE_TOOLS:
        if task.intensity == "passive":
            return False, "ACTIVE_SCAN_NOT_ALLOWED"
        if not task.allow_active_scan:
            return False, "ACTIVE_SCAN_NOT_ALLOWED"
    return True, ""

