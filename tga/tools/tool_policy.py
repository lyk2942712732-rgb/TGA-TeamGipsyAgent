from __future__ import annotations

import ipaddress
import os
from pathlib import Path
from urllib.parse import urlparse
from typing import Any

from pydantic import BaseModel, model_validator

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
    reason: str = "allowed"
    required_authorization: str | None = None
    retryable: bool = False

    @model_validator(mode="after")
    def synchronize_reason(self) -> "PolicyDecision":
        if self.reason == "allowed" and self.message != "allowed":
            self.reason = self.message
        elif self.message == "allowed" and self.reason != "allowed":
            self.message = self.reason
        return self

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
    risk: str | None = None,
    action: str | None = None,
    sandboxed: bool = False,
    **_: Any,
) -> PolicyDecision:
    if task is not None:
        if task.schema_version >= 3 and task.execution_policy is not None:
            return _policy_decision(
                task=task, tool=tool, target=target, risk=risk,
                action=action, sandboxed=sandboxed,
            )
        scope = task.scope
        intensity = task.intensity
        allow_active_scan = task.allow_active_scan
    scope = scope or []
    intensity = intensity or "normal"
    allow_active_scan = bool(allow_active_scan)

    normalized = normalize_tool_name(tool)
    if normalized in {"workspace.read", "workspace.write", "workspace.python", "workspace.shell", "artifact.inspect"}:
        # Version-2 tasks predate independent filesystem/process dimensions;
        # preserve their historical workspace behavior while schema v3 is
        # governed above by execution_policy.
        return PolicyDecision(allowed=True)
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
    if task is not None and task.mode == "ctf" and normalized == "http.request":
        # Legacy CTF sessions treated requests inside the exact challenge
        # scope as authorized interactions. Preserve replay behavior; schema
        # v3 routes above still separate observe/interact/state changes.
        return PolicyDecision(allowed=True)

    effective_risk = risk or classify_tool(normalized)
    if effective_risk == "passive":
        return PolicyDecision(allowed=True)
    if effective_risk == "active":
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


def _policy_decision(
    *, task: TGATask, tool: str, target: str, risk: str | None,
    action: str | None, sandboxed: bool,
) -> PolicyDecision:
    policy = task.execution_policy
    assert policy is not None
    normalized = normalize_tool_name(tool)

    def deny(code: str, reason: str, required: str) -> PolicyDecision:
        return PolicyDecision(
            allowed=False, code=code, message=reason, reason=reason,
            required_authorization=required, retryable=False,
        )

    if normalized in {"artifact.inspect", "workspace.read"}:
        return PolicyDecision(allowed=True)
    if normalized == "workspace.write":
        if policy.filesystem.mode != "workspace_write":
            return deny("FILESYSTEM_WRITE_NOT_AUTHORIZED", "workspace file writes are not authorized", "filesystem.mode=workspace_write")
        return PolicyDecision(allowed=True)
    if normalized in {"workspace.python", "workspace.shell", "workspace.binary", "process.execute"}:
        if policy.process_execution.mode == "forbidden":
            return deny("PROCESS_EXECUTION_FORBIDDEN", "process execution is forbidden", "process_execution.mode=sandbox_only|authorized_host")
        if policy.process_execution.mode == "sandbox_only" and not sandboxed:
            return deny("PROCESS_EXECUTION_REQUIRES_SANDBOX", "this execution entry point is not an approved sandbox", "sandboxed process execution")
        return PolicyDecision(allowed=True)
    if "fuzz" in normalized or normalized in {"boofuzz", "dharma"}:
        if policy.fuzzing.mode == "disabled":
            return deny("FUZZING_DISABLED", "fuzzing is disabled", "fuzzing.mode=bounded|extended")
        if policy.fuzzing.max_cases <= 0 or policy.fuzzing.max_duration_seconds <= 0 or policy.fuzzing.concurrency <= 0:
            return deny("FUZZING_BUDGET_REQUIRED", "fuzzing requires positive case, duration, and concurrency budgets", "bounded fuzzing budget")
        return PolicyDecision(allowed=True)

    parsed = urlparse(target)
    is_network = (
        parsed.scheme in {"http", "https"} or bool(parsed.hostname)
        or normalized in (PASSIVE_TOOLS | ACTIVE_TOOLS | DESTRUCTIVE_TOOLS) and normalized not in LOCAL_TARGET_TOOLS
    )
    if normalized == "http.request" or is_network:
        if policy.network.mode == "none":
            return deny("NETWORK_ACCESS_FORBIDDEN", "network access is disabled", "network.mode=observe|interact")
        if not _target_in_scope(target, policy.network.allowed_scopes, local_target=False):
            return deny("OUT_OF_SCOPE", "network target is not in execution_policy.allowed_scopes", "network.allowed_scopes")
        method = (action or "GET").upper()
        interaction = risk in {"active", "destructive"} or method not in {"GET", "HEAD"}
        if interaction and policy.network.mode != "interact":
            return deny("NETWORK_INTERACTION_NOT_AUTHORIZED", "network policy permits observation only", "network.mode=interact")
        if method in {"PUT", "PATCH", "DELETE"} or risk == "destructive":
            allowed_action = method.casefold() in {item.casefold() for item in policy.state_change.allowed_actions}
            if policy.state_change.mode != "authorized" or not allowed_action:
                return deny("STATE_CHANGE_NOT_AUTHORIZED", f"state-changing action {method} is not explicitly authorized", f"state_change.allowed_actions includes {method}")
        return PolicyDecision(allowed=True)

    classified = risk or classify_tool(normalized)
    if classified == "destructive":
        requested = action or normalized
        if policy.state_change.mode != "authorized" or requested not in policy.state_change.allowed_actions:
            return deny("STATE_CHANGE_NOT_AUTHORIZED", "destructive action is not explicitly authorized", f"state_change.allowed_actions includes {requested}")
    return PolicyDecision(allowed=True)


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
    if "*" in scope:
        return True
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
