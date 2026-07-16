from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from tga.contracts import ArtifactRecord, Intensity, RiskLevel, TaskMode


CapabilityName = Literal[
    "http.request",
    "tool.invoke",
    "workspace.python",
    "workspace.binary",
    "artifact.inspect",
]
ActionStatus = Literal["ok", "failed", "blocked", "timeout"]


class ActionSpec(BaseModel):
    task_id: str
    solver_id: str
    action_id: str
    capability: CapabilityName
    target: str = ""
    scope: list[str] = Field(default_factory=list)
    mode: TaskMode = "ctf"
    intensity: Intensity = "normal"
    allow_active_scan: bool = False
    flag_format: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)


class ActionError(BaseModel):
    code: str
    message: str
    retryable: bool = False


class ActionResult(BaseModel):
    task_id: str
    solver_id: str
    action_id: str
    capability: CapabilityName
    status: ActionStatus
    summary: str
    artifacts: list[ArtifactRecord] = Field(default_factory=list)
    candidate_flags: list[str] = Field(default_factory=list)
    error: ActionError | None = None
    output_truncated: bool = False


class CapabilityDescriptor(BaseModel):
    name: CapabilityName
    input_schema: dict[str, Any]
    risk: RiskLevel
    supported_modes: list[TaskMode]
    max_output_bytes: int
    timeout_seconds: int
    scope_validator: str
    budget_key: str
    redacted_summary: str
    available: bool = True
    unavailable_reason: str | None = None


class HTTPRequestInput(BaseModel):
    url: str
    method: Literal["GET", "POST", "HEAD"] = "GET"
    headers: dict[str, str] = Field(default_factory=dict)
    body: str | None = None
    allow_redirects: bool = False
    max_redirects: int = 3
    timeout_seconds: int = Field(default=20, ge=1, le=120)
    max_output_bytes: int = Field(default=65536, ge=1024, le=1048576)


class ToolInvokeInput(BaseModel):
    tool: str
    mcp_tool: str | None = None
    target: str = ""
    arguments: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int = Field(default=120, ge=1, le=900)
    max_output_bytes: int = Field(default=131072, ge=1024, le=1048576)


class WorkspacePythonInput(BaseModel):
    code: str
    argv: list[str] = Field(default_factory=list)
    stdin: str = ""
    timeout_seconds: int = Field(default=10, ge=1, le=120)
    max_output_bytes: int = Field(default=65536, ge=1024, le=1048576)


class WorkspaceBinaryInput(BaseModel):
    path: str
    operation: Literal["metadata", "strings", "hexdump"] = "metadata"
    offset: int = Field(default=0, ge=0)
    length: int = Field(default=4096, ge=1, le=1048576)
    min_string: int = Field(default=4, ge=3, le=64)
    max_output_bytes: int = Field(default=65536, ge=1024, le=1048576)


class ArtifactInspectInput(BaseModel):
    artifact_path: str
    offset: int = Field(default=0, ge=0)
    length: int = Field(default=8192, ge=1, le=1048576)
    keywords: list[str] = Field(default_factory=list)
    context_chars: int = Field(default=120, ge=0, le=2000)
    max_output_bytes: int = Field(default=65536, ge=1024, le=1048576)
