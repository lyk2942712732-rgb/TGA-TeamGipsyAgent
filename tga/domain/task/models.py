"""Canonical schema-v6 task and task-input models."""

from __future__ import annotations

import ipaddress
import re
from typing import Annotated, Any, Literal, Union
from urllib.parse import urlparse

from pydantic import BaseModel, Field, model_validator

from tga.domain.governance.models import ExecutionPolicy
from tga.modes import TaskMode, normalize_mode


ResourceRole = Literal["target", "hint"]
ResourceKind = Literal[
    "url", "network", "file", "files", "directory", "repository", "archive",
    "image", "text", "artifact", "mcp_resource", "mcp_tool",
]
SessionFileKind = Literal["task_input"]
MediaKind = Literal["image", "text", "document", "archive", "binary", "other"]


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
            "id": self.id, "role": self.role, "kind": self.kind,
            "label": self.label, "uri": self.uri, "mime_type": self.mime_type,
            "size": self.size, "sha256": self.sha256, "summary": self.summary,
            "status": self.status, "retrieval": self.retrieval(),
            "provenance": self.provenance.model_dump(mode="json"),
            "server_id": self.server_id, "resource_uri": self.resource_uri,
            "tool_name": self.tool_name, "artifact_id": self.artifact_id,
        }


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
        CtfModeConfig, PenetrationTestModeConfig, IncidentResponseModeConfig,
        VulnerabilityResearchModeConfig, ReverseAnalysisModeConfig,
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
    agent_prompt_snapshot: dict[str, Any] | None = None
    execution_budget: dict[str, int] = Field(default_factory=dict)
    insecure_tls_origins: list[str] = Field(default_factory=list, max_length=8)
    schema_version: int = 6

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
            self.execution_policy.network.seed_origins, field_name="seed_origins",
        )
        self.execution_policy.network.custom_origins = _canonical_http_origins(
            self.execution_policy.network.custom_origins, field_name="custom_origins",
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
            parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username or parsed.password or parsed.path not in {"", "/"}
            or parsed.params or parsed.query or parsed.fragment
        ):
            raise ValueError(f"{field_name} must contain absolute HTTP(S) origins without credentials or paths")
        host = parsed.hostname.lower()
        default_port = 80 if parsed.scheme == "http" else 443
        origin = f"{parsed.scheme.lower()}://{host}" if parsed.port in {None, default_port} else f"{parsed.scheme.lower()}://{host}:{parsed.port}"
        if origin not in origins:
            origins.append(origin)
    return origins


__all__ = [
    "CtfModeConfig", "CtfVerifier", "IncidentResponseModeConfig", "MCPCapabilitySnapshot",
    "MCPCapabilityTool", "MediaKind", "ModeConfig", "ModelSnapshot",
    "PenetrationTestModeConfig", "ResourceKind", "ResourceProvenance", "ResourceRef",
    "ResourceRole", "ReverseAnalysisModeConfig", "SessionFile", "SessionFileKind",
    "SessionInput", "TGATask", "VulnerabilityResearchModeConfig", "default_mode_config",
]
