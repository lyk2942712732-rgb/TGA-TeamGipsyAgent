from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HTTPRequestArguments(StrictArguments):
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "GET"
    path: str | None = None
    url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    query: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    body: Any | None = None
    timeout: int = Field(default=12, ge=1, le=60)

    @model_validator(mode="after")
    def has_destination(self) -> "HTTPRequestArguments":
        if not self.path and not self.url:
            raise ValueError("one of path or url is required")
        return self


class ToolInvokeArguments(StrictArguments):
    tool_id: str = Field(min_length=1, max_length=128)
    tool_method: str = Field(min_length=1, max_length=128)
    arguments: dict[str, Any] = Field(default_factory=dict)
    # All capability executions share the same hard upper bound. Individual
    # tools may request less, never an arbitrarily long MCP process.
    timeout: int = Field(default=120, ge=1, le=120)


class WorkspaceReadArguments(StrictArguments):
    relative_path: str = Field(min_length=1, max_length=512)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=16_384, ge=1, le=262_144)


class WorkspaceWriteArguments(StrictArguments):
    relative_path: str = Field(min_length=1, max_length=512)
    content: str = Field(max_length=262_144)


class WorkspacePythonArguments(StrictArguments):
    script_path: str | None = Field(default=None, max_length=512)
    source: str | None = Field(default=None, max_length=65_536)
    argv: list[str] = Field(default_factory=list, max_length=32)
    timeout: int = Field(default=30, ge=1, le=120)

    @model_validator(mode="after")
    def has_script(self) -> "WorkspacePythonArguments":
        if bool(self.script_path) == bool(self.source):
            raise ValueError("provide exactly one of script_path or source")
        return self


class ArtifactInspectArguments(StrictArguments):
    artifact_id: str = Field(pattern=r"^artifact_[a-f0-9]{12}$")
    query: str | None = Field(default=None, max_length=256)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=16_384, ge=1, le=262_144)
