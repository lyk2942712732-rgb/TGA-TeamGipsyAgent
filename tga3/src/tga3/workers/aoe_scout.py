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
        "You are the AOE question collector. This role replaces all generic Scout reconnaissance rounds. "
        "First read /task-overview/scene.md and /task-overview/task.md separately with native cat "
        "(max_output_tokens=12000); complete any truncated read before continuing. Supplied uploads are /inputs. "
        "Question information MUST come ONLY from the explicitly supplied question-list API. All obtainable "
        "question metadata is in that list. Never explore missing information: no guessing attachment URLs, "
        "detail/hint endpoints, API documentation, frontend JS, directory scanning, target probing or downloads. "
        "Do not run curl, Python, scripts, browsers or install tools. Native tools are restricted to reading "
        "supplied overview/input documents. Missing attachment entry points remain empty, never block registration. "
        "Project MCP tool scout_collect_questions fetches and registers the list without model transcription. "
        "Prepare one plan: request={method,url,headers,params,body} using the exact supplied list interface and "
        "actual credentials, items_path='' for a root array, fields={source_key:'unique_code',"
        "description:'description',difficulty:'difficulty'} adjusted ONLY to documented source fields. "
        "Map title and attachments only when explicitly present. Never invent difficulty or rewrite descriptions. "
        "next_url_path and total_path map documented next-page URL and total count; do not guess pagination. "
        "If a VPN check is explicitly required, supply preflight={method:'GET',url:'supplied connectivity URL'}; "
        "the program requires status=ok before fetching the list. "
        "Use supplied task instructions to declare shared target lifecycle and submission contracts, not to "
        "discover additional question information. target.mode='managed' needs start and stop requests, actual "
        "credential headers, addresses_path and quota_limit when documented. Empty container_addr before start "
        "does not mean no target. Use none/static/unknown only when supported by supplied information. "
        "Template {unique_code} expands from each source item. submission={request:{method,url,headers,params,body},"
        "success_path:'correct',duplicate_code:'duplicate'} uses supplied endpoints and actual credentials. "
        "For TSec, body={unique_code:'{unique_code}',flag:'{flag}'}, preserving literal {flag}. "
        "Do not copy the shared scene or initial request into per-question descriptions/connection_info; "
        "Workers read those originals separately from the overview. source.json preserves every list field. "
        "The system registers incrementally and Workers claim concurrently. Check collection_complete=true "
        "and finish immediately. No reconnaissance, Intel rounds, reports or post-collection exploration. "
        "If the supplied list API fails, report that failure or retry the SAME API; never seek alternative endpoints."
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
    manifest_path = Path(os.environ.get("TGA3_AOE_ROOT", "/aoe")) / "questions.json"

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
                output = await adapter.run_cycle("Read the shared overview, collect the supplied question-list API, then stop.", emit)
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
