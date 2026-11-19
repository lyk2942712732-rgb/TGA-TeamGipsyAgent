"""Local AOE read bootstrap and lightweight Scout anti-enumeration guard; no control-plane models/imports."""

from __future__ import annotations

import json
import os
import re
import shlex
import threading
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
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (OSError, ValueError):
            pass
    return {"imported_count": 0, "consecutive_misses": 0, "tripped": False}


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


_read_lock = threading.RLock()


def overview_policy(event: dict) -> str | None:
    # Native tools can finish in parallel; their acknowledgements must be unioned.
    with _read_lock:
        return _overview_policy(event)


def scout_post_tool_notice(event: dict) -> str | None:
    """Return an optional non-blocking guidance note for AOE Scout after PostToolUse."""
    if os.environ.get("TGA3_AGENT_ROLE") != "scout" or not os.environ.get("TGA3_AOE_PARENT_ID"):
        return None
    if event.get("hook_event_name") not in {"PostToolUse", "PostToolUseFailure"}:
        return None
    with _read_lock:
        state = _load_probe_state()
        max_misses = _max_probe_misses()
        if state.get("tripped"):
            if state.get("imported_count", 0) > 0:
                return (
                    "Probe guard tripped after repeated 403/404/405 responses without new questions. "
                    "Stop guessing undocumented paths or attachments; write remaining collected questions "
                    "to /workspace/outbox/ (with collection_notes explaining missing items) and call "
                    "scout_finish_collection."
                )
            return (
                "Multiple 403/404/405 responses detected. Stop guessing undocumented paths; re-check "
                "/task-overview/task.md and /inputs/ for the exact supplied entry points."
            )
        if state.get("consecutive_misses", 0) >= max(2, max_misses - 2):
            return (
                f"Detected {state['consecutive_misses']} consecutive 403/404/405 probe responses without new "
                "questions. Do not brute-force attachment or endpoint URLs; if an attachment is not referenced "
                "in the response (e.g., only inside a dynamic target), leave attachments empty, document it in "
                "collection_notes, and move on."
            )
    return None


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
        _save_probe_state(state)
    else:
        state["imported_count"] = imported_count
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
    return None
