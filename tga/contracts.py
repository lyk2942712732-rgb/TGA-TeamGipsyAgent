"""Shared cross-module contracts for TGA Week 1.

All teams should import these models instead of redefining task, intent,
artifact, finding, or worker-result shapes locally.
"""

from __future__ import annotations

import hashlib
import re
import ipaddress
from pathlib import Path
from typing import Annotated, Any, Literal, Union
from urllib.parse import urlparse

from pydantic import BaseModel, Field, model_validator

from tga.modes import TaskMode, normalize_mode


ResourceRole = Literal["target", "hint"]
ResourceKind = Literal[
    "url", "network", "file", "files", "directory", "repository", "archive",
    "image", "text", "artifact", "mcp_resource", "mcp_tool",
]
IntentKind = Literal["recon", "verify", "exploit_ctf", "code_scan", "report"]
IntentStatus = Literal["pending", "running", "done", "failed", "blocked"]
FindingStatus = Literal["candidate", "confirmed", "rejected"]
Severity = Literal["info", "low", "medium", "high", "critical"]
ArtifactKind = Literal["stdout", "stderr", "tool_output", "http_response", "http_body", "file", "report"]
WorkerStatus = Literal["ok", "failed", "blocked"]
RiskLevel = Literal["passive", "active", "destructive"]
DecisionPhase = Literal["planning", "execution", "adaptation", "gate"]
SessionStatus = Literal["created", "running", "paused", "awaiting_approval", "blocked", "completed", "failed", "cancelled"]
SolverStatus = Literal["starting", "running", "waiting", "completed", "failed", "cancelled"]
SolverRole = Literal["recon", "targeted", "research", "main"]
ChallengeStatus = Literal["unknown", "active", "solved", "blocked", "expired"]
MemoryKind = Literal["fact", "evidence", "failure_boundary", "hint", "constraint", "decision"]
ActionKind = Literal["http", "tool", "workspace", "browser"]
ActionStatus = Literal["proposed", "pending_approval", "approved", "running", "succeeded", "failed", "blocked", "cancelled", "rejected"]
StrategyStatus = Literal["pending", "testing", "succeeded", "failed", "blocked"]
ExtractionStatus = Literal["not_requested", "blocked_out_of_scope", "failed", "extracted"]


class TGAError(BaseModel):
    code: str
    message: str
    retryable: bool = False


class ResourceProvenance(BaseModel):
    model_config = {"extra": "forbid"}

    source: Literal["user_upload", "manual", "mcp", "generated"] = "manual"
    created_at: str | None = None
    original_name: str | None = Field(default=None, max_length=255)
    parent_input_id: str | None = None


