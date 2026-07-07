"""Report model helpers."""

from __future__ import annotations

from typing import Any


def tools_used(snapshot: dict[str, Any]) -> list[str]:
    tools = {a.get("tool") for a in snapshot.get("artifacts", []) if a.get("tool")}
    return sorted(tools)

