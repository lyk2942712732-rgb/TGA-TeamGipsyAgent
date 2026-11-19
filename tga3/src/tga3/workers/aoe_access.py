"""Local AOE read bootstrap and lightweight Scout anti-enumeration guard; no control-plane models/imports."""

from __future__ import annotations

import json
import os
import re
import shlex
import threading
import time
from pathlib import Path, PurePosixPath

SCANNER_BINARIES = {
    "amass",
    "dirb",
    "dirbuster",
    "dirsearch",
    "feroxbuster",
    "ffuf",
    "gobuster",
    "hydra",
    "katana",
    "masscan",
    "medusa",
    "nikto",
    "nmap",
    "nuclei",
    "sqlmap",
    "subfinder",
    "wfuzz",
    "wpscan",
}

SHELL_TOOLS = {"bash", "exec_command", "shell", "shell_command", "command_execution", "run_shell_command"}
NETWORK_HINTS = re.compile(
    r"\b(?:curl|wget|httpie|http|fetch|lynx|playwright|puppeteer|selenium|requests|httpx|urllib|aiohttp)\b|https?://",
    re.I,
)
MISS_RESPONSE_PATTERN = re.compile(
    r"probe_guard_tripped|"
    r"HTTP/(?:1\.[01]|2(?:\.0)?)\s+40[345]\b|"
    r"\b404\s+Not\s+Found\b|"
    r"\b405\s+Method\s+Not\s+Allowed\b|"
    r"[\"']status(?:_code)?[\"']\s*:\s*40[345]\b|"
    r"\bstatus[=:\s]+40[345]\b|"
    r"\bCannot\s+(?:GET|POST|PUT|DELETE)\s+/",
    re.I,
)


def inputs(event: dict) -> dict:
    value = event.get("tool_input", {})
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            value = {}
    return value if isinstance(value, dict) else {}


def _shell_command(event: dict) -> str:
    tool = event.get("tool_name", "").lower()
    if tool not in SHELL_TOOLS:
        return ""
    args = inputs(event)
    return str(args.get("command") or args.get("cmd") or "")


def _split_shell_segments(command: str) -> list[list[str]]:
    """Split a shell command into per-subcommand argv tokens to inspect invoked executables."""
    if not command.strip():
        return []
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    if len(tokens) == 3 and tokens[0] in {"bash", "/bin/bash", "sh", "/bin/sh"} and tokens[1] in {"-c", "-lc"}:
        return _split_shell_segments(tokens[2])
    segments: list[list[str]] = []
    current: list[str] = []
    for token in tokens:
        if token in {";", "&&", "||", "|", "&"}:
            if current:
                segments.append(current)
                current = []
        else:
            current.append(token)
    if current:
        segments.append(current)
    return segments


def _invoked_scanner(command: str) -> str | None:
    for segment in _split_shell_segments(command):
        idx = 0
        while idx < len(segment) and "=" in segment[idx] and not segment[idx].startswith("-"):
            idx += 1
        while idx < len(segment) and PurePosixPath(segment[idx]).name in {"sudo", "env", "timeout", "nice", "nohup"}:
            idx += 1
            while idx < len(segment) and segment[idx].startswith("-"):
                idx += 1
        if idx < len(segment):
            binary = PurePosixPath(segment[idx]).name.lower()
            if binary in SCANNER_BINARIES:
                return binary
    return None


def read_paths(event: dict) -> list[str]:
    tool = event.get("tool_name", "").lower()
    args = inputs(event)
    if tool in {"read", "read_file"}:
        # A partial read does not count as consuming the full overview.
        return [args.get("file_path") or args.get("filePath") or args.get("absolute_path", "")] \
            if args.get("offset", 1) in {None, 0, 1} and not args.get("limit") else []
    if tool not in SHELL_TOOLS:
        return []
    try:
        words = shlex.split(args.get("command") or args.get("cmd") or "")
        if len(words) == 3 and words[0] in {"bash", "/bin/bash", "sh", "/bin/sh"} and words[1] in {"-c", "-lc"}:
            words = shlex.split(words[2])
    except ValueError:
        return []
    if words and words[0] in {"cat", "/bin/cat"}:
        paths = [w for w in words[1:] if w != "--"]
        if paths and all(p.startswith("/") and not re.search(r"[;$`|&<>\n]", p) for p in paths):
            return paths
    return []


