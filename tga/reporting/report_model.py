"""Report model helpers."""

from __future__ import annotations

from typing import Any


def tools_used(snapshot: dict[str, Any]) -> list[str]:
    tools = {a.get("tool") for a in snapshot.get("artifacts", []) if a.get("tool")}
    return sorted(tools)


def findings_by_status(snapshot: dict[str, Any], status: str) -> list[dict[str, Any]]:
    return [f for f in snapshot.get("findings", []) if f.get("status") == status]


def events_by_type(snapshot: dict[str, Any], *types: str) -> list[dict[str, Any]]:
    wanted = set(types)
    return [event for event in snapshot.get("events", []) if event.get("type") in wanted]

