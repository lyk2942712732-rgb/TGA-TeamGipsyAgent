"""Kali image inventory and execution constraints."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from tga.domain.capabilities.solver_binding import KaliCapability


class KaliToolInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    executable: str
    version: str | None = None
    category: str | None = None


class KaliResourceLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cpu_cores: float = Field(gt=0, le=64)
    memory_mb: int = Field(ge=64, le=1_048_576)
    timeout_seconds: int = Field(ge=1, le=7_200)
    max_processes: int = Field(ge=1, le=8_192)


class KaliProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    display_name: str
    image_name: str
    image_tag: str
    image_digest: str | None = None
    tools: tuple[KaliToolInfo, ...]
    supported_capabilities: tuple[KaliCapability, ...] = ("kali.exec",)
    allowed_executables: tuple[str, ...]
    session_executables: tuple[str, ...] = ()
    network_mode: Literal[
        "disabled", "task_target_allowlist", "unrestricted_with_approval"
    ]
    input_mount: Literal["none", "read_only"] = "read_only"
    scratch_mount: Literal["private_read_write"] = "private_read_write"
    shared_artifact_mount: Literal["none", "read_only"] = "read_only"
    limits: KaliResourceLimits
    enabled: bool = True

    @field_validator("supported_capabilities", "allowed_executables", "session_executables")
    @classmethod
    def unique_executables(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)):
            raise ValueError("Kali executable entries must be unique")
        return values


__all__ = ["KaliProfile", "KaliResourceLimits", "KaliToolInfo"]