def collection_state() -> dict:
    root = Path(os.environ.get("TGA3_AOE_ROOT", "/aoe"))
    for name in ("collection-state.json", "questions.json"):
        path = root / name
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    if "imported_count" not in data and isinstance(data.get("questions"), list):
                        data["imported_count"] = len(data["questions"])
                    return data
            except (OSError, ValueError):
                continue
    return {"collection_complete": False, "status": "collecting", "imported_count": 0, "imported_keys": []}


def _probe_state_path() -> Path:
    return Path(os.environ.get("TGA3_WORKSPACE", "/workspace")) / ".tga3/scout-probe-state.json"


def _load_probe_state() -> dict:
    path = _probe_state_path()
    defaults = {
        "imported_count": 0,
        "last_seen_import_seq": 0,
        "last_error_signature": "",
        "same_error_repeats": 0,
        "consecutive_misses": 0,
        "tripped": False,
        "idle_tool_calls": 0,
        "last_progress_at": time.time(),
        "stall_warned": False,
        "stall_locked": False,
    }
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {**defaults, **data}
        except (OSError, ValueError):
            pass
    return defaults


def _save_probe_state(state: dict) -> None:
    path = _probe_state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)
    except OSError:
        pass


def _max_probe_misses() -> int:
    try:
        return max(2, int(os.environ.get("TGA3_SCOUT_PROBE_MAX_MISSES", "6")))
    except ValueError:
        return 6


def _stall_seconds() -> float:
    try:
        return max(10.0, float(os.environ.get("TGA3_AOE_COLLECTION_STALL_SECONDS", "120")))
    except ValueError:
        return 120.0


def _is_idle_probe_command(command: str) -> bool:
    """Detect no-op liveness checks (echo ok, true, sleep, repeated outbox ls/wc) that waste budget."""
    stripped = command.strip()
    if not stripped or any(op in stripped for op in (">", ">>", "python", "node", "curl", "wget")):
        return False
    segments = _split_shell_segments(stripped)
    if not segments:
        return False
    noop_bins = {"echo", "true", ":", "sleep", "printf", "pwd", "date"}
    if all(PurePosixPath(seg[0]).name.lower() in noop_bins for seg in segments if seg):
        return True
    first = PurePosixPath(segments[0][0]).name.lower()
    if first in {"ls", "find", "stat", "wc"} and "outbox" in stripped and "rejected" not in stripped:
        return True
    return False


def _format_recent_errors(recent_errors: list[dict]) -> str:
    lines: list[str] = []
    for item in recent_errors[:5]:
        count = item.get("count", 1)
        samples = ", ".join(item.get("sample_files", [])[:3])
        sample_text = f" (e.g. {samples})" if samples else ""
        lines.append(f"- [{count} files{sample_text}]: {item.get('error', '')}")
    return "\n".join(lines)


def _error_signature(recent_errors: list[dict]) -> str:
    if not recent_errors:
        return ""
    return "|".join(str(item.get("error", "")) for item in recent_errors[:3])


def _sync_error_tracking(state: dict, current: dict) -> None:
    """Update repeat-error counters once per import_seq so sync and async imports stay consistent."""
    import_seq = int(current.get("import_seq", 0) or 0)
    if import_seq <= int(state.get("last_tracked_error_seq", 0) or 0):
        return
    state["last_tracked_error_seq"] = import_seq
    sig = _error_signature(current.get("recent_errors") or [])
    if sig:
        if sig == state.get("last_error_signature"):
            state["same_error_repeats"] = int(state.get("same_error_repeats", 0) or 0) + 1
        else:
            state["last_error_signature"] = sig
            state["same_error_repeats"] = 1
    else:
        state["last_error_signature"] = ""
        state["same_error_repeats"] = 0


_read_lock = threading.RLock()


