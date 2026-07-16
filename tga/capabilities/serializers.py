from __future__ import annotations

import re
from typing import Any


SENSITIVE_HEADER_NAMES = {"authorization", "cookie", "set-cookie", "proxy-authorization", "x-api-key"}
SENSITIVE_VALUE = re.compile(r"(?i)(authorization|cookie|token|secret|api[_-]?key|password)\s*[:=]\s*[^\s,;]+")


def redact_headers(headers: dict[str, Any]) -> dict[str, str]:
    return {
        str(key): "[REDACTED]" if str(key).lower() in SENSITIVE_HEADER_NAMES else str(value)[:2000]
        for key, value in headers.items()
    }


def redact_text(value: str, limit: int = 4000) -> str:
    return SENSITIVE_VALUE.sub(lambda match: match.group(1) + "=[REDACTED]", value)[:limit]


def output_excerpt(raw: bytes, limit: int = 262_144) -> tuple[str, bool]:
    truncated = len(raw) > limit
    return raw[:limit].decode("utf-8", errors="replace"), truncated
