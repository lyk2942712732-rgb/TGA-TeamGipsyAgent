"""Shared cross-module contracts for TGA Week 1.

All teams should import these models instead of redefining task, intent,
artifact, finding, or worker-result shapes locally.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Annotated, Any, Literal, Union
from urllib.parse import urlparse

from pydantic import BaseModel, Field, model_validator

from tga.modes import TaskMode, normalize_mode


Intensity = Literal["passive", "normal", "active"]
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
SessionStatus = Literal["created", "running", "paused", "blocked", "completed", "failed", "cancelled"]
SolverStatus = Literal["starting", "running", "waiting", "completed", "failed", "cancelled"]
SolverRole = Literal["recon", "targeted", "research", "main"]
ChallengeStatus = Literal["unknown", "active", "solved", "blocked", "expired"]
HypothesisStatus = Literal["pending", "testing", "verified", "rejected", "inconclusive", "superseded"]
MemoryKind = Literal["fact", "evidence", "failure_boundary", "hint", "constraint", "decision"]
ActionKind = Literal["http", "tool", "workspace", "browser"]
ActionStatus = Literal["proposed", "approved", "running", "succeeded", "failed", "blocked", "cancelled"]
StrategyStatus = Literal["pending", "testing", "verified", "rejected", "superseded"]
ExtractionStatus = Literal["not_requested", "blocked_out_of_scope", "failed", "extracted"]


class TGAError(BaseModel):
    code: str
    message: str
    retryable: bool = False


class ResourceProvenance(BaseModel):
    model_config = {"extra": "forbid"}

    source: Literal["user_upload", "manual", "mcp", "generated", "legacy"] = "manual"
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
        if self.kind in {"file", "files", "archive", "image"} and self.provenance.source != "legacy":
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


SessionFileKind = Literal["task", "hint"]
MediaKind = Literal["image", "text", "document", "archive", "binary", "other"]


class SessionFile(BaseModel):
    """Immutable file owned by one Session workspace."""

    model_config = {"extra": "forbid", "populate_by_name": True}

    id: str = Field(pattern=r"^asset_[a-f0-9]{16,64}$")
    original_name: str = Field(alias="originalName", min_length=1, max_length=255)
    stored_name: str = Field(alias="storedName", pattern=r"^[a-f0-9]{32,64}(?:\.[A-Za-z0-9]{1,16})?$")
    relative_path: str = Field(alias="relativePath", pattern=r"^inputs/(?:task|hints)/[a-f0-9]{32,64}(?:\.[A-Za-z0-9]{1,16})?$")
    mime_type: str = Field(alias="mimeType", min_length=1, max_length=255)
    size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    kind: SessionFileKind
    media_kind: MediaKind = Field(alias="mediaKind")

    @property
    def container_path(self) -> str:
        return f"/workspace/{self.relative_path}"

    def manifest_item(self) -> dict[str, Any]:
        return {
            **self.model_dump(mode="json", by_alias=False),
            "container_path": self.container_path,
            "purpose": "primary task material" if self.kind == "task" else "auxiliary hint material",
        }


class SessionHint(BaseModel):
    model_config = {"extra": "forbid"}

    text: str | None = Field(default=None, max_length=16_384)
    files: list[SessionFile] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def files_are_hints(self) -> "SessionHint":
        if any(item.kind != "hint" for item in self.files):
            raise ValueError("hint.files may contain only hint attachments")
        self.text = self.text.strip() if self.text and self.text.strip() else None
        return self


class SessionInput(BaseModel):
    model_config = {"extra": "forbid", "populate_by_name": True}

    task_files: list[SessionFile] = Field(default_factory=list, alias="taskFiles", max_length=64)
    hint: SessionHint = Field(default_factory=SessionHint)

    @model_validator(mode="after")
    def validate_files(self) -> "SessionInput":
        if any(item.kind != "task" for item in self.task_files):
            raise ValueError("taskFiles may contain only task files")
        ids = [item.id for item in [*self.task_files, *self.hint.files]]
        if len(ids) != len(set(ids)):
            raise ValueError("Session input file ids must be unique")
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

    mode: Literal["none", "observe", "interact"] = "none"
    allowed_scopes: list[str] = Field(default_factory=list, max_length=128)
    rate_limit: int = Field(default=30, ge=0, le=100_000)
    concurrency: int = Field(default=2, ge=0, le=128)


class FilesystemExecutionPolicy(BaseModel):
    model_config = {"extra": "forbid"}

    mode: Literal["read_only", "workspace_write"] = "read_only"
    allowed_roots: list[str] = Field(default_factory=list, max_length=64)


class ProcessExecutionPolicy(BaseModel):
    model_config = {"extra": "forbid"}

    mode: Literal["forbidden", "sandbox_only", "authorized_host"] = "forbidden"
    timeout_seconds: int = Field(default=60, ge=0, le=3600)


class FuzzingExecutionPolicy(BaseModel):
    model_config = {"extra": "forbid"}

    mode: Literal["disabled", "bounded", "extended"] = "disabled"
    max_cases: int = Field(default=0, ge=0, le=10_000_000)
    max_duration_seconds: int = Field(default=0, ge=0, le=86_400)
    concurrency: int = Field(default=0, ge=0, le=128)


class StateChangeExecutionPolicy(BaseModel):
    model_config = {"extra": "forbid"}

    mode: Literal["forbidden", "approval_required", "authorized"] = "forbidden"
    allowed_actions: list[str] = Field(default_factory=list, max_length=64)


class ContainmentExecutionPolicy(BaseModel):
    model_config = {"extra": "forbid"}

    mode: Literal["observe_only", "approval_required", "authorized"] = "observe_only"
    allowed_actions: list[str] = Field(default_factory=list, max_length=64)


class MCPExecutionPolicy(BaseModel):
    model_config = {"extra": "forbid"}

    enabled_servers: list[str] = Field(default_factory=list, max_length=64)
    enabled_tools: list[str] = Field(default_factory=list, max_length=128)
    enabled_resources: list[str] = Field(default_factory=list, max_length=128)
    allow_active: bool = False


class ExecutionPolicy(BaseModel):
    model_config = {"extra": "forbid"}

    network: NetworkExecutionPolicy = Field(default_factory=NetworkExecutionPolicy)
    filesystem: FilesystemExecutionPolicy = Field(default_factory=FilesystemExecutionPolicy)
    process_execution: ProcessExecutionPolicy = Field(default_factory=ProcessExecutionPolicy)
    fuzzing: FuzzingExecutionPolicy = Field(default_factory=FuzzingExecutionPolicy)
    state_change: StateChangeExecutionPolicy = Field(default_factory=StateChangeExecutionPolicy)
    containment: ContainmentExecutionPolicy = Field(default_factory=ContainmentExecutionPolicy)
    # Legacy read projection only. Schema-v4 Session creation and runtime MCP
    # authorization ignore this field and use the global registry instead.
    mcp: MCPExecutionPolicy = Field(default_factory=MCPExecutionPolicy)
    source: Literal["default", "user", "legacy_migration"] = "default"


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


def _legacy_resource(value: str, *, role: ResourceRole) -> dict[str, Any]:
    clean = value.strip()
    digest = hashlib.sha256(clean.encode("utf-8", errors="replace")).hexdigest()[:12]
    prefix = "input" if role == "target" else "hint"
    if clean.startswith(("http://", "https://")):
        kind = "url"
        return {"id": f"{prefix}_legacy_{digest}", "role": role, "kind": kind, "label": clean, "url": clean, "uri": clean, "provenance": {"source": "legacy"}}
    try:
        path = Path(clean)
        kind = "directory" if path.exists() and path.is_dir() else "file" if path.exists() else "text"
    except (OSError, ValueError):
        kind = "text"
    payload: dict[str, Any] = {
        "id": f"{prefix}_legacy_{digest}", "role": role, "kind": kind,
        "label": (Path(clean).name or clean or "Legacy target") if kind in {"file", "directory"} else "Legacy target",
        "provenance": {"source": "legacy", "original_name": Path(clean).name if kind in {"file", "directory"} else None},
    }
    if kind == "text":
        payload["text"] = clean
    else:
        payload["uri"] = clean
    return payload


def default_mode_config(mode: TaskMode, *, intensity: str = "normal", flag_format: str | None = None) -> ModeConfig:
    if mode == "ctf":
        return CtfModeConfig(flag_format=flag_format or CtfModeConfig().flag_format)
    if mode == "penetration_test":
        return PenetrationTestModeConfig(depth={"passive": "reconnaissance", "normal": "validation", "active": "comprehensive"}.get(intensity, "reconnaissance"))
    if mode == "incident_response":
        return IncidentResponseModeConfig()
    if mode == "vulnerability_research":
        return VulnerabilityResearchModeConfig(depth={"passive": "triage", "normal": "focused", "active": "deep"}.get(intensity, "triage"))
    return ReverseAnalysisModeConfig(analysis_method={"passive": "static_only", "normal": "static_and_dynamic", "active": "deep_instrumentation"}.get(intensity, "static_only"))


def default_execution_policy(
    mode: TaskMode, *, targets: list[ResourceRef], legacy_scope: list[str],
    intensity: str = "normal", allow_active_scan: bool = False,
    mcp_servers: list[str] | None = None, mcp_tools: list[str] | None = None,
) -> ExecutionPolicy:
    scopes = list(dict.fromkeys(item for item in legacy_scope if item))
    if not scopes:
        for item in targets:
            if item.kind == "url" and item.url:
                parsed = urlparse(item.url)
                scopes.append(f"{parsed.scheme}://{parsed.netloc}")
            elif item.kind == "network" and item.uri:
                scopes.append(item.uri)
    network_mode: Literal["none", "observe", "interact"] = "none"
    if scopes:
        if mode == "ctf":
            network_mode = "interact"
        elif mode in {"penetration_test", "incident_response"}:
            network_mode = "interact" if allow_active_scan else "observe"
        elif mode == "vulnerability_research":
            network_mode = "observe"
    process_mode: Literal["forbidden", "sandbox_only", "authorized_host"] = "forbidden"
    if mode == "reverse_engineering" and intensity in {"normal", "active"}:
        process_mode = "sandbox_only"
    return ExecutionPolicy(
        network=NetworkExecutionPolicy(mode=network_mode, allowed_scopes=scopes),
        process_execution=ProcessExecutionPolicy(mode=process_mode),
        mcp=MCPExecutionPolicy(
            enabled_servers=list(mcp_servers or []), enabled_tools=list(mcp_tools or []),
            allow_active=bool(allow_active_scan),
        ),
        source="legacy_migration",
    )


class TGATask(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    name: str = Field(min_length=1, max_length=255)
    mode: TaskMode
    # Compatibility projection only. New runtime authorization and retrieval
    # use targets/hints and execution_policy, never this display string.
    target: str = ""
    targets: list[ResourceRef] = Field(default_factory=list, max_length=256)
    hints: list[ResourceRef] = Field(default_factory=list, max_length=256)
    session_input: SessionInput = Field(default_factory=SessionInput)
    mcp_capabilities: MCPCapabilitySnapshot = Field(default_factory=MCPCapabilitySnapshot)
    # Kept only so existing task files remain readable. Product sessions use
    # ``target`` as the challenge authorization contract and derive this
    # compatibility value automatically.
    scope: list[str] = Field(default_factory=list)
    target_theme: str = ""
    target_description: str = ""
    intensity: Intensity = "normal"
    allow_active_scan: bool = False
    # MCP access is deny-by-default.  Old task payloads remain readable and
    # therefore receive an empty allowlist rather than every configured MCP.
    mcp_servers: list[str] = Field(default_factory=list, max_length=64)
    mcp_direct_tools: list[str] = Field(default_factory=list, max_length=128)
    goal: str = Field(min_length=1, max_length=8000)
    flag_format: str | None = None
    mode_config: ModeConfig | None = None
    execution_policy: ExecutionPolicy | None = None
    execution_budget: dict[str, int] = Field(default_factory=dict)
    migration_notes: list[str] = Field(default_factory=list, max_length=32)
    # A CTF platform can occasionally use an incomplete/self-signed chain.
    # This is never a global TLS switch: every exception is an exact HTTPS
    # origin that must already be inside this task's authorization scope.
    insecure_tls_origins: list[str] = Field(default_factory=list, max_length=8)
    # Version 1 payloads omitted this field.  The default keeps them readable;
    # the runtime only creates a v2 session after an explicit start request.
    schema_version: int = 4

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_mode(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        migrated = dict(value)
        source_schema = int(migrated.get("schema_version") or 1)
        if "mode" in migrated:
            migrated["mode"] = normalize_mode(migrated["mode"])
        mode = migrated.get("mode") or "ctf"
        legacy_payload = "targets" not in migrated and "mode_config" not in migrated and "execution_policy" not in migrated
        if not migrated.get("targets") and str(migrated.get("target") or "").strip():
            migrated["targets"] = [_legacy_resource(str(migrated["target"]), role="target")]
            migrated.setdefault("migration_notes", []).append("legacy target migrated to targets[]")
        if not migrated.get("hints") and str(migrated.get("initial_hint") or "").strip():
            migrated["hints"] = [_legacy_resource(str(migrated["initial_hint"]), role="hint")]
            migrated.setdefault("migration_notes", []).append("legacy initial_hint migrated to hints[]")
        if not migrated.get("mode_config"):
            migrated["mode_config"] = default_mode_config(
                mode, intensity=str(migrated.get("intensity") or "normal") if legacy_payload else "passive",
                flag_format=migrated.get("flag_format"),
            ).model_dump(mode="json")
            if legacy_payload:
                migrated.setdefault("migration_notes", []).append("legacy intensity migrated conservatively to mode_config")
        if not migrated.get("execution_policy"):
            refs = [ResourceRef.model_validate(item) for item in (migrated.get("targets") or [])]
            migrated["execution_policy"] = default_execution_policy(
                mode, targets=refs, legacy_scope=list(migrated.get("scope") or []),
                intensity=str(migrated.get("intensity") or "normal") if legacy_payload else "passive",
                allow_active_scan=bool(migrated.get("allow_active_scan")) if legacy_payload else False,
                mcp_servers=list(migrated.get("mcp_servers") or []),
                mcp_tools=list(migrated.get("mcp_direct_tools") or []),
            ).model_dump(mode="json")
            if not legacy_payload:
                migrated["execution_policy"]["source"] = "default"
            if legacy_payload:
                migrated.setdefault("migration_notes", []).append("legacy authorization migrated with least privilege")
        if legacy_payload:
            migrated["schema_version"] = int(migrated.get("schema_version") or 2)
        elif "schema_version" not in migrated:
            migrated["schema_version"] = 3
        if source_schema < 4 and "session_input" not in migrated:
            # Historical resources remain readable through targets/hints.
            # They are deliberately not promoted into schema-v4 Session files.
            migrated["session_input"] = {"taskFiles": [], "hint": {"files": []}}
        if migrated.get("mode") not in {None, "ctf"}:
            migrated["flag_format"] = None
        return migrated

    @model_validator(mode="after")
    def validate_authorized_scope(self) -> "TGATask":
        self.target = self.target.strip()
        self.targets = list({item.id: item for item in self.targets}.values())
        self.hints = list({item.id: item for item in self.hints}.values())
        if set(item.id for item in self.targets).intersection(item.id for item in self.hints):
            raise ValueError("target and hint input ids must be unique")
        if any(item.role != "target" for item in self.targets) or any(item.role != "hint" for item in self.hints):
            raise ValueError("targets and hints contain a resource with the wrong role")
        if self.mode_config is None or self.mode_config.mode != self.mode:
            raise ValueError("mode_config discriminator must match task mode")
        if self.execution_policy is None:
            raise ValueError("execution_policy is required after migration")
        if self.schema_version >= 4:
            forbidden = [item.kind for item in [*self.targets, *self.hints] if item.kind in {"url", "network", "directory", "repository", "artifact", "mcp_resource", "mcp_tool"}]
            if forbidden:
                raise ValueError(f"new Sessions accept only uploaded task and hint files, not: {', '.join(sorted(set(forbidden)))}")
            # Compatibility fields may still be supplied by old clients, but
            # they can never become a schema-v4 MCP permission source.
            self.mcp_servers = []
            self.mcp_direct_tools = []
            self.execution_policy.mcp = MCPExecutionPolicy()
        if not self.target and self.targets:
            primary = self.targets[0]
            self.target = primary.url or primary.uri or primary.label
        self.scope = list(dict.fromkeys(item.strip() for item in self.scope if item.strip()))
        if not self.scope:
            self.scope = list(self.execution_policy.network.allowed_scopes)
        self.mcp_servers = list(dict.fromkeys(item.strip() for item in self.mcp_servers if item.strip()))
        self.mcp_direct_tools = list(dict.fromkeys(item.strip() for item in self.mcp_direct_tools if item.strip()))
        if not self.mcp_servers:
            self.mcp_servers = list(self.execution_policy.mcp.enabled_servers)
        if not self.mcp_direct_tools:
            self.mcp_direct_tools = list(self.execution_policy.mcp.enabled_tools)
        if self.schema_version == 3 and set(self.mcp_servers) != set(self.execution_policy.mcp.enabled_servers):
            raise ValueError("mcp_servers must match execution_policy.mcp.enabled_servers")
        if self.schema_version == 3 and set(self.mcp_direct_tools) != set(self.execution_policy.mcp.enabled_tools):
            raise ValueError("mcp_direct_tools must match execution_policy.mcp.enabled_tools")
        if any(not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", item) for item in self.mcp_servers):
            raise ValueError("mcp_servers contains an invalid server id")
        if any(not re.fullmatch(r"mcp__[A-Za-z0-9_-]{1,59}", item) for item in self.mcp_direct_tools):
            raise ValueError("mcp_direct_tools must contain discovered provider names")
        if not self.scope and self.target:
            # Non-network inputs never become network authorization. Legacy
            # URL tasks already received exact scopes in migration above.
            parsed_target = urlparse(self.target)
            if parsed_target.scheme in {"http", "https"} and parsed_target.netloc:
                self.scope = [f"{parsed_target.scheme}://{parsed_target.netloc}"]
                self.execution_policy.network.allowed_scopes = list(self.scope)
        if self.flag_format:
            if len(self.flag_format) > 256:
                raise ValueError("flag_format exceeds 256 characters")
            try:
                re.compile(self.flag_format)
            except re.error as exc:
                raise ValueError(f"invalid flag_format: {exc}") from exc
        if self.mode == "ctf" and isinstance(self.mode_config, CtfModeConfig):
            self.flag_format = self.mode_config.flag_format
        target_origin = next((_https_origin(item.url or "") for item in self.targets if item.kind == "url" and _https_origin(item.url or "")), None)
        from tga.core.scope import is_in_scope

        canonical_origins: list[str] = []
        for value in self.insecure_tls_origins:
            origin = _https_origin(value)
            if origin is None or origin != target_origin:
                raise ValueError("insecure_tls_origins may contain only the exact HTTPS target origin")
            if not is_in_scope(origin, self.scope):
                raise ValueError("insecure_tls_origins must be inside task scope")
            if origin not in canonical_origins:
                canonical_origins.append(origin)
        self.insecure_tls_origins = canonical_origins
        return self

    def input_manifest(self) -> dict[str, Any]:
        if self.schema_version >= 4:
            return {
                "task_goal": self.goal,
                "hint_text": self.session_input.hint.text,
                "task_files": [item.manifest_item() for item in self.session_input.task_files],
                "hint_files": [item.manifest_item() for item in self.session_input.hint.files],
            }
        return {
            "task_goal": self.goal,
            "inputs": [item.manifest_item() for item in [*self.targets, *self.hints]],
        }

    def primary_target(self, *, kind: ResourceKind | None = None) -> ResourceRef | None:
        return next((item for item in self.targets if kind is None or item.kind == kind), None)

    def default_action_target(self) -> str:
        preferred = next((item for item in self.targets if item.kind in {"url", "network"}), None)
        preferred = preferred or (self.targets[0] if self.targets else None)
        return (preferred.url or preferred.uri or preferred.id) if preferred else self.id


def _https_origin(value: str) -> str | None:
    parsed = urlparse(value.strip())
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        return None
    if parsed.path not in {"", "/"} or parsed.params or parsed.query or parsed.fragment:
        return None
    host = parsed.hostname.lower()
    port = parsed.port
    return f"https://{host}" if port in {None, 443} else f"https://{host}:{port}"


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


class Hypothesis(BaseModel):
    id: str
    task_id: str
    statement: str
    attack_class: str
    entry_point: str
    rationale: str
    next_test: str
    status: HypothesisStatus = "pending"
    confidence: float = Field(ge=0, le=1)
    attempt_count: int = 0
    evidence_artifact_ids: list[str] = Field(default_factory=list)
    last_result: str = ""
    owner_solver_id: str | None = None
    created_at: str
    updated_at: str


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


class ActionSpec(BaseModel):
    """The sole request shape accepted by a controlled executor (A -> B)."""

    id: str
    task_id: str
    solver_id: str
    hypothesis_id: str | None = None
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
    expected_side_effects: str = ""
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


class HypothesisDraft(BaseModel):
    model_config = {"extra": "forbid"}

    statement: str = Field(min_length=1)
    attack_class: str = Field(min_length=1)
    entry_point: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    next_test: str = Field(min_length=1)
    confidence: float = Field(default=0.5, ge=0, le=1)


class HypothesisUpdate(BaseModel):
    model_config = {"extra": "forbid"}

    hypothesis_id: str
    status: HypothesisStatus
    last_result: str = Field(default="", max_length=800)
    evidence_artifact_ids: list[str] = Field(default_factory=list)
    decisive: bool = False


class FactDraft(BaseModel):
    model_config = {"extra": "forbid"}

    content: str = Field(min_length=1, max_length=800)
    artifact_ids: list[str] = Field(default_factory=list)


class FailureBoundaryDraft(BaseModel):
    model_config = {"extra": "forbid"}

    attack_class: str = Field(min_length=1)
    entry_point: str = Field(min_length=1)
    summary: str = Field(min_length=1, max_length=800)
    artifact_ids: list[str] = Field(default_factory=list)


class SubagentRequest(BaseModel):
    """The only context hand-off accepted when Manager starts a child Solver."""

    model_config = {"extra": "forbid"}

    id: str
    task_id: str
    parent_solver_id: str
    role: SolverRole
    objective: str = Field(min_length=1, max_length=800)
    hypothesis_ids: list[str] = Field(default_factory=list)
    input_artifact_ids: list[str] = Field(default_factory=list)
    skill_names: list[str] = Field(default_factory=list)
    max_actions: int = Field(default=32, ge=1, le=256)

    @model_validator(mode="after")
    def child_role_only(self) -> "SubagentRequest":
        if self.role == "main":
            raise ValueError("main is the manager-owned coordinator, not a subagent role")
        return self


class SubagentOutput(BaseModel):
    """Bounded, schema-validated child Solver hand-off."""

    model_config = {"extra": "forbid"}

    request_id: str
    solver_id: str
    status: Literal["completed", "blocked", "failed"]
    hypotheses: list[HypothesisDraft] = Field(default_factory=list, max_length=5)
    result_updates: list[HypothesisUpdate] = Field(default_factory=list, max_length=8)
    facts: list[FactDraft] = Field(default_factory=list, max_length=8)
    failure_boundaries: list[FailureBoundaryDraft] = Field(default_factory=list, max_length=8)
    candidate_flags: list[str] = Field(default_factory=list, max_length=8)
    artifact_ids: list[str] = Field(default_factory=list, max_length=32)
    coverage_gaps: list[str] = Field(default_factory=list, max_length=8)
    next_recommendation: str = Field(default="", max_length=800)


# Compatibility aliases for early consumers of the advanced contract draft.
HypothesisDraftContract = HypothesisDraft
HypothesisUpdateContract = HypothesisUpdate
