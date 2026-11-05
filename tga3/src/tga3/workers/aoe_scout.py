"""AOE collection only: register question inputs while Workers solve concurrently."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from uuid import UUID

from ..action_logs import read_action_log
from .action_journal import ActionJournal
from .cli_adapter import CodexAdapter


def recover_unfinished(journal: ActionJournal) -> None:
    """After a stopped process, preserve unfinished commands as interrupted facts."""
    enabled = journal.enabled
    journal.enabled = True
    try:
        for action in read_action_log(journal.directory)["actions"]:
            if action["status"] == "started":
                native_id = action["tool_call_id"]
                journal.pending[native_id] = action
                journal.record(native_id, action["action"]["tool"], status="interrupted")
    finally:
        journal.enabled = enabled


def instructions() -> str:
    return (
        "You are the TGA AOE question collector. AOE Scout has ONLY the collection pre-phase. "
        "These AOE-specific instructions replace the generic three-round Scout role. "
        "Collect the COMPLETE question list from the "
        "user's text/files or gateway, including pagination. Preserve original IDs, names, descriptions, "
        "shared constraints, targets, submission requirements and explicitly supplied difficulty. "
        "Never invent difficulty. Inputs are /inputs (read-only), private files /workspace, Skills /skills. "
        "Download each question's required attachments before registering it, or reference supplied /inputs files. "
        "Call scout_register_questions(source_key,title,description,difficulty?,connection_info?,"
        "submission_instructions?,attachments?) as soon as that question's metadata and files are complete. "
        "Register incrementally; prioritize supplied easy questions when possible. Workers immediately solve "
        "registered easy/unrated questions while you continue collecting. Do not wait to accumulate a batch. "
        "Do not probe or solve targets, start/close challenge instances, extract Intel, merge Intel or write reports. "
        "Do not omit failed downloads or pagination. Only after the complete list and files are registered call "
        "scout_finish_collection(expected_count), then finish immediately. There are NO later Scout rounds "
        "and no collection time budget. Other Workers' ongoing questions must not be touched."
    )


async def scout() -> None:
    batch_id = os.environ["TGA3_AOE_PARENT_ID"]
    workspace = Path(os.environ.get("TGA3_WORKSPACE", "/workspace"))
    state = workspace / ".tga3"
    state.mkdir(parents=True, exist_ok=True)
    os.environ["TGA3_CLI_HOME"] = str(workspace / ".codex")
    adapter = CodexAdapter()
    adapter.journal.identity["scope"] = "batch_collection"
    adapter.journal.enabled = True
    adapter.timeout = None
    manifest_path = Path(os.environ.get("TGA3_AOE_ROOT", "/aoe")) / "inputs/questions.json"

    async def emit(method, params):
        print(json.dumps({"method": method, "params": params}, ensure_ascii=False), flush=True)

    def manifest():
        return json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {
            "collection_complete": False, "questions": []}

    previous_request = None
    while True:
        path = state / "aoe-scout-request.json"
        if not path.is_file():
            await asyncio.sleep(0.1)
            continue
        request = json.loads(path.read_text(encoding="utf-8"))
        if request["id"] == previous_request:
            await asyncio.sleep(0.1)
            continue
        previous_request = request["id"]
        result = {"task_id": batch_id, "question_id": None, "phase": request["phase"],
                  "finish_reason": "completed", "error": None, "budget_seconds": None}
        try:
            if request["phase"] != "collecting" or request.get("question_id"):
                raise ValueError("AOE Scout supports only batch collection")
            os.environ["TGA3_SCOUT_PHASE"] = adapter.scout_phase = "collecting"
            stalled = 0
            while not manifest()["collection_complete"]:
                before = len(manifest()["questions"])
                output = await adapter.run_cycle(instructions(), emit)
                if output.strip():
                    await emit("agent.output.delta", {"text": output})
                stalled = stalled + 1 if len(manifest()["questions"]) == before else 0
                if stalled >= 3 and not manifest()["collection_complete"]:
                    raise RuntimeError("Scout ended three turns without registering more questions")
        except Exception as exc:
            result.update(finish_reason="error", error=str(exc).replace(adapter.api_key, "[redacted]"))
        results = state / "aoe-scout-results"
        results.mkdir(exist_ok=True)
        target = results / (UUID(request["id"]).hex + ".json")
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        temporary.replace(target)