class ResourceRef(BaseModel):
    """Stable reference to untrusted task input; presence never grants authority."""

    model_config = {"extra": "forbid"}

    id: str = Field(pattern=r"^(?:input|hint)_[A-Za-z0-9_-]{1,64}$")
    role: ResourceRole
    kind: ResourceKind
    label: str = Field(min_length=1, max_length=255)
    uri: str | None = Field(default=None, max_length=2048)
    mime_type: str | None = Field(default=None, max_length=255)
    size: int | None = Field(default=None, ge=0)
    sha256: str | None = Field(default=None, pattern=r"^[a-fA-F0-9]{64}$")
    provenance: ResourceProvenance = Field(default_factory=ResourceProvenance)
    status: Literal["available", "pending", "failed", "missing"] = "available"
    metadata: dict[str, Any] = Field(default_factory=dict)
    summary: str = Field(default="", max_length=2000)
    text: str | None = Field(default=None, max_length=16_384)
    url: str | None = Field(default=None, max_length=2048)
    server_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")
    resource_uri: str | None = Field(default=None, max_length=2048)
    tool_name: str | None = Field(default=None, max_length=128)
    arguments: dict[str, Any] = Field(default_factory=dict)
    artifact_id: str | None = Field(default=None, pattern=r"^artifact_[a-f0-9]{12}$")

    @model_validator(mode="after")
    def validate_kind_fields(self) -> "ResourceRef":
        if self.role == "target" and not self.id.startswith("input_"):
            raise ValueError("target resource ids must start with input_")
        if self.role == "hint" and not self.id.startswith("hint_"):
            raise ValueError("hint resource ids must start with hint_")
        if self.kind == "url":
            value = self.url or self.uri or ""
            parsed = urlparse(value)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError("url resources require an absolute HTTP(S) URL")
            self.url = value
            self.uri = self.uri or value
        elif self.kind in {"mcp_resource", "mcp_tool"}:
            if not self.server_id:
                raise ValueError(f"{self.kind} requires server_id")
            if self.kind == "mcp_resource" and not self.resource_uri:
                raise ValueError("mcp_resource requires resource_uri")
            if self.kind == "mcp_tool" and not self.tool_name:
                raise ValueError("mcp_tool requires tool_name")
        elif self.kind == "artifact" and not self.artifact_id:
            raise ValueError("artifact resources require artifact_id")
        elif self.kind == "text" and not (self.text or self.uri):
            raise ValueError("text resources require inline text or a persisted uri")
        if self.kind in {"file", "files", "archive", "image"}:
            if not (self.uri or "").startswith(("input://", "upload://")):
                raise ValueError(f"{self.kind} resources must use task-owned input:// or staged upload:// storage")
        return self

    def retrieval(self) -> str:
        if self.kind == "image":
            return "input_view"
        if self.kind in {"text", "file", "files", "archive", "directory", "repository", "mcp_resource"}:
            return "input_read"
        return "input_get"

    def manifest_item(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "role": self.role,
            "kind": self.kind,
            "label": self.label,
            "uri": self.uri,
            "mime_type": self.mime_type,
            "size": self.size,
            "sha256": self.sha256,
            "summary": self.summary,
            "status": self.status,
            "retrieval": self.retrieval(),
            "provenance": self.provenance.model_dump(mode="json"),
            "server_id": self.server_id,
            "resource_uri": self.resource_uri,
            "tool_name": self.tool_name,
            "artifact_id": self.artifact_id,
        }


SessionFileKind = Literal["task_input"]
MediaKind = Literal["image", "text", "document", "archive", "binary", "other"]


class SessionFile(BaseModel):
    """Immutable file owned by one Session workspace."""

    model_config = {"extra": "forbid", "populate_by_name": True}

    id: str = Field(pattern=r"^asset_[a-f0-9]{16,64}$")
    original_name: str = Field(alias="originalName", min_length=1, max_length=255)
    stored_name: str = Field(alias="storedName", pattern=r"^[a-f0-9]{32,64}(?:\.[A-Za-z0-9]{1,16})?$")
    relative_path: str = Field(alias="relativePath", pattern=r"^inputs/files/[a-f0-9]{32,64}(?:\.[A-Za-z0-9]{1,16})?$")
    mime_type: str = Field(alias="mimeType", min_length=1, max_length=255)
    size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    kind: SessionFileKind
    media_kind: MediaKind = Field(alias="mediaKind")
    provenance: ResourceProvenance

    @property
    def container_path(self) -> str:
        return f"/workspace/{self.relative_path}"

    def manifest_item(self) -> dict[str, Any]:
        return {
            **self.model_dump(mode="json", by_alias=False),
            "container_path": self.container_path,
            "purpose": "task input",
        }


class SessionInput(BaseModel):
    model_config = {"extra": "forbid"}

    prompt: str = Field(default="", max_length=16_384)
    files: list[SessionFile] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def validate_files(self) -> "SessionInput":
        if any(item.kind != "task_input" for item in self.files):
            raise ValueError("session_input.files may contain only task_input files")
        ids = [item.id for item in self.files]
        if len(ids) != len(set(ids)):
            raise ValueError("Session input file ids must be unique")
        self.prompt = self.prompt.strip()
        return self


class MCPCapabilityTool(BaseModel):
    model_config = {"extra": "forbid"}

    provider_name: str
    server_id: str
    method: str
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)


class MCPCapabilitySnapshot(BaseModel):
    """Creation-time audit snapshot, never a user-editable ACL."""

    model_config = {"extra": "forbid"}

    catalog_version: str = "mcp_empty"
    server_ids: list[str] = Field(default_factory=list)
    tools: list[MCPCapabilityTool] = Field(default_factory=list)
    created_at: str | None = None