def overview_policy(event: dict) -> str | None:
    # Native tools can finish in parallel; their acknowledgements must be unioned.
    with _read_lock:
        return _overview_policy(event)


def scout_post_tool_notice(event: dict, *, synced_import_seq: int = 0) -> str | None:
    """Return non-blocking import receipts, grouped rejection feedback, and stall/probe guidance."""
    if os.environ.get("TGA3_AGENT_ROLE") != "scout" or not os.environ.get("TGA3_AOE_PARENT_ID"):
        return None
    if event.get("hook_event_name") not in {"PostToolUse", "PostToolUseFailure"}:
        return None
    with _read_lock:
        state = _load_probe_state()
        current = collection_state()
        _sync_error_tracking(state, current)
        notices: list[str] = []

        import_seq = int(current.get("import_seq", 0) or 0)
        last_seen = int(state.get("last_seen_import_seq", 0) or 0)
        recent_errors = current.get("recent_errors") or []
        imported_count = int(current.get("imported_count", 0) or 0)
        rejected_count = int(current.get("rejected_count", 0) or 0)

        if synced_import_seq > last_seen:
            state["last_seen_import_seq"] = synced_import_seq
            last_seen = synced_import_seq

        if import_seq > last_seen:
            last_batch = current.get("last_batch") or {}
            msg = (
                f"[AOE Collection Import Update #{import_seq}] "
                f"imported_total={imported_count} "
                f"(batch: +{last_batch.get('imported', 0)} new, "
                f"{last_batch.get('skipped_existing', 0)} skipped_existing, "
                f"{last_batch.get('rejected', 0)} rejected; total_rejected={rejected_count})."
            )
            if recent_errors:
                msg += (
                    "\nGrouped rejection errors (fix your generator script before writing again):\n"
                    + _format_recent_errors(recent_errors)
                )
            notices.append(msg)
            state["last_seen_import_seq"] = import_seq

        if recent_errors and int(state.get("same_error_repeats", 0) or 0) >= 2:
            notices.append(
                f"WARNING: The exact same rejection error repeated {state['same_error_repeats']} times! "
                "Do NOT re-run the unchanged script or write dummy test files. Fix the JSON field structure "
                "shown above (e.g., target.start/stop must be {method,url,headers?,params?,body?} objects, "
                'or set target={"mode":"unknown"}), or call scout_finish_collection if remaining items cannot be fixed.'
            )

        max_misses = _max_probe_misses()
        if state.get("tripped"):
            if imported_count > 0:
                notices.append(
                    "Probe guard tripped after repeated 403/404/405 responses without new questions. "
                    "Stop guessing undocumented paths or attachments; write remaining collected questions "
                    "to /workspace/outbox/ (with collection_notes explaining missing items) and call "
                    "scout_finish_collection."
                )
            else:
                notices.append(
                    "Multiple 403/404/405 responses detected. Stop guessing undocumented paths; re-check "
                    "/task-overview/task.md and /inputs/ for the exact supplied entry points."
                )
        elif state.get("consecutive_misses", 0) >= max(2, max_misses - 2):
            notices.append(
                f"Detected {state['consecutive_misses']} consecutive 403/404/405 probe responses without new "
                "questions. Do not brute-force attachment or endpoint URLs; if an attachment is not referenced "
                "in the response (e.g., only inside a dynamic target), leave attachments empty, document it in "
                "collection_notes, and move on."
            )

        now = time.time()
        idle_calls = int(state.get("idle_tool_calls", 0) or 0)
        elapsed_idle = now - float(state.get("last_progress_at", now) or now)
        stall_limit = _stall_seconds()
        if imported_count > 0 and (idle_calls >= 8 or elapsed_idle >= stall_limit):
            state["stall_warned"] = True
            if idle_calls >= 12 or elapsed_idle >= stall_limit + 45:
                state["stall_locked"] = True
            stall_msg = (
                f"Stall warning: {imported_count} questions are already imported, but no new questions were "
                f"registered across {idle_calls} recent tool calls ({int(elapsed_idle)}s). "
                "Stop running no-op commands (echo/true/ls outbox). "
            )
            if recent_errors:
                stall_msg += (
                    "Either fix the grouped rejection errors below and import the remaining questions now, "
                    "or call scout_finish_collection(status='partial', warnings=[...]):\n"
                    + _format_recent_errors(recent_errors)
                )
            else:
                stall_msg += "Call scout_finish_collection immediately so Workers can proceed."
            notices.append(stall_msg)

        _save_probe_state(state)
        return "\n\n".join(notices) if notices else None


