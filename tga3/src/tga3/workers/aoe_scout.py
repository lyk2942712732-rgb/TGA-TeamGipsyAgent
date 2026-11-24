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
from .adapter_factory import create_adapter
from .aoe_access import collection_state
from .cli_adapter import CliAdapter, CodexAdapter
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
        "os.replace). Do NOT spend time downloading remote attachments during collection: put remote download "
        "URLs directly into attachments (either 'https://...' strings or {url, filename?, headers?, params?} objects) "
        "and TGA3 will download them automatically when a Worker claims that question. Only save files under "
        "/workspace/attachments/<source_key>/ for local archives or when an ephemeral login session is required. "
        "The system streams staged files from /workspace/outbox/ into the database, archives imported files to "
        "/workspace/imported/ (or /workspace/rejected/ with .error.json on errors), and wakes Workers concurrently. "
        "Outbox JSON fields: required source_key and (already_solved=true OR non-empty description OR attachments); "
        "optional title (defaults to source_key), difficulty, category, tags, flag_format, connection_info, "
        "submission_instructions, collection_notes, already_solved (bool: set true if the platform already marks "
        "this question solved before this task started—skip its attachments so Workers and Reporter skip it), "
        "solved_flag (optional known flag for pre-solved items), attachments (remote URLs/download specs or local "
        "paths under /workspace/... or /inputs/...), target, submission, and raw_source (saved verbatim to "
        "source.json). "
        "EXACT schema for target (CRITICAL — NEVER pass bare URL strings for start/stop): "
        '{"mode":"none"|"static"|"unknown"} OR for dynamic targets: '
        '{"mode":"managed","start":{"method":"POST","url":"https://...","headers":{...},"params":{...},"body":{...}},'
        '"stop":{"method":"POST","url":"https://...","headers":{...},"params":{...},"body":{...}},'
        '"addresses_path":"data.addresses","quota_limit":1}. '
        "Every target.start, target.stop, target.status, and submission.request MUST be a JSON object with at least "
        "'url' (and 'method'/'headers'/'params'/'body' as needed)—NEVER a plain URL string. "
        "EXACT schema for submission: "
        '{"request":{"method":"POST","url":"https://...","headers":{...},"params":{...},"body":{"<id_field>":123,'
        '"<flag_field>":"{flag}"}},"success_path":"data.result","success_value":true,'
        '"duplicate_codes":[...],"duplicate_keywords":[...]}. '
        "Choose submission.request.body field names to match the actual platform API and preserve literal {flag}. "
        "Set target={'mode':'unknown'} or submission=null when no static API contract is documented. "
        "Sample-first workflow: when generating many questions (especially managed-target challenges), first write "
        "ONE sample question JSON to /workspace/outbox/ and call scout_import_questions() to confirm zero schema "
        "errors before generating the remaining files. Note that /workspace/outbox/ being empty does NOT mean "
        "success—rejected files move to /workspace/rejected/ with .error.json, and grouped errors are recorded in "
        "/aoe/collection-state.json. Always inspect /aoe/collection-state.json or call scout_import_questions() "
        "instead of running no-op checks (NEVER run echo ok, true, ls outbox loops, or write dummy test.json files). "
        "Strict boundaries: "
        "1) Evidence-driven collection ONLY: request only URLs/endpoints given in task.md, /inputs, or explicitly "
        "referenced in actual responses (HTML links, JS routes, API fields, pagination links). NEVER brute-force or "
        "guess undocumented URLs/attachment paths, and never run directory/vulnerability scanners. "
        "2) NEVER solve challenges, probe target services for vulnerabilities, start/stop dynamic challenge "
        "instances, or submit flags. "
        "3) Missing items are valid: some attachments or connection details only exist after starting a dynamic "
        "target instance. If an attachment or field is not present in the platform response, leave it empty, explain "
        "why in collection_notes, and register the question immediately so Workers can handle it. "
        "Zero-token watch probe (HIGH VALUE for dynamic/continuous-release platforms): "
        "If questions come from a scriptable platform API or list endpoint, also save a standalone, read-only "
        "Python script at /workspace/watch_probe.py (using only stdlib or installed packages, reading credentials "
        "from /task-overview/task.md or saved session files in /workspace). When executed as "
        "`python3 /workspace/watch_probe.py`, it must perform ONLY a lightweight read-only listing (never start "
        "targets or submit flags), print a single JSON object to stdout in the exact format "
        '{"source_keys": ["<key1>", "<key2>", ...], "solved_keys": ["<already_solved_key>", ...]} '
        "using the exact same source_key format you wrote to /workspace/outbox/, and exit 0 (or non-zero if auth "
        "expired / request failed). Verify `python3 /workspace/watch_probe.py` once before finishing. If the "
        "source is purely static local files or cannot be polled cleanly by script, do NOT create watch_probe.py. "
        "Call scout_import_questions() to verify import receipts if needed, then call scout_finish_collection() "
        "as soon as available questions are staged (use status='partial' with warnings if some items could not be "
        "retrieved) and stop immediately."
    )