class NetworkExecutionPolicy(BaseModel):
    model_config = {"extra": "forbid"}

    access: Literal["disabled", "task_sources", "public_internet", "custom"] = "disabled"
    interaction: Literal["observe", "interact"] = "observe"
    seed_origins: list[str] = Field(default_factory=list, max_length=128)
    custom_origins: list[str] = Field(default_factory=list, max_length=128)
    custom_domains: list[str] = Field(default_factory=list, max_length=128)
    custom_cidrs: list[str] = Field(default_factory=list, max_length=128)
    deny_private_networks: bool = True
    deny_loopback: bool = True
    deny_link_local: bool = True
    deny_cloud_metadata: bool = True
    rate_limit_per_minute: int = Field(default=30, ge=1, le=100_000)
    concurrency: int = Field(default=2, ge=1, le=128)
    request_timeout_seconds: int = Field(default=30, ge=1, le=300)


class LocalComputeExecutionPolicy(BaseModel):
    model_config = {"extra": "forbid"}

    mode: Literal["disabled", "isolated"] = "disabled"
    timeout_seconds: int = Field(default=120, ge=1, le=3600)
    concurrency: int = Field(default=2, ge=1, le=128)
    network_inheritance: Literal["task_network_policy"] = "task_network_policy"


class HighImpactExecutionPolicy(BaseModel):
    model_config = {"extra": "forbid"}

    mode: Literal["forbidden", "approval_required", "allowlisted"] = "forbidden"
    allowed_actions: list[str] = Field(default_factory=list, max_length=64)


class ExecutionPolicy(BaseModel):
    model_config = {"extra": "forbid"}

    preset: Literal["autonomous_ctf", "safe_observation", "offline_analysis", "custom"] = "offline_analysis"
    network: NetworkExecutionPolicy = Field(default_factory=NetworkExecutionPolicy)
    local_compute: LocalComputeExecutionPolicy = Field(default_factory=LocalComputeExecutionPolicy)
    high_impact: HighImpactExecutionPolicy = Field(default_factory=HighImpactExecutionPolicy)


class ModelSnapshot(BaseModel):
    model_config = {"extra": "forbid"}

    provider: str = Field(default="openai-compatible", max_length=128)
    model: str = Field(min_length=1, max_length=255)
    capability_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    verification_id: str = Field(min_length=1, max_length=100)
    verified_at: str = Field(min_length=1, max_length=80)
    capabilities: dict[str, bool | None] = Field(default_factory=dict)
    max_output_tokens: int = Field(ge=256, le=16_384)
    timeout_seconds: int = Field(ge=5, le=300)
    temperature: float = Field(ge=0, le=2)
    reasoning_mode: Literal["auto", "enabled", "disabled"] = "auto"


class CtfVerifier(BaseModel):
    model_config = {"extra": "forbid"}

    kind: Literal["local_regex", "platform_tool", "mcp_api", "proof"] = "local_regex"
    tool_ref: str | None = None


class CtfModeConfig(BaseModel):
    model_config = {"extra": "forbid"}

    mode: Literal["ctf"] = "ctf"
    subtype: Literal["web", "pwn", "reverse", "crypto", "misc", "forensics", "auto", "unknown"] = "auto"
    flag_format: str | None = r"[A-Za-z0-9_]{2,32}\{[^{}\s]{4,200}\}"
    expected_flag_count: int | None = Field(default=1, ge=1, le=128)
    verifier: CtfVerifier = Field(default_factory=CtfVerifier)
    deadline: str | None = None
    alternative_proof: str | None = Field(default=None, max_length=1000)


class PenetrationTestModeConfig(BaseModel):
    model_config = {"extra": "forbid"}

    mode: Literal["penetration_test"] = "penetration_test"
    depth: Literal["reconnaissance", "validation", "comprehensive"] = "reconnaissance"
    included_scopes: list[str] = Field(default_factory=list, max_length=128)
    exclusions: list[str] = Field(default_factory=list, max_length=128)
    testing_window: str | None = Field(default=None, max_length=500)
    credentials_ref: str | None = Field(default=None, max_length=128)
    rules_of_engagement: str = Field(default="", max_length=4000)
    allowed_techniques: list[str] = Field(default_factory=list, max_length=128)
    prohibited_techniques: list[str] = Field(default_factory=list, max_length=128)
    authenticated_testing: bool = False
    exploit_validation: bool = False
    state_change_requested: bool = False
    data_retention: str = Field(default="", max_length=1000)
    report_requirements: list[str] = Field(default_factory=list, max_length=64)


