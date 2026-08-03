"""Immutable Sandbox Profile data captured for a Solver assignment."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SandboxResourceLimitsSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    timeout_seconds: int = Field(ge=1, le=7200)
    max_output_bytes: int = Field(ge=1024, le=64 * 1024 * 1024)
    memory_bytes: int = Field(ge=64 * 1024 * 1024)
    cpu_count: float = Field(gt=0, le=64)
    pids_limit: int = Field(ge=16, le=8192)


class SandboxProfileSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    display_name: str | None = None
    enabled: bool = True
    provider: Literal["docker_sandbox", "sandboxd", "remote_http"]
    image: str | None = None
    network_mode: Literal["none", "public_http", "target_allowlist", "remote"] = "none"
    web_allow_hosts: tuple[str, ...] = ()
    allow_net_raw: bool = False
    allow_ptrace: bool = False
    supported_capabilities: tuple[str, ...] = ()
    allowed_executables: tuple[str, ...] = ()
    session_executables: tuple[str, ...] = ()
    toolset_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    limits: SandboxResourceLimitsSnapshot


__all__ = ["SandboxProfileSnapshot", "SandboxResourceLimitsSnapshot"]