async def scout(adapter: CliAdapter | None = None) -> None:
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

        if adapter is None:
            runtime = os.environ.get("TGA3_AGENT_RUNTIME") or "codex_cli"
            adapter = CodexAdapter() if runtime == "codex_cli" else create_adapter(runtime)
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
                incremental = bool(request.get("incremental"))
                detected_new_keys = [
                    str(item).strip() for item in (request.get("detected_new_keys") or []) if str(item).strip()
                ]
                started_at = time.monotonic()
                deadline = started_at + float(budget_seconds) if budget_seconds else None
                last_progress_at = started_at
                stalled_turns = 0
                wrapup_warned = False

                def round_done(st: dict) -> bool:
                    if "round_complete" in st:
                        return bool(st["round_complete"])
                    return bool(st.get("collection_complete"))

                while not round_done(collection_state()):
                    if deadline is not None:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            if incremental or collection_state().get("imported_count", 0) > 0:
                                break
                            raise TimeoutError(f"Scout collection exceeded budget of {budget_seconds}s")
                        adapter.timeout = max(30, int(remaining))
                    else:
                        adapter.timeout = None

                    state_now = collection_state()
                    before = int(state_now.get("imported_count", 0) or 0)
                    if incremental:
                        if stalled_turns >= 1:
                            if wrapup_warned:
                                break
                            wrapup_warned = True
                            prompt = (
                                "Wrap up this incremental scan now: stage any newly discovered questions into "
                                "/workspace/outbox/<source_key>.json, verify /workspace/watch_probe.py if applicable, "
                                "and immediately call scout_finish_collection() (even if 0 new questions were found)."
                            )
                        else:
                            hint_keys = (
                                f" The zero-token sentinel detected newly released source_keys: "
                                f"{json.dumps(detected_new_keys, ensure_ascii=False)}. Prioritize collecting them!"
                                if detected_new_keys
                                else ""
                            )
                            prompt = (
                                "This is an incremental follow-up collection round on an active AOE task. "
                                "Read /aoe/collection-state.json first to inspect `imported_keys` "
                                "(already registered). Reuse your existing scripts/auth in /workspace "
                                "to check the platform for newly released questions, newly posted "
                                f"hints/updates, or questions already solved on the platform.{hint_keys} "
                                "Stage ONLY new questions (or existing questions that became "
                                "already_solved=true or gained new hints) into "
                                "/workspace/outbox/<source_key>.json. If /workspace/watch_probe.py "
                                "is missing or failed (e.g. expired session), create/repair and verify "
                                "it for scriptable platforms. Then call scout_import_questions() if "
                                "files were staged and call scout_finish_collection() immediately "
                                "(0 new questions is valid)."
                            )
                    elif before > 0 and (stalled_turns >= 1 or (time.monotonic() - last_progress_at) >= stall_seconds):
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
                            "(put remote attachment URLs directly in attachments instead of downloading them, and set "
                            "already_solved=true for questions already solved on the platform). For scriptable "
                            "platforms, also create and verify /workspace/watch_probe.py for zero-token standby "
                            "monitoring. Then call scout_finish_collection and stop."
                        )

                    output = await adapter.run_cycle(prompt, emit)
                    if output.strip():
                        await emit("agent.output.delta", {"text": output})
                    after_state = collection_state()
                    if round_done(after_state):
                        break
                    after = int(after_state.get("imported_count", 0) or 0)
                    if after > before:
                        last_progress_at = time.monotonic()
                        stalled_turns = 0
                        wrapup_warned = False
                    else:
                        stalled_turns += 1
                        if (incremental or after > 0) and wrapup_warned:
                            break
                        if not incremental and after == 0 and stalled_turns >= 3:
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
