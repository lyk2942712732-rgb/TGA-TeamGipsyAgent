"""AOE collection only: freely collect from any task.md and deliver staged question JSON via outbox."""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from uuid import UUID

from ..action_logs import read_action_log
from .action_journal import ActionJournal
from .aoe_access import collection_state
from .cli_adapter import CodexAdapter
from .scout_probe_guard import ScoutProbeGuard, build_no_proxy, default_bypass_hosts


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
        "You are the AOE question collector. This role replaces generic Scout reconnaissance rounds. "
        "First read /task-overview/scene.md and /task-overview/task.md separately with native Read or cat "
        "(max_output_tokens=12000); complete any truncated read before continuing. User-uploaded files are in /inputs. "
        "Adapt to whatever task.md and /inputs provide (REST/GraphQL APIs, authenticated CTF web platforms, "
        "list+detail endpoints, remote attachment downloads, local archives/directories, or plain-text lists). "
        "Use native shell, Python, curl, archive or browser tools freely to collect questions and attachments. "
        "Prefer writing a short Python script to fetch/extract data and write JSON files directly so full descriptions "
        "and raw fields are preserved verbatim without model transcription. "
        "Deliver each complete question atomically to /workspace/outbox/<source_key>.json (write .tmp then "
        "os.replace) and save downloaded/extracted attachments under /workspace/attachments/<source_key>/. "
        "The system streams staged files from /workspace/outbox/ into the database, archives imported files to "
        "/workspace/imported/ (or /workspace/rejected/ with .error.json on errors), and wakes Workers concurrently. "
        "Outbox JSON fields: required source_key and non-empty description OR attachments; optional title (defaults "
        "to source_key), difficulty, category, tags, flag_format, connection_info, submission_instructions, "
        "collection_notes, attachments (paths under /workspace/... or /inputs/...), target ({mode:'managed'|'static'|"
        "'none'|'unknown', start?, stop?, addresses_path?, quota_limit?}), submission ({request:{method,url,headers,"
        "params,body}, success_path?, success_value?, duplicate_code?}), and raw_source (saved verbatim to "
        "source.json). Choose submission.request.body field names to match the actual platform API and preserve "
        "literal {flag}. Set target.mode='unknown' or submission=null when no static API contract is documented. "
        "Strict boundaries: "
        "1) Evidence-driven collection ONLY: request only URLs/endpoints given in task.md, /inputs, or explicitly "
        "referenced in actual responses (HTML links, JS routes, API fields, pagination links). NEVER brute-force or "
        "guess undocumented URLs/attachment paths, and never run directory/vulnerability scanners. "
        "2) NEVER solve challenges, probe target services for vulnerabilities, start/stop dynamic challenge "
        "instances, or submit flags. "
        "3) Missing items are valid: some attachments or connection details only exist after starting a dynamic "
        "target instance. If an attachment or field is not present in the platform response, leave it empty, explain "
        "why in collection_notes, and register the question immediately so Workers can handle it. "
        "Call scout_import_questions() to verify import receipts if needed, then call scout_finish_collection() "
        "as soon as available questions are staged (use status='partial' with warnings if some items could not be "
        "retrieved) and stop immediately."
    )


