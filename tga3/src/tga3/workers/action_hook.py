"""Save Worker tool results before the CLI returns them to the model."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

from .action_journal import ActionJournal


def hook_journal() -> ActionJournal:
    return ActionJournal(
        Path(os.environ.get("TGA3_LOGS", "/logs")),
        identity={"task_id": os.environ["TGA3_TASK_ID"], "agent_id": os.environ["TGA3_AGENT_ID"],
                  **({"question_id": os.environ["TGA3_QUESTION_ID"]} if os.environ.get("TGA3_QUESTION_ID") else {}),
                  "runtime": os.environ["TGA3_AGENT_RUNTIME"]},
        secret=os.environ.get("TGA3_API_KEY", ""),
        output_directory=Path(os.environ.get("TGA3_WORKSPACE", "/workspace")),
    )


def _pending_directory(journal: ActionJournal) -> Path:
    return journal.output_directory / ".tga3/action-hooks"


def _restore(journal: ActionJournal, state: dict) -> dict:
    record = state["record"]
    native_id = record["tool_call_id"]
    journal.pending[native_id] = record
    journal.output_snapshots[native_id] = {path: tuple(value) for path, value in state["snapshot"].items()}
    return record


def record_tool(event: dict) -> dict | None:
    if os.environ.get("TGA3_AGENT_ROLE") != "worker" or not event.get("tool_use_id"):
        return None
    journal = hook_journal()
    native_id = str(event["tool_use_id"])
    generation = event.get("_tga_generation", os.environ.get("TGA3_CLI_GENERATION", ""))
    token = hashlib.sha256(f"{generation}:{native_id}".encode()).hexdigest()
    path = _pending_directory(journal) / f"{token}.json"
    tool = event.get("tool_name", "unknown")
    inputs = event.get("tool_input", {})
    if isinstance(inputs, str):
        try:
            inputs = json.loads(inputs)
        except ValueError:
            inputs = {"command": inputs}
    if event["hook_event_name"] == "PreToolUse":
        if not path.exists():
            record = journal.record(native_id, tool, inputs=inputs, cwd=event.get("cwd", "/workspace"))
            journal._write(path, json.dumps({"generation": generation, "record": record,
                                            "snapshot": journal.output_snapshots[native_id]}))
        return None
    if path.is_file():
        _restore(journal, json.loads(path.read_text(encoding="utf-8")))
    response = event.get("tool_response", event.get("error", ""))
    code = response.get("exit_code") if isinstance(response, dict) else None
    if code is None and isinstance(response, str):
        match = re.search(r"(?:Process exited with code|Exit code:)\s*(-?\d+)", response)
        if match:
            code = int(match[1])
    failed = (event["hook_event_name"] == "PostToolUseFailure" or bool(event.get("is_error"))
              or bool(isinstance(response, dict) and (response.get("isError") or response.get("interrupted")))
              or code is not None and code != 0)
    record = journal.record(native_id, tool, inputs=inputs,
                            result={"type": "tool_result", "content": response, "exit_code": code},
                            status="failed" if failed else "completed", cwd=event.get("cwd", "/workspace"))
    path.unlink(missing_ok=True)
    return record


def interrupt_hook_actions(generation: str) -> None:
    journal = hook_journal()
    for path in _pending_directory(journal).glob("*.json"):
        state = json.loads(path.read_text(encoding="utf-8"))
        if state["generation"] != generation:
            continue
        record = _restore(journal, state)
        journal.record(record["tool_call_id"], record["action"]["tool"], status="interrupted")
        path.unlink()


def result_context(record: dict) -> str:
    return (
        f"TGA action_id: {record['action_id']}\n"
        "If this result establishes a new reusable fact, immediately write one JSON file in /findings/ "
        "with action_id, claim, scope and optional preconditions. The system registers evidence and imports it. "
        "Otherwise continue; no log review."
    )