class IncidentResponseModeConfig(BaseModel):
    model_config = {"extra": "forbid"}

    mode: Literal["incident_response"] = "incident_response"
    phase: Literal["triage", "investigation", "containment", "eradication", "recovery", "post-incident"] = "triage"
    response_authority: Literal["analysis_only", "containment_with_approval", "authorized_containment"] = "analysis_only"
    time_range: str | None = Field(default=None, max_length=500)
    timezone: str = Field(default="UTC", max_length=80)
    affected_assets: list[str] = Field(default_factory=list, max_length=256)
    known_iocs: list[str] = Field(default_factory=list, max_length=512)
    evidence_preservation: str = Field(default="Preserve originals and provenance.", max_length=2000)
    allow_live_queries: bool = False
    approval_required_actions: list[str] = Field(default_factory=list, max_length=64)


class VulnerabilityResearchModeConfig(BaseModel):
    model_config = {"extra": "forbid"}

    mode: Literal["vulnerability_research"] = "vulnerability_research"
    depth: Literal["triage", "focused", "deep"] = "triage"
    software_version: str = Field(default="", max_length=500)
    commit: str = Field(default="", max_length=128)
    build_info: str = Field(default="", max_length=2000)
    vulnerability_classes: list[str] = Field(default_factory=list, max_length=128)
    build_environment: str = Field(default="", max_length=2000)
    allow_target_execution: bool = False
    require_sandbox: bool = True
    allow_fuzzing: bool = False
    require_poc: bool = False
    require_minimized_crash: bool = False
    disclosure_constraints: str = Field(default="", max_length=2000)


class ReverseAnalysisModeConfig(BaseModel):
    model_config = {"extra": "forbid"}

    mode: Literal["reverse_engineering"] = "reverse_engineering"
    analysis_method: Literal["static_only", "static_and_dynamic", "deep_instrumentation"] = "static_only"
    sample_type: str = Field(default="auto", max_length=128)
    platform: str = Field(default="auto", max_length=128)
    architecture: str = Field(default="auto", max_length=128)
    known_context: list[str] = Field(default_factory=list, max_length=128)
    analysis_goals: list[str] = Field(default_factory=list, max_length=64)
    allow_dynamic_execution: bool = False
    require_sandbox: bool = True
    allow_network: bool = False
    allow_instrumentation: bool = False
    expected_outputs: list[str] = Field(default_factory=list, max_length=64)


ModeConfig = Annotated[
    Union[
        CtfModeConfig,
        PenetrationTestModeConfig,
        IncidentResponseModeConfig,
        VulnerabilityResearchModeConfig,
        ReverseAnalysisModeConfig,
    ],
    Field(discriminator="mode"),
]


def default_mode_config(mode: TaskMode, *, flag_format: str | None = None) -> ModeConfig:
    if mode == "ctf":
        return CtfModeConfig(flag_format=flag_format or CtfModeConfig().flag_format)
    if mode == "penetration_test":
        return PenetrationTestModeConfig()
    if mode == "incident_response":
        return IncidentResponseModeConfig()
    if mode == "vulnerability_research":
        return VulnerabilityResearchModeConfig()
    return ReverseAnalysisModeConfig()


