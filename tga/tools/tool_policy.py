from __future__ import annotations

import ipaddress
import os
from pathlib import Path
from urllib.parse import urlparse
from typing import Any

from pydantic import BaseModel, model_validator

from tga.contracts import TGATask
from tga.network_policy import authorize_url, enforce_address_policy


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
    risk: str | None = None,
    action: str | None = None,
    sandboxed: bool = False,
    approved: bool = False,
    **_: Any,
) -> PolicyDecision:
    if task is not None:
        return _policy_decision(
            task=task, tool=tool, target=target, risk=risk,
            action=action, sandboxed=sandboxed, approved=approved,
        )

    scope = scope or []
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
    effective_risk = risk or classify_tool(normalized)
    if effective_risk == "passive":
        return PolicyDecision(allowed=True)
    return PolicyDecision(
        allowed=False,
        code="POLICY_DISABLED",
        message="active or destructive tools require a task ExecutionPolicy",
    )


def _policy_decision(
    *, task: TGATask, tool: str, target: str, risk: str | None,
    action: str | None, sandboxed: bool, approved: bool,
) -> PolicyDecision:
    policy = task.execution_policy
    assert policy is not None
    normalized = normalize_tool_name(tool)

    def deny(code: str, reason: str, required: str) -> PolicyDecision:
        return PolicyDecision(
            allowed=False, code=code, message=reason, reason=reason,
            required_authorization=required, retryable=False,
        )

    if normalized in {"kali.exec", "kali.session"}:
        if policy.local_compute.mode == "disabled":
            return deny("LOCAL_COMPUTE_DISABLED", "Kali execution is disabled", "local_compute.mode=isolated")
        if not sandboxed:
            return deny("ISOLATED_COMPUTE_REQUIRED", "Kali capability requires the isolated sandbox backend", "isolated local compute")
        return PolicyDecision(allowed=True)

    parsed = urlparse(target)
    effective_risk = risk or classify_tool(normalized)
    is_network = (
        parsed.scheme in {"http", "https"} or bool(parsed.hostname)
        or normalized in (PASSIVE_TOOLS | ACTIVE_TOOLS | DESTRUCTIVE_TOOLS) and normalized not in LOCAL_TARGET_TOOLS
    )
    if is_network:
        try:
            # Planning policy validates the declared origin and literal IP.
            # HTTP execution performs DNS and redirect revalidation immediately
            # before I/O, avoiding stale DNS answers and test-only resolution.
            authorize_url(target, policy.network, resolve_dns=False)
        except (PermissionError, ValueError) as exc:
            return deny(str(exc), "network target is outside the authorized access boundary", "network access policy")
        method = (action or "GET").upper()
        interaction = effective_risk in {"active", "destructive"} or method not in {"GET", "HEAD"}
        if interaction and policy.network.interaction != "interact":
            return deny("NETWORK_INTERACTION_NOT_AUTHORIZED", "network policy permits observation only", "network.interaction=interact")

    high_impact = (
        effective_risk == "destructive"
        or "fuzz" in normalized
        or normalized in {"boofuzz", "dharma"}
        or normalized in {"artifact.publish", "input.materialize"}
    )
    if high_impact:
        requested = action or normalized
        if approved:
            return PolicyDecision(allowed=True)
        if policy.high_impact.mode == "forbidden":
            return deny("HIGH_IMPACT_FORBIDDEN", "high-impact action is forbidden", "high_impact.mode")
        if policy.high_impact.mode == "approval_required":
            return deny("APPROVAL_REQUIRED", "high-impact action requires user approval", "user approval")
        allowed = {item.casefold() for item in policy.high_impact.allowed_actions}
        if requested.casefold() not in allowed and normalized.casefold() not in allowed:
            return deny("HIGH_IMPACT_NOT_ALLOWLISTED", "high-impact action is not allowlisted", "high_impact.allowed_actions")
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
