from __future__ import annotations

import ipaddress
import os
from pathlib import Path
from urllib.parse import urlparse
from typing import Any

from pydantic import BaseModel

from tga.contracts import TGATask


PASSIVE_TOOLS = {
    "capa",
    "dnstwist",
    "gitleaks",
    "maigret",
    "mcp-scan",
    "networksdb",
    "otx",
    "searchsploit",
    "semgrep",
    "shodan",
    "trivy",
    "virustotal",
    "waybackurls",
    "whatweb",
    "yara",
    "zoomeye",
}

ACTIVE_TOOLS = {
    "binwalk",
    "burp",
    "externalattacker",
    "ffuf",
    "ghidra",
    "ida",
    "masscan",
    "nikto",
    "nmap",
    "nuclei",
    "pd-tools",
    "prowler",
    "radare2",
    "roadrecon",
    "sqlmap",
}

DESTRUCTIVE_TOOLS = {
    "bloodhound",
    "boofuzz",
    "daml-viewer",
    "dharma",
    "go-analyzer",
    "go-crash-analyzer",
    "go-fuzzer",
    "go-harness-tester",
    "hashcat",
    "medusa",
    "solazy",
}

LOCAL_TARGET_TOOLS = {
    "binwalk",
    "capa",
    "gitleaks",
    "mcp-scan",
    "semgrep",
    "trivy",
    "yara",
}


class PolicyDecision(BaseModel):
    allowed: bool
    code: str | None = None
    message: str = "allowed"
    retryable: bool = False

    def __bool__(self) -> bool:
        return self.allowed

    def __iter__(self):
        yield self.allowed
        yield self.code or ""


def is_allowed(
    *,
    tool: str,
    target: str,
    task: TGATask | None = None,
    scope: list[str] | None = None,
    intensity: str | None = None,
    allow_active_scan: bool | None = None,
    **_: Any,
) -> PolicyDecision:
    if task is not None:
        scope = task.scope
        intensity = task.intensity
        allow_active_scan = task.allow_active_scan
    scope = scope or []
    intensity = intensity or "normal"
    allow_active_scan = bool(allow_active_scan)

    normalized = normalize_tool_name(tool)
    if not scope:
        return PolicyDecision(
            allowed=False,
            code="OUT_OF_SCOPE",
            message="task scope is empty",
        )
    if not _target_in_scope(target, scope, local_target=normalized in LOCAL_TARGET_TOOLS):
        return PolicyDecision(
            allowed=False,
            code="OUT_OF_SCOPE",
            message="target is not in task scope",
        )

    risk = classify_tool(normalized)
    if risk == "passive":
        return PolicyDecision(allowed=True)
    if risk == "active":
        if intensity == "passive":
            return PolicyDecision(
                allowed=False,
                code="ACTIVE_SCAN_NOT_ALLOWED",
                message="passive intensity does not allow active tools",
            )
        if not allow_active_scan:
            return PolicyDecision(
                allowed=False,
                code="ACTIVE_SCAN_NOT_ALLOWED",
                message="active scan requires allow_active_scan=true",
            )
        return PolicyDecision(allowed=True)

    return PolicyDecision(
        allowed=False,
        code="POLICY_DISABLED",
        message="destructive or high-blast-radius tools require a future explicit policy flag",
    )


def classify_tool(tool: str) -> str:
    normalized = normalize_tool_name(tool)
    if normalized in PASSIVE_TOOLS:
        return "passive"
    if normalized in ACTIVE_TOOLS:
        return "active"
    if normalized in DESTRUCTIVE_TOOLS:
        return "destructive"
    return "active"


def normalize_tool_name(value: str) -> str:
    return value.lower().replace("_", "-").removesuffix("-mcp")


def _target_in_scope(target: str, scope: list[str], *, local_target: bool) -> bool:
    if local_target and _path_in_scope(target, scope):
        return True
    target_host = _host_or_path(target)
    for item in scope:
        if item == "*":
            return True
        if _path_in_scope(target, [item]):
            return True
        if _host_matches(target_host, item):
            return True
    return False


def _path_in_scope(target: str, scope: list[str]) -> bool:
    if not target:
        return False
    parsed = urlparse(target)
    is_windows_drive = len(parsed.scheme) == 1 and target[1:3] in {":\\", ":/"}
    if parsed.scheme and parsed.scheme != "file" and not is_windows_drive:
        return False
    try:
        target_path = Path(parsed.path if parsed.scheme == "file" else target).expanduser().resolve()
    except (OSError, RuntimeError):
        return False
    for item in scope:
        try:
            scope_path = Path(item).expanduser().resolve()
        except (OSError, RuntimeError):
            continue
        if os.path.commonpath([str(target_path), str(scope_path)]) == str(scope_path):
            return True
    return False


def _host_or_path(target: str) -> str:
    parsed = urlparse(target)
    if len(parsed.scheme) == 1 and target[1:3] in {":\\", ":/"}:
        return target
    if parsed.hostname:
        return f"{parsed.hostname}:{parsed.port}" if parsed.port else parsed.hostname
    return target.strip("/")


def _host_matches(target: str, scope_item: str) -> bool:
    parsed = urlparse(scope_item)
    scope_host = f"{parsed.hostname}:{parsed.port}" if parsed.hostname and parsed.port else parsed.hostname
    candidate = scope_host or scope_item.strip("/")
    if target == candidate:
        return True
    target_without_port = target.split(":", 1)[0]
    candidate_without_port = candidate.split(":", 1)[0]
    if target_without_port == candidate_without_port and ":" not in candidate:
        return True
    try:
        network = ipaddress.ip_network(candidate, strict=False)
        return ipaddress.ip_address(target_without_port) in network
    except ValueError:
        return False