class TGATask(BaseModel):
    model_config = {"extra": "forbid"}

    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    name: str = Field(min_length=1, max_length=255)
    mode: TaskMode
    session_input: SessionInput = Field(default_factory=SessionInput)
    task_entry_url: str | None = Field(default=None, max_length=2048)
    mcp_capabilities: MCPCapabilitySnapshot = Field(default_factory=MCPCapabilitySnapshot)
    goal: str = Field(min_length=1, max_length=8000)
    flag_format: str | None = None
    mode_config: ModeConfig | None = None
    execution_policy: ExecutionPolicy | None = None
    model_snapshot: ModelSnapshot | None = None
    execution_budget: dict[str, int] = Field(default_factory=dict)
    # A CTF platform can occasionally use an incomplete/self-signed chain.
    # This is never a global TLS switch: every exception is an exact HTTPS
    # origin that must already be inside this task's authorization scope.
    insecure_tls_origins: list[str] = Field(default_factory=list, max_length=8)
    schema_version: int = 5

    @model_validator(mode="before")
    @classmethod
    def apply_current_defaults(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        current = dict(value)
        if "mode" in current:
            current["mode"] = normalize_mode(current["mode"])
        mode = current.get("mode") or "ctf"
        if not current.get("mode_config"):
            current["mode_config"] = default_mode_config(
                mode, flag_format=current.get("flag_format")
            ).model_dump(mode="json")
        if not current.get("execution_policy"):
            current["execution_policy"] = ExecutionPolicy().model_dump(mode="json")
        if mode != "ctf":
            current["flag_format"] = None
        return current

    @model_validator(mode="after")
    def validate_authorized_scope(self) -> "TGATask":
        if self.mode_config is None or self.mode_config.mode != self.mode:
            raise ValueError("mode_config discriminator must match task mode")
        if self.execution_policy is None:
            raise ValueError("execution_policy is required")
        self.execution_policy.network.seed_origins = _canonical_http_origins(
            self.execution_policy.network.seed_origins,
            field_name="seed_origins",
        )
        self.execution_policy.network.custom_origins = _canonical_http_origins(
            self.execution_policy.network.custom_origins,
            field_name="custom_origins",
        )
        domains: list[str] = []
        for value in self.execution_policy.network.custom_domains:
            domain = value.strip().casefold().rstrip(".")
            candidate = domain[2:] if domain.startswith("*.") else domain
            if not candidate or "://" in domain or "/" in domain or " " in domain:
                raise ValueError(f"invalid custom domain rule: {value}")
            if domain not in domains:
                domains.append(domain)
        self.execution_policy.network.custom_domains = domains
        cidrs: list[str] = []
        for value in self.execution_policy.network.custom_cidrs:
            try:
                cidr = str(ipaddress.ip_network(value.strip(), strict=False))
            except ValueError as exc:
                raise ValueError(f"invalid custom CIDR rule: {value}") from exc
            if cidr not in cidrs:
                cidrs.append(cidr)
        self.execution_policy.network.custom_cidrs = cidrs
        if self.task_entry_url:
            parsed_entry = urlparse(self.task_entry_url)
            if parsed_entry.scheme not in {"http", "https"} or not parsed_entry.netloc or parsed_entry.username or parsed_entry.password:
                raise ValueError("task_entry_url must be an absolute HTTP(S) URL without credentials")
        if self.flag_format:
            if len(self.flag_format) > 256:
                raise ValueError("flag_format exceeds 256 characters")
            try:
                re.compile(self.flag_format)
            except re.error as exc:
                raise ValueError(f"invalid flag_format: {exc}") from exc
        if self.mode == "ctf" and isinstance(self.mode_config, CtfModeConfig):
            self.flag_format = self.mode_config.flag_format
        target_origin = _https_origin(self.task_entry_url or "")
        canonical_origins: list[str] = []
        for value in self.insecure_tls_origins:
            origin = _https_origin(value)
            if origin is None or origin != target_origin:
                raise ValueError("insecure_tls_origins may contain only the exact HTTPS target origin")
            if origin not in canonical_origins:
                canonical_origins.append(origin)
        self.insecure_tls_origins = canonical_origins
        return self

    def input_manifest(self) -> dict[str, Any]:
        return {
            "task_goal": self.goal,
            "prompt": self.session_input.prompt,
            "files": [item.manifest_item() for item in self.session_input.files],
            "task_entry_url": self.task_entry_url,
        }

    def default_action_target(self) -> str:
        return self.task_entry_url or self.id


def _https_origin(value: str) -> str | None:
    parsed = urlparse(value.strip())
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        return None
    if parsed.path not in {"", "/"} or parsed.params or parsed.query or parsed.fragment:
        return None
    host = parsed.hostname.lower()
    port = parsed.port
    return f"https://{host}" if port in {None, 443} else f"https://{host}:{port}"


def _canonical_http_origins(values: list[str], *, field_name: str) -> list[str]:
    origins: list[str] = []
    for value in values:
        parsed = urlparse(value.strip())
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path not in {"", "/"}
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(f"{field_name} must contain absolute HTTP(S) origins without credentials or paths")
        host = parsed.hostname.lower()
        default_port = 80 if parsed.scheme == "http" else 443
        origin = f"{parsed.scheme.lower()}://{host}" if parsed.port in {None, default_port} else f"{parsed.scheme.lower()}://{host}:{parsed.port}"
        if origin not in origins:
            origins.append(origin)
    return origins


class Intent(BaseModel):
    id: str
    task_id: str
    kind: IntentKind
    target: str
    goal: str
    required_tools: list[str] = Field(default_factory=list)
    risk: RiskLevel = "passive"
    status: IntentStatus = "pending"


class ArtifactRecord(BaseModel):
    id: str
    task_id: str
    intent_id: str | None = None
    kind: ArtifactKind
    path: str
    sha256: str
    tool: str | None = None
    target: str | None = None
    input_id: str | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class Finding(BaseModel):
    id: str
    task_id: str
    title: str
    target: str
    severity: Severity
    status: FindingStatus = "candidate"
    evidence_artifact_id: str | None = None
    evidence_excerpt: str | None = None
    reproduction_steps: list[str] = Field(default_factory=list)
    remediation: str | None = None
    tool: str | None = None


class WorkerResult(BaseModel):
    task_id: str
    intent_id: str
    status: WorkerStatus
    artifacts: list[ArtifactRecord] = Field(default_factory=list)
    facts: list[str] = Field(default_factory=list)
    leads: list[str] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class DecisionTrace(BaseModel):
    task_id: str
    phase: DecisionPhase
    summary: str
    rationale: str
    intent_id: str | None = None
    inputs: list[str] = Field(default_factory=list)
    selected_tools: list[str] = Field(default_factory=list)
    next_action: str | None = None


class SessionRecord(BaseModel):
    task_id: str
    schema_version: int = 2
    status: SessionStatus = "created"
    active_solver_id: str | None = None
    turn_count: int = 0
    max_turns: int = 48
    started_at: str | None = None
    finished_at: str | None = None
    stop_reason: str = ""
    workspace_path: str = ""
    mcp_catalog_version: str = ""


class SolverRecord(BaseModel):
    id: str
    task_id: str
    role: SolverRole = "main"
    status: SolverStatus = "starting"
    model_name: str = ""
    parent_solver_id: str | None = None
    started_at: str | None = None
    finished_at: str | None = None


class MemoryEntry(BaseModel):
    id: str
    task_id: str
    kind: MemoryKind
    content: str = Field(min_length=1, max_length=800)
    artifact_ids: list[str] = Field(default_factory=list)
    source: str
    supersedes_id: str | None = None
    created_at: str
    updated_at: str


class StrategySource(BaseModel):
    """A provenance anchor for untrusted hint or article content."""

    model_config = {"extra": "forbid"}

    hint_id: str | None = None
    url: str | None = None
    artifact_id: str | None = None
    extraction_status: ExtractionStatus = "not_requested"
    source_refs: list[str] = Field(default_factory=list, max_length=32)


class StrategyStep(BaseModel):
    """One candidate, evidence-producing test in a StrategyCard."""

    model_config = {"extra": "forbid"}

    id: str
    title: str = Field(min_length=1, max_length=200)
    instructions: str = Field(min_length=1, max_length=1200)
    expected_request: str = Field(default="", max_length=800)
    success_marker: str = Field(default="", max_length=300)
    failure_conditions: list[str] = Field(default_factory=list, max_length=8)
    next_step_id: str | None = None
    risk: RiskLevel = "passive"
    status: StrategyStatus = "pending"
    action_ids: list[str] = Field(default_factory=list, max_length=128)
    evidence_artifact_ids: list[str] = Field(default_factory=list, max_length=128)
    last_result: str = Field(default="", max_length=800)


class StrategyCard(BaseModel):
    """Durable candidate strategy; source claims are never facts by default."""

    model_config = {"extra": "forbid"}

    id: str
    task_id: str
    schema_version: int = 1
    title: str = Field(min_length=1, max_length=240)
    summary: str = Field(default="", max_length=2000)
    claims: list[str] = Field(default_factory=list, max_length=24)
    prerequisites: list[str] = Field(default_factory=list, max_length=16)
    target_version_checks: list[str] = Field(default_factory=list, max_length=12)
    sources: list[StrategySource] = Field(default_factory=list, max_length=16)
    steps: list[StrategyStep] = Field(default_factory=list, max_length=32)
    status: StrategyStatus = "pending"
    active_step_id: str | None = None
    created_at: str
    updated_at: str


class ArtifactSegment(BaseModel):
    model_config = {"extra": "forbid"}

    ref: str
    heading: str = Field(default="", max_length=300)
    text: str = Field(default="", max_length=8000)
    char_start: int = Field(default=0, ge=0)
    char_end: int = Field(default=0, ge=0)


class ArtifactIndex(BaseModel):
    """Searchable, non-authoritative projection of an immutable Artifact."""

    model_config = {"extra": "forbid"}

    artifact_id: str
    task_id: str
    document_type: str
    extraction_status: ExtractionStatus
    summary: str = Field(default="", max_length=2400)
    segments: list[ArtifactSegment] = Field(default_factory=list, max_length=128)
    created_at: str


class ContextMetric(BaseModel):
    task_id: str
    solver_id: str
    turn: int = Field(ge=0)
    audit_message_count: int = Field(ge=0)
    working_message_count: int = Field(ge=0)
    working_chars: int = Field(ge=0)
    summary_hits: int = Field(default=0, ge=0)
    artifact_retrievals: int = Field(default=0, ge=0)
    provider_input_tokens: int | None = Field(default=None, ge=0)
    provider_output_tokens: int | None = Field(default=None, ge=0)
    created_at: str


class ActionEffect(BaseModel):
    """User-reviewable description of an Action's possible side effect."""

    model_config = {"extra": "forbid"}

    scope: Literal["none", "session", "workspace", "target"] = "none"
    persistence: Literal["none", "temporary", "persistent"] = "none"
    reversibility: Literal["not_applicable", "reversible", "uncertain", "irreversible"] = "not_applicable"
    category: Literal[
        "authentication", "submission", "file_write", "resource_create",
        "resource_modify", "resource_delete", "containment", "destructive_scan",
    ] = "submission"
    description: str = Field(default="No persistent side effect is declared.", min_length=1, max_length=500)


class ActionSpec(BaseModel):
    """The sole request shape accepted by a controlled executor (A -> B)."""

    id: str
    task_id: str
    solver_id: str
    kind: ActionKind
    capability: str
    target: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    rationale: str
    risk: RiskLevel
    strategy_card_id: str | None = None
    strategy_step_id: str | None = None
    expected_outcome: str = ""
    retry_reason: str = ""
    alternative_analysis: str = ""
    effect: ActionEffect = Field(default_factory=ActionEffect)
    input_id: str | None = None
    target_ref: str | None = None
    actual_target: str | None = None
    authorization: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)


class ActionResult(BaseModel):
    """The sole execution result shape returned to the orchestration runtime."""

    action_id: str
    task_id: str
    solver_id: str
    status: ActionStatus
    summary: str
    artifact_ids: list[str] = Field(default_factory=list)
    facts: list[str] = Field(default_factory=list)
    leads: list[str] = Field(default_factory=list)
    candidate_flags: list[str] = Field(default_factory=list)
    candidate_findings: list[Finding] = Field(default_factory=list)
    error: TGAError | None = None


class AgentEvent(BaseModel):
    schema_version: int = 2
    id: str
    task_id: str
    solver_id: str | None = None
    seq: int = Field(ge=1)
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class ChallengeContract(BaseModel):
    """Durable completion state for an authorized challenge.

    TGA deliberately has no submission fields: a provenance-backed
    ``FLAG_CONFIRMED`` event is the sole solved oracle.
    """

    task_id: str
    entry_url: str | None = None
    allowed_origins: list[str]
    status: ChallengeStatus = "unknown"
    flag_format: str | None = None
    completion_proof_artifact_id: str | None = None
    status_reason: str = ""
    solved_at: str | None = None
