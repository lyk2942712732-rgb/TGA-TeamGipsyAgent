"""Report model helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def tools_used(snapshot: dict[str, Any]) -> list[str]:
    tools = {a.get("tool") for a in snapshot.get("artifacts", []) if a.get("tool")}
    return sorted(tools)


def findings_by_status(snapshot: dict[str, Any], status: str) -> list[dict[str, Any]]:
    return [f for f in snapshot.get("findings", []) if f.get("status") == status]


def events_by_type(snapshot: dict[str, Any], *types: str) -> list[dict[str, Any]]:
    wanted = set(types)
    return [event for event in runtime_events(snapshot) if event.get("type") in wanted]


def runtime_events(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Use the v2 session's authoritative per-task sequence."""
    events = snapshot.get("events") if snapshot.get("schema_version") == 2 else snapshot.get("agent_events")
    return sorted(events or [], key=lambda item: (int(item.get("seq") or 0), str(item.get("created_at") or "")))


def runtime_actions(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    return sorted(snapshot.get("actions") or [], key=lambda item: (str(item.get("created_at") or ""), str(item.get("id") or "")))


def read_artifact_payload(snapshot: dict[str, Any], artifact: dict[str, Any]) -> dict[str, Any] | None:
    path = artifact.get("path")
    if not path:
        return None
    task = snapshot.get("task") or {}
    task_id = task.get("id")
    if not task_id:
        return None
    candidates = []
    raw_path = Path(path)
    if raw_path.is_absolute():
        candidates.append(raw_path)
    else:
        artifact_base_path = snapshot.get("_artifact_base_path")
        if artifact_base_path:
            candidates.append(Path(str(artifact_base_path)) / raw_path)
        candidates.extend([
            Path("runs") / task_id / "artifacts" / raw_path,
            Path("runs") / task_id / raw_path,
        ])
    for candidate in candidates:
        try:
            if candidate.is_file():
                text = candidate.read_text(encoding="utf-8", errors="replace")
                return json.loads(text)
        except (OSError, json.JSONDecodeError):
            continue
    return None

