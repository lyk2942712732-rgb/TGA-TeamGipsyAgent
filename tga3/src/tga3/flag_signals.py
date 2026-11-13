"""Cheap closing-window signal, not a correctness or platform-acceptance check."""

import json
import re
from pathlib import Path

FLAG = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,39}\{[^{}\r\n]{1,4096}\}")


def flag_pattern(flag_format=""):
    """Treat a declared format as a literal envelope, never executable regex.

    Unknown formats disable automatic detection instead of guessing another prefix.
    Missing formats preserve the historical heuristic. This is not proof of correctness.
    """
    if not isinstance(flag_format, str) or not flag_format.strip():
        return FLAG
    match = re.search(r"([A-Za-z][A-Za-z0-9_.-]{0,79})\{([^{}\r\n]{0,200})\}", flag_format)
    if not match:
        return re.compile(r"(?!)")
    prefix, content = match.groups()
    payload = {
        "uuid": r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}",
        "md5": r"[0-9a-fA-F]{32}",
        "sha256": r"[0-9a-fA-F]{64}",
    }.get(content.strip().lower(), r"[^{}\r\n]{1,4096}")
    return re.compile(re.escape(prefix) + r"\{" + payload + r"\}")


def declared_format(document):
    if not isinstance(document, dict):
        return ""
    for key in ("flag_format", "flagFormat"):
        value = document.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    # Only a labelled declaration in the original description, not arbitrary flags.
    description = document.get("description", "")
    if isinstance(description, str):
        match = re.search(r"(?:flag\s*(?:format|格式)|flag格式)\s*[:：]\s*`?([^`\r\n]+)",
                          description, re.I)
        if match:
            return match[1].strip()
    return ""


def question_flag_format(config, question):
    value = declared_format(question.metadata)
    if value:
        return value
    # Original list records also cover tasks registered before flag_format existed.
    return file_flag_format(config.task_path(question.id) / "source.json")


def file_flag_format(path):
    try:
        return declared_format(json.loads(Path(path).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return ""
