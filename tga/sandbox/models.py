"""Validated sandbox API models shared by providers."""

from __future__ import annotations

import ipaddress
import re
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class SandboxState(StrEnum):
    ACQUIRING = "acquiring"
    READY = "ready"
    RELEASED = "released"
    DESTROYING = "destroying"
    DESTROYED = "destroyed"
    FAILED = "failed"


class ResourceLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    timeout_seconds: int = Field(default=300, ge=1, le=7200)
    max_output_bytes: int = Field(default=262_144, ge=1024, le=64 * 1024 * 1024)
    memory_bytes: int = Field(default=512 * 1024 * 1024, ge=64 * 1024 * 1024)
    cpu_count: float = Field(default=1.0, gt=0, le=64)
    pids_limit: int = Field(default=256, ge=16, le=8192)


class NetworkGrant(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cidr: str
    ports: tuple[int, ...] = ()

    @field_validator("cidr")
    @classmethod
    def canonical_cidr(cls, value: str) -> str:
        network = ipaddress.ip_network(value, strict=False)
        return str(network)

    @field_validator("ports")
    @classmethod
    def valid_ports(cls, values: tuple[int, ...]) -> tuple[int, ...]:
        if any(port < 1 or port > 65535 for port in values):
            raise ValueError("network ports must be between 1 and 65535")
        return tuple(sorted(set(values)))


class SandboxProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    provider: Literal["docker_sandbox", "sandboxd", "remote_http"]
    image: str | None = None
    network_mode: Literal["none", "public_http", "target_allowlist", "remote"] = "none"
    web_allow_hosts: tuple[str, ...] = ()
    allow_net_raw: bool = False
    allow_ptrace: bool = False
    allowed_executables: tuple[str, ...] = ()
    toolset_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    limits: ResourceLimits = Field(default_factory=ResourceLimits)

    @field_validator("id")
    @classmethod
    def valid_id(cls, value: str) -> str:
        if not IDENTIFIER.fullmatch(value):
            raise ValueError("invalid sandbox profile id")
        return value

    @field_validator("allowed_executables")
    @classmethod
    def valid_executables(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)):
            raise ValueError("allowed executables must be unique")
        for value in values:
            if not IDENTIFIER.fullmatch(value):
                raise ValueError(f"invalid allowed executable: {value!r}")
        return values

    @model_validator(mode="after")
    def validate_provider(self) -> "SandboxProfile":
        if self.provider != "remote_http" and not self.image:
            raise ValueError("local sandbox profiles require a pinned image")
        if self.allow_net_raw and self.provider != "sandboxd":
            raise ValueError("NET_RAW is only supported by sandboxd profiles")
        if self.allow_ptrace and self.provider != "sandboxd":
            raise ValueError("SYS_PTRACE is only supported by sandboxd profiles")
        if self.network_mode == "target_allowlist" and self.provider != "sandboxd":
            raise ValueError("target allowlists require sandboxd")
        if self.network_mode != "public_http" and self.web_allow_hosts:
            raise ValueError("web allow hosts require public_http network mode")
        for resource in self.web_allow_hosts:
            if (
                not re.fullmatch(
                    r"(?:\*\.)?[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?(?::(?:80|443))?",
                    resource,
                )
                or ".." in resource
            ):
                raise ValueError(f"invalid web allow host: {resource!r}")
        return self


class SandboxHandle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instance_id: str
    task_id: str
    solver_id: str
    solver_run_id: str
    profile_id: str
    provider: Literal["docker_sandbox", "sandboxd"]
    config_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    image_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    toolset_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    fencing_token: int = Field(ge=1)
    state: SandboxState = SandboxState.READY

    @field_validator("instance_id", "task_id", "solver_id", "solver_run_id")
    @classmethod
    def valid_identifier(cls, value: str) -> str:
        if not IDENTIFIER.fullmatch(value):
            raise ValueError("invalid sandbox identifier")
        return value


class ProcessSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    argv: tuple[str, ...] = Field(default=(), max_length=256)
    tool_id: str | None = None
    environment: dict[str, str] = Field(default_factory=dict)
    logical_workspace: Literal["solver", "task_inputs", "task_shared"] = "solver"
    timeout_seconds: int | None = Field(default=None, ge=1, le=7200)
    network_grants: tuple[NetworkGrant, ...] = ()

    @field_validator("argv")
    @classmethod
    def safe_argv(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any("\x00" in value for value in values):
            raise ValueError("argv may not contain NUL")
        return values

    @field_validator("environment")
    @classmethod
    def safe_environment(cls, values: dict[str, str]) -> dict[str, str]:
        for name, value in values.items():
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) or "\x00" in value:
                raise ValueError("invalid process environment")
        return values

    @model_validator(mode="after")
    def command_or_tool(self) -> "ProcessSpec":
        if not self.argv and not self.tool_id:
            raise ValueError("process requires argv or a trusted tool id")
        if self.tool_id and not IDENTIFIER.fullmatch(self.tool_id):
            raise ValueError("invalid trusted tool id")
        return self


class ExecFrame(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sequence: int = Field(ge=1)
    timestamp_unix_ms: int = Field(ge=0)
    stream: Literal["stdout", "stderr"]
    data: bytes


class ExecResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    exit_code: int | None = None
    signal: str | None = None
    timed_out: bool = False
    truncated: bool = False
    stdout: bytes = b""
    stderr: bytes = b""


class SandboxInspection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    handle: SandboxHandle
    runtime: str
    active_processes: int = Field(default=0, ge=0)
    created_at: str | None = None