def _update_scout_probe_state(event: dict, output: str) -> None:
    if os.environ.get("TGA3_AGENT_ROLE") != "scout":
        return
    command = _shell_command(event)
    tool = event.get("tool_name", "").split("__")[-1].split(".")[-1]
    current = collection_state()
    imported_count = int(current.get("imported_count", 0) or 0)
    state = _load_probe_state()

    if imported_count > int(state.get("imported_count", 0) or 0):
        state["imported_count"] = imported_count
        state["consecutive_misses"] = 0
        state["tripped"] = False
        state["idle_tool_calls"] = 0
        state["last_progress_at"] = time.time()
        state["stall_warned"] = False
        state["stall_locked"] = False
    else:
        state["imported_count"] = imported_count
        if imported_count > 0 and tool not in {"scout_import_questions", "scout_finish_collection"}:
            increment = 2 if (command and _is_idle_probe_command(command)) else 1
            state["idle_tool_calls"] = int(state.get("idle_tool_calls", 0) or 0) + increment

    # Track repeated identical rejection errors across import batches.
    _sync_error_tracking(state, current)

    if tool in {"scout_import_questions", "scout_finish_collection"}:
        _save_probe_state(state)
        return
    if "probe_guard_tripped" in output:
        state["consecutive_misses"] = max(int(state.get("consecutive_misses", 0)), _max_probe_misses())
        state["tripped"] = True
        _save_probe_state(state)
        return
    if command and NETWORK_HINTS.search(command) and MISS_RESPONSE_PATTERN.search(output):
        misses = int(state.get("consecutive_misses", 0)) + 1
        state["consecutive_misses"] = misses
        if misses >= _max_probe_misses():
            state["tripped"] = True
    _save_probe_state(state)


