"""Local AOE read bootstrap and Scout tool boundary; no control-plane models/imports."""

import json
import os
import re
import shlex
import threading
from pathlib import Path, PurePosixPath


def inputs(event):
    value = event.get("tool_input", {})
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            value = {}
    return value if isinstance(value, dict) else {}


def read_paths(event):
    tool = event.get("tool_name", "").lower()
    args = inputs(event)
    if tool in {"read", "read_file"}:
        # A partial read does not count as consuming the full overview.
        return [args.get("file_path") or args.get('filePath') or args.get('absolute_path', '')] \
            if args.get("offset", 1) in {None, 0, 1} and not args.get("limit") else []
    if tool not in {"bash", "exec_command", "shell", "shell_command", "command_execution", "run_shell_command"}:
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


_read_lock = threading.RLock()


def overview_policy(event):
    # Native tools can finish in parallel; their acknowledgements must be unioned.
    with _read_lock:
        return _overview_policy(event)


def _overview_policy(event):
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
    if name == "PostToolUse":
        response = event.get("tool_response", "")
        output = json.dumps(response, ensure_ascii=False) if not isinstance(response, str) else response
        failed = isinstance(response, dict) and (
            response.get("isError")
            or response.get("is_error")
            or response.get("interrupted")
            or response.get("exit_code", 0)
            or response.get("truncated") is True
        )
        file = response.get('file', {}) if isinstance(response, dict) else {}
        partial = (isinstance(file, dict) and file.get('totalLines', 0) > file.get('numLines', float('inf')))
        if (
            paths
            and not event.get("is_error")
            and not failed
            and not partial
            and not re.search(r"Output truncated|truncated output|tokens truncated|No such file|"
                              r"not found|Permission denied|Exit code: [1-9]", output, re.I)
        ):
            seen.update(required.intersection(paths))
            state.parent.mkdir(parents=True, exist_ok=True)
            state.write_text(json.dumps({"context": context, "paths": sorted(seen)}))
        return None
    if name != "PreToolUse":
        return None
    if role == "scout":
        manifest = Path(os.environ.get("TGA3_AOE_ROOT", "/aoe")) / "questions.json"
        if manifest.is_file() and json.loads(manifest.read_text()).get("collection_complete"):
            return "AOE question collection is complete. Stop immediately; no further tools or exploration."
    safe_paths = bool(paths) and all(
        ".." not in PurePosixPath(p).parts and (p in required or role == "scout" and p.startswith("/inputs/"))
        for p in paths
    )
    if role == "scout":
        tool = event.get("tool_name", "").split("__")[-1].split(".")[-1]
        allowed_tool = tool in {"scout_collect_questions", "scout_finish_collection"}
        if not safe_paths and not allowed_tool:
            return (
                "AOE Scout may only read supplied overview/input documents and use scout_collect_questions. "
                "No shell networking, endpoint probing, attachment exploration, browser or tool installation."
            )
    if not required.issubset(seen) and not safe_paths:
        return (
            "Before working, read /task-overview/scene.md and /task-overview/task.md separately with native Read "
            "or cat (Codex max_output_tokens=12000). Do not obtain these texts from the question blackboard. "
            "If output was truncated, repeat a complete read with a larger output allowance."
        )
    return None
