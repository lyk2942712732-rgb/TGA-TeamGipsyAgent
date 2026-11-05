"""Identical native Hook protocol for Codex and Claude; no model tool call required."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx

from .action_hook import record_tool, result_context

_publication_versions = {}


def publications_changed():
    workspace = Path(os.environ.get('TGA3_WORKSPACE', '/workspace'))
    root = Path(os.environ.get('TGA3_FINDINGS', '/findings'))
    paths = [*root.glob('*.json')]
    final = workspace / 'final-candidate.json'
    if final.is_file():
        paths.append(final)
    version = tuple(sorted((str(p), p.stat().st_mtime_ns, p.stat().st_ctime_ns, p.stat().st_size) for p in paths))
    key = (str(workspace), str(root))
    changed = bool(version) and _publication_versions.get(key) != version
    _publication_versions[key] = version
    return changed


def checkpoint(event: dict, *, client=None) -> dict:
    if os.environ.get("TGA3_AGENT_ROLE") not in {"scout", "worker"}:
        return {}
    event_name = event.get("hook_event_name", "PreToolUse")
    post_tool = event_name in {"PostToolUse", "PostToolUseFailure"}
    record = record_tool(event) if post_tool else None
    if post_tool and not publications_changed():
        return ({"hookSpecificOutput": {"hookEventName": event_name, "additionalContext": result_context(record)}}
                if record else {})
    state_dir = Path(os.environ.get("TGA3_WORKSPACE", "/workspace")) / ".tga3"
    state_dir.mkdir(parents=True, exist_ok=True)
    ack_file = state_dir / "blackboard-ack.json"
    generation = event.get("_tga_generation", os.environ.get("TGA3_CLI_GENERATION", ""))
    previous = json.loads(ack_file.read_text()) if ack_file.is_file() else {}
    arguments = event.get("tool_input", {})
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except ValueError:
            arguments = {}

    def candidate(value):
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                return False
        if not isinstance(value, dict):
            return False
        return value.get("kind") == "final_candidate" or any(
            candidate(value.get(key)) for key in ("request", "arguments", "input")
        )

    final = (str(event.get("tool_name", "")).split("__")[-1] == "blackboard_publish"
             or candidate(arguments))
    start_token = os.environ.get("TGA3_BOARD_START_TOKEN", "")
    start_file = state_dir / "aoe-start-token"
    force_start = bool(start_token and event_name in {"UserPromptSubmit", "PreToolUse"} and
                       (not start_file.is_file() or start_file.read_text() != start_token))
    base = os.environ["TGA3_BLACKBOARD_MCP_URL"].rstrip("/").removesuffix("/mcp")
    response = (client.post if client is not None else httpx.post)(
        f"{base}/internal/blackboard/{os.environ['TGA3_TASK_ID']}/{os.environ['TGA3_AGENT_ID']}/checkpoint",
        json={
            "ack": previous.get("delivery_id", "") if not post_tool and previous.get("generation") == generation else "",
            "final": final or force_start,
            "ack_only": bool(event.get("ack_only")),
            "initial_only": event.get("hook_event_name") == "UserPromptSubmit" and not force_start,
            "file_sync": os.environ.get("TGA3_AGENT_ROLE") == "worker",
            "action_start": (os.environ.get("TGA3_ATTEMPT_ID", "")
                             if event_name == "PreToolUse" and event.get("tool_use_id") else ""),
            "force_start": force_start,
        },
        timeout=10,
    )
    response.raise_for_status()
    delivery = response.json()
    if force_start:
        start_file.write_text(start_token)
    context = delivery.get("context", "")
    if event_name == "PreToolUse" and not ((final or force_start) and context):
        record_tool(event)
    if context:
        ack_file.write_text(json.dumps({"generation": generation, "delivery_id": delivery["delivery_id"]}))
    parts = [result_context(record)] if record else []
    if context:
        parts.append(context)
    if receipts := delivery.get("publication_receipts"):
        parts.append("TGA system file-import receipts: " + json.dumps(receipts, ensure_ascii=False))
    if not parts:
        return {}
    result = {
        "hookSpecificOutput": {
            "hookEventName": event.get("hook_event_name", "PreToolUse"),
            "additionalContext": "\n\n".join(parts),
        }
    }
    if context and (final or force_start and event_name == "PreToolUse"):
        result["hookSpecificOutput"].update(
            permissionDecision="deny",
            permissionDecisionReason=(
                "New blackboard updates were delivered. Review them, then resubmit the candidate."
                if final else "Question blackboard updates were delivered. Review them, then retry."
            ),
        )
    return result


def main() -> None:
    try:
        print(json.dumps(checkpoint(json.load(sys.stdin)), ensure_ascii=False))
    except Exception as exc:
        # Do not silently allow a final candidate past a failed freshness check.
        print(f"Blackboard checkpoint failed: {type(exc).__name__}; retry this tool after recovery.", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