def _worker_submission_block(event: dict) -> str | None:
    if os.environ.get("TGA3_AGENT_ROLE") != "worker":
        return None
    if os.environ.get("TGA3_AOE_AUTO_SUBMIT_FLAG") == "0":
        return None
    sub_path = Path(os.environ.get("TGA3_WORKSPACE", "/workspace")) / ".tga3/submission.json"
    if not sub_path.is_file():
        return None
    try:
        info = json.loads(sub_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(info, dict) or not info.get("managed_by_system"):
        return None
    blocked_url = str(info.get("blocked_url") or "").strip()
    if not blocked_url:
        return None
    from urllib.parse import urlsplit

    parsed = urlsplit(blocked_url)
    host_path = f"{parsed.netloc}{parsed.path}" if parsed.netloc and parsed.path else ""
    command = _shell_command(event)
    args = inputs(event)
    haystack = command or str(args.get("url") or "")
    if not haystack:
        return None
    if blocked_url in haystack or (
        host_path and host_path in haystack and NETWORK_HINTS.search(haystack)
    ):
        return (
            "Direct flag submission to the platform is forbidden for Workers. "
            "Do NOT call the platform submission endpoint yourself; write the exact flag to "
            "/workspace/final-candidate.json (conclusion, rationale, finding_ids) and TGA3 will "
            "submit it to the platform automatically and return the real platform receipt."
        )
    return None


def _overview_policy(event: dict) -> str | None:
    root = os.environ.get("TGA3_TASK_OVERVIEW_ROOT")
    if not root or not os.environ.get("TGA3_AOE_PARENT_ID"):
        return None
    required = {str(PurePosixPath(root) / name) for name in ["scene.md", "task.md"]}
    role = os.environ.get("TGA3_AGENT_ROLE")
    paths = read_paths(event)
    state = Path(os.environ.get("TGA3_WORKSPACE", "/workspace")) / ".tga3/overview-read.json"
    context = (
        event.get("session_id")
        or os.environ.get("TGA3_EXPERIENCE_SESSION_TOKEN")
        or os.environ.get("TGA3_OVERVIEW_SESSION_TOKEN", "scout-context")
    )
    saved = json.loads(state.read_text()) if state.is_file() else {}
    seen = set(saved.get("paths", [])) if saved.get("context") == context else set()
    name = event.get("hook_event_name")
    if name in {"PostToolUse", "PostToolUseFailure"}:
        response = event.get("tool_response", event.get("error", ""))
        output = json.dumps(response, ensure_ascii=False) if not isinstance(response, str) else response
        failed = isinstance(response, dict) and (
            response.get("isError")
            or response.get("is_error")
            or response.get("interrupted")
            or response.get("exit_code", 0)
            or response.get("truncated") is True
        )
        file = response.get("file", {}) if isinstance(response, dict) else {}
        partial = isinstance(file, dict) and file.get("totalLines", 0) > file.get("numLines", float("inf"))
        if (
            name == "PostToolUse"
            and paths
            and not event.get("is_error")
            and not failed
            and not partial
            and not re.search(
                r"Output truncated|truncated output|tokens truncated|No such file|"
                r"not found|Permission denied|Exit code: [1-9]",
                output,
                re.I,
            )
        ):
            seen.update(required.intersection(paths))
            state.parent.mkdir(parents=True, exist_ok=True)
            state.write_text(json.dumps({"context": context, "paths": sorted(seen)}))
        _update_scout_probe_state(event, output)
        return None
    if name != "PreToolUse":
        return None
    if role == "scout":
        current = collection_state()
        if current.get("collection_complete"):
            return "AOE question collection is complete. Stop immediately; no further tools or exploration."
    safe_paths = bool(paths) and all(
        ".." not in PurePosixPath(p).parts and (p in required or role == "scout" and p.startswith("/inputs/"))
        for p in paths
    )
    if not required.issubset(seen) and not safe_paths:
        return (
            "Before working, read /task-overview/scene.md and /task-overview/task.md separately with native Read "
            "or cat (Codex max_output_tokens=12000). Do not obtain these texts from the question blackboard. "
            "If output was truncated, repeat a complete read with a larger output allowance."
        )
    if role == "scout":
        tool = event.get("tool_name", "").split("__")[-1].split(".")[-1]
        if tool in {"scout_import_questions", "scout_finish_collection"}:
            return None
        command = _shell_command(event)
        if command:
            if scanner := _invoked_scanner(command):
                return (
                    f"Directory/vulnerability scanner '{scanner}' is forbidden during AOE question collection. "
                    "Only fetch supplied or response-referenced URLs; leave unavailable items empty with "
                    "collection_notes."
                )
            probe_state = _load_probe_state()
            current = collection_state()
            imported_count = int(current.get("imported_count", 0) or 0)
            if probe_state.get("tripped") and imported_count > 0 and NETWORK_HINTS.search(command):
                return (
                    "External network probing paused after repeated 403/404/405 misses without new questions. "
                    "Write already-collected questions to /workspace/outbox/ (recording missing items in "
                    "collection_notes) and call scout_import_questions / scout_finish_collection."
                )
            if probe_state.get("stall_locked") and imported_count > 0 and (
                _is_idle_probe_command(command) or NETWORK_HINTS.search(command)
            ):
                recent = current.get("recent_errors") or []
                hint = (
                    " Fix the grouped rejection errors in your script first:\n" + _format_recent_errors(recent)
                    if recent
                    else ""
                )
                return (
                    f"Collection stalled with {imported_count} questions already imported. "
                    "No-op probes (echo/true/ls outbox) and further network probing are blocked. "
                    "Either run your fixed local conversion script to write valid JSON to /workspace/outbox/ "
                    "or call scout_finish_collection now." + hint
                )
    if role == "worker":
        if reason := _worker_submission_block(event):
            return reason
    return None
