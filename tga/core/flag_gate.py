"""CTF flag provenance gate."""

from __future__ import annotations

import re


_PLACEHOLDER_BODIES = {
    "...",
    "flag",
    "the flag",
    "your_flag",
    "your flag",
    "your_flag_here",
    "flag_here",
    "placeholder",
    "redacted",
    "todo",
    "tbd",
}


def is_placeholder_flag(flag: str) -> bool:
    value = flag.strip()
    if not value:
        return True
    match = re.search(r"\{([^}]*)\}", value)
    if not match:
        return False
    body = match.group(1).strip().strip("'\"`<>").lower()
    if not body:
        return True
    if body in _PLACEHOLDER_BODIES:
        return True
    if "..." in body:
        return True
    return False


def flag_ok(
    flag: str,
    *,
    flag_format: str,
    raw_output: str = "",
    artifact_texts: list[str] | None = None,
) -> bool:
    if not flag_format or not re.fullmatch(flag_format, flag):
        return False
    if is_placeholder_flag(flag):
        return False
    if flag in raw_output:
        return True
    return any(flag in text for text in artifact_texts or [])