async def scout() -> None:
    batch_id = os.environ["TGA3_AOE_PARENT_ID"]
    workspace = Path(os.environ.get("TGA3_WORKSPACE", "/workspace"))
    state = workspace / ".tga3"
    state.mkdir(parents=True, exist_ok=True)
    for subdir in ("outbox", "attachments", "imported", "rejected"):
        (workspace / subdir).mkdir(parents=True, exist_ok=True)

    saved_env_keys = ("TGA3_CLI_HOME", "TGA3_SCOUT_PHASE", "HTTP_PROXY", "http_proxy", "NO_PROXY", "no_proxy")
    saved_env = {key: os.environ.get(key) for key in saved_env_keys}
    os.environ["TGA3_CLI_HOME"] = str(workspace / ".codex")

    max_misses = int(os.environ.get("TGA3_SCOUT_PROBE_MAX_MISSES", "6"))
    stall_seconds = float(os.environ.get("TGA3_AOE_COLLECTION_STALL_SECONDS", "120"))

    probe_guard: ScoutProbeGuard | None = None
    try:
        if os.environ.get("TGA3_DISABLE_SCOUT_PROBE_GUARD") != "1":
            probe_guard = ScoutProbeGuard(
                max_misses=max_misses,
                bypass_hosts=default_bypass_hosts(),
                state_path=state / "scout-probe-guard.json",
            )
            proxy_url = await probe_guard.start()
            no_proxy = build_no_proxy(os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or "")
            os.environ["HTTP_PROXY"] = proxy_url
            os.environ["http_proxy"] = proxy_url
            os.environ["NO_PROXY"] = no_proxy
            os.environ["no_proxy"] = no_proxy

        adapter = CodexAdapter()
        adapter.journal.identity["scope"] = "batch_collection"
        adapter.journal.enabled = True
        adapter.timeout = None

        async def emit(method, params):
            print(json.dumps({"method": method, "params": params}, ensure_ascii=False), flush=True)

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
            budget_seconds = request.get("budget_seconds")
            result = {
                "task_id": batch_id,
                "question_id": None,
                "phase": request["phase"],
                "finish_reason": "completed",
                "error": None,
                "budget_seconds": budget_seconds,
            }
            try:
                if request["phase"] != "collecting" or request.get("question_id"):
                    raise ValueError("AOE Scout supports only batch collection")
                os.environ["TGA3_SCOUT_PHASE"] = adapter.scout_phase = "collecting"
                started_at = time.monotonic()
                deadline = started_at + float(budget_seconds) if budget_seconds else None
                last_progress_at = started_at
                stalled_turns = 0
                wrapup_warned = False

                while not collection_state().get("collection_complete"):
                    if deadline is not None:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            if collection_state().get("imported_count", 0) > 0:
                                break
                            raise TimeoutError(f"Scout collection exceeded budget of {budget_seconds}s")
                        adapter.timeout = max(30, int(remaining))
                    else:
                        adapter.timeout = None

                    before = int(collection_state().get("imported_count", 0) or 0)
                    if before > 0 and (stalled_turns >= 1 or (time.monotonic() - last_progress_at) >= stall_seconds):
                        if wrapup_warned:
                            # Already warned once with no new questions; exit cleanly so host finishes partial.
                            break
                        wrapup_warned = True
                        prompt = (
                            "No new questions were imported recently. Stop probing missing endpoints or attachments, "
                            "write any remaining collected questions to /workspace/outbox/ (documenting missing items "
                            "in collection_notes), call scout_import_questions if needed, and immediately call "
                            "scout_finish_collection."
                        )
                    else:
                        prompt = (
                            "Read /task-overview/scene.md and /task-overview/task.md separately, inspect /inputs if "
                            "present, collect all available questions into /workspace/outbox/<source_key>.json "
                            "(attachments in /workspace/attachments/<source_key>/), then call scout_finish_collection "
                            "and stop."
                        )

                    output = await adapter.run_cycle(prompt, emit)
                    if output.strip():
                        await emit("agent.output.delta", {"text": output})
                    after_state = collection_state()
                    if after_state.get("collection_complete"):
                        break
                    after = int(after_state.get("imported_count", 0) or 0)
                    if after > before:
                        last_progress_at = time.monotonic()
                        stalled_turns = 0
                        wrapup_warned = False
                    else:
                        stalled_turns += 1
                        if after > 0 and wrapup_warned:
                            break
                        if after == 0 and stalled_turns >= 3:
                            raise RuntimeError("Scout ended three turns without registering any questions")
            except TimeoutError:
                result.update(finish_reason="timeout", error="Scout collection timed out")
            except Exception as exc:
                result.update(finish_reason="error", error=str(exc).replace(adapter.api_key, "[redacted]"))
            results = state / "aoe-scout-results"
            results.mkdir(exist_ok=True)
            target = results / (UUID(request["id"]).hex + ".json")
            temporary = target.with_suffix(".tmp")
            temporary.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
            temporary.replace(target)
    finally:
        for key, original in saved_env.items():
            if original is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = original
        if probe_guard is not None:
            await probe_guard.stop()
