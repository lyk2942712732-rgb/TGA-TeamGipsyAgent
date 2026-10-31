"""Identical native Hook protocol for Codex and Claude; no model tool call required."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx


def checkpoint(event: dict) -> dict:
    if os.environ.get("TGA3_AGENT_ROLE") not in {"scout", "worker"}:
        return {}
    state_dir = Path(os.environ.get("TGA3_WORKSPACE", "/workspace")) / ".tga3"
    state_dir.mkdir(parents=True, exist_ok=True)
    ack_file = state_dir / "blackboard-ack.json"
    generation = os.environ.get("TGA3_CLI_GENERATION", "")
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

    final = candidate(arguments)
    base = os.environ["TGA3_BLACKBOARD_MCP_URL"].rstrip("/").removesuffix("/mcp")
    response = httpx.post(
        f"{base}/internal/blackboard/{os.environ['TGA3_TASK_ID']}/{os.environ['TGA3_AGENT_ID']}/checkpoint",
        json={
            "ack": previous.get("delivery_id", "") if previous.get("generation") == generation else "",
            "final": final,
            "ack_only": bool(event.get("ack_only")),
            "initial_only": event.get("hook_event_name") == "UserPromptSubmit",
        },
        timeout=10,
    )
    response.raise_for_status()
    delivery = response.json()
    if not delivery:
        return {}
    ack_file.write_text(json.dumps({"generation": generation, "delivery_id": delivery["delivery_id"]}))
    result = {
        "hookSpecificOutput": {
            "hookEventName": event.get("hook_event_name", "PreToolUse"),
            "additionalContext": delivery["context"],
        }
    }
    if final:
        result["hookSpecificOutput"].update(
            permissionDecision="deny",
            permissionDecisionReason="New blackboard updates were delivered. Review them, then resubmit the candidate.",
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
