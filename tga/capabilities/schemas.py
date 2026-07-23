from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HTTPRequestAssertions(StrictArguments):
    parameter_count: int | None = Field(default=None, ge=0, le=100_000)
    encoded_length: int | None = Field(default=None, ge=0, le=4_194_304)
    content_type: str | None = Field(default=None, max_length=200)
    expected_marker: str | None = Field(default=None, min_length=1, max_length=300)


class HTTPRequestArguments(StrictArguments):
    input_id: str | None = Field(default=None, pattern=r"^input_[A-Za-z0-9_-]{1,64}$")
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "GET"
    path: str | None = None
    url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    query: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    body: Any | None = None
    body_format: Literal["json", "form", "text"] | None = None
    assertions: HTTPRequestAssertions = Field(default_factory=HTTPRequestAssertions)
    session_mode: Literal["persistent", "stateless"] = "persistent"
    timeout: int = Field(default=12, ge=1, le=60)

    @model_validator(mode="after")
    def has_destination(self) -> "HTTPRequestArguments":
        if not self.path and not self.url:
            raise ValueError("one of path or url is required")
        if self.method == "GET" and self.body is not None:
            raise ValueError("GET requests cannot include a body")
        if self.body is not None and self.body_format is None and isinstance(self.body, str):
            explicit_form = any(
                key.casefold() == "content-type" and str(value).casefold().startswith("application/x-www-form-urlencoded")
                for key, value in self.headers.items()
            )
            if not explicit_form and "=" in self.body and ("&" in self.body or re.match(r"^[^=\s]+=[^=]*$", self.body)):
                raise ValueError("form-like body requires body_format='form'; no Content-Type is inferred from opaque text")
        if self.body_format == "form" and not isinstance(self.body, (dict, list, str)):
            raise ValueError("body_format='form' requires a mapping, pair list, or already encoded string")
        if self.body_format == "json" and isinstance(self.body, str):
            try:
                json.loads(self.body)
            except json.JSONDecodeError as exc:
                raise ValueError("body_format='json' requires valid JSON text or a JSON value") from exc
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


class WorkspaceShellArguments(StrictArguments):
    command: str = Field(min_length=1, max_length=65_536)
    timeout: int = Field(default=60, ge=1, le=300)


class ArtifactInspectArguments(StrictArguments):
    artifact_id: str = Field(pattern=r"^artifact_[a-f0-9]{12}$")
    query: str | None = Field(default=None, max_length=256)
    section: str | None = Field(default=None, max_length=256)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=16_384, ge=1, le=262_144)
