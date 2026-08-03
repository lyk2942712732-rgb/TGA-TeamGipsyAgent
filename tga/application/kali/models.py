"""Stable product-facing Kali Profile commands and details."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from tga.domain.kali import KaliResourceLimits, KaliToolInfo


class _KaliProfileWriteCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    display_name: str
    image_name: str
    image_tag: str
    image_digest: str | None = None
    # Read-only projections are accepted so a GET response is a valid PUT body.
    image: str | None = None
    tools: tuple[KaliToolInfo, ...] = ()
    supported_capabilities: tuple[Literal["kali.exec", "kali.session"], ...]
    allowed_executables: tuple[str, ...]
    session_executables: tuple[str, ...] = ()
    network_mode: Literal[
        "disabled", "task_target_allowlist", "unrestricted_with_approval"
    ]
    # These mounts are fixed runtime invariants, not Sandbox configuration.
    input_mount: Literal["read_only"] = "read_only"
    scratch_mount: Literal["private_read_write"] = "private_read_write"
    shared_artifact_mount: Literal["read_only"] = "read_only"
    limits: KaliResourceLimits
    enabled: bool = True
    assigned_solver_count: int | None = Field(default=None, ge=0)
    assigned_solver_ids: tuple[str, ...] = ()
    config_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class KaliProfileCreateCommand(_KaliProfileWriteCommand):
    pass


class KaliProfileUpdateCommand(_KaliProfileWriteCommand):
    pass


class KaliProfileDetail(_KaliProfileWriteCommand):
    assigned_solver_count: int = Field(ge=0)


__all__ = [
    "KaliProfileCreateCommand",
    "KaliProfileDetail",
    "KaliProfileUpdateCommand",
]
