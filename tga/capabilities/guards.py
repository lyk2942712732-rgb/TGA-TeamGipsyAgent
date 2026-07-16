from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

from tga.tools.tool_policy import _target_in_scope


SECRET_HEADER_RE = re.compile(r"(authorization|cookie|token|key|secret)", re.IGNORECASE)


def target_in_scope(target: str, scope: list[str], *, local_target: bool = False) -> bool:
    return _target_in_scope(target, scope, local_target=local_target)


def redirect_in_scope(base_url: str, location: str, scope: list[str]) -> tuple[bool, str]:
    redirected = urljoin(base_url, location)
    return target_in_scope(redirected, scope), redirected


def local_path_in_roots(path: str | Path, roots: list[str | Path]) -> bool:
    try:
        resolved = Path(path).expanduser().resolve()
    except (OSError, RuntimeError):
        return False
    for root in roots:
        try:
            resolved_root = Path(root).expanduser().resolve()
            if os.path.commonpath([str(resolved), str(resolved_root)]) == str(resolved_root):
                return True
        except (OSError, RuntimeError, ValueError):
            continue
    return False


def safe_join(root: Path, *parts: str) -> Path:
    candidate = root.joinpath(*parts).resolve()
    root_resolved = root.resolve()
    if os.path.commonpath([str(candidate), str(root_resolved)]) != str(root_resolved):
        raise ValueError("WORKSPACE_PATH_DENIED")
    return candidate


def local_scope_roots(scope: list[str]) -> list[Path]:
    roots: list[Path] = []
    for item in scope:
        parsed = urlparse(item)
        is_windows_drive = len(parsed.scheme) == 1 and item[1:3] in {":\\", ":/"}
        if parsed.scheme and parsed.scheme != "file" and not is_windows_drive:
            continue
        try:
            roots.append(Path(parsed.path if parsed.scheme == "file" else item).expanduser().resolve())
        except (OSError, RuntimeError):
            continue
    return roots


def truncate_text(value: str, max_bytes: int) -> tuple[str, bool]:
    data = value.encode("utf-8", errors="replace")
    if len(data) <= max_bytes:
        return value, False
    truncated = data[:max_bytes].decode("utf-8", errors="replace")
    return truncated, True


def redact_headers(headers: dict[str, str]) -> dict[str, str]:
    redacted: dict[str, str] = {}
    for key, value in headers.items():
        redacted[key] = "<redacted>" if SECRET_HEADER_RE.search(key) else value
    return redacted
