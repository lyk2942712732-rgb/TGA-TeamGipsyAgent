"""Root/host-owned sandbox configuration."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator, model_validator

from tga.sandbox.models import SandboxProfile


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "sandbox.json"


class DockerSandboxSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    executable: str = "sbx"
    task_root: str = "runs"
    template: str = (
        "docker.io/docker/sandbox-templates:shell-docker"
        "@sha256:REPLACE_WITH_RELEASE_DIGEST"
    )
    min_version: str = "0.34.0"
    max_version_exclusive: str = "0.35.0"


class SandboxdSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    socket_path: str = "/run/tga-sandboxd/sandboxd.sock"
    rpc_timeout_seconds: int = Field(default=10, ge=1, le=120)
    protocol_major: int = Field(default=1, ge=1)
    run_root: str = "/var/lib/tga/runs"
    allowed_client_uids: tuple[int, ...] = ()

    @field_validator("allowed_client_uids")
    @classmethod
    def valid_client_uids(cls, values: tuple[int, ...]) -> tuple[int, ...]:
        if len(set(values)) != len(values) or any(value < 0 or value > 2**32 - 1 for value in values):
            raise ValueError("sandboxd client UIDs must be unique uint32 values")
        return values


class SandboxTool(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    profile_id: str
    image: str
    args: tuple[str, ...] = ()

    @model_validator(mode="after")
    def safe_args(self) -> "SandboxTool":
        if len(self.args) > 32 or any("\x00" in value for value in self.args):
            raise ValueError("sandbox tool args are invalid")
        return self


class SandboxConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    runtime: Literal["disabled", "enforced"] = "disabled"
    terminal_grace_seconds: int = Field(default=900, ge=0, le=86_400)
    reconcile_interval_seconds: int = Field(default=60, ge=10, le=3600)
    docker_sandbox: DockerSandboxSettings = Field(default_factory=DockerSandboxSettings)
    sandboxd: SandboxdSettings = Field(default_factory=SandboxdSettings)
    profiles: dict[str, SandboxProfile]
    tools: dict[str, SandboxTool] = Field(default_factory=dict)
    _digest: str = PrivateAttr(default="")

    @model_validator(mode="after")
    def profile_keys_match(self) -> "SandboxConfig":
        if not self.profiles:
            raise ValueError("at least one sandbox profile is required")
        if self.runtime == "enforced" and not re.search(
            r"@sha256:[a-f0-9]{64}$", self.docker_sandbox.template
        ):
            raise ValueError("enforced Docker Sandbox template requires a pinned image")
        if self.runtime == "enforced" and any(
            profile.provider == "sandboxd" for profile in self.profiles.values()
        ) and not self.sandboxd.allowed_client_uids:
            raise ValueError("enforced sandboxd requires allowed_client_uids")
        for key, profile in self.profiles.items():
            if key != profile.id:
                raise ValueError(f"profile key {key!r} does not match id {profile.id!r}")
            if (
                self.runtime == "enforced"
                and profile.provider != "remote_http"
                and not re.search(r"@sha256:[a-f0-9]{64}$", profile.image or "")
            ):
                raise ValueError(f"enforced profile {key!r} requires a digest-pinned image")
            if (
                self.runtime == "enforced"
                and profile.provider != "remote_http"
                and profile.toolset_digest is None
            ):
                raise ValueError(f"enforced profile {key!r} requires a toolset digest")
        for tool_id, tool in self.tools.items():
            if not re.fullmatch(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$", tool_id):
                raise ValueError(f"invalid sandbox tool id {tool_id!r}")
            if tool.profile_id not in self.profiles:
                raise ValueError(f"sandbox tool {tool_id!r} references an unknown profile")
            if self.runtime == "enforced" and not re.search(
                r"@sha256:[a-f0-9]{64}$", tool.image
            ):
                raise ValueError(f"enforced sandbox tool {tool_id!r} requires a pinned image")
        return self

    @property
    def digest(self) -> str:
        if self._digest:
            return self._digest
        payload = self.model_dump(mode="json")
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def profile(self, profile_id: str) -> SandboxProfile:
        try:
            return self.profiles[profile_id]
        except KeyError as exc:
            raise ValueError(f"unknown sandbox profile: {profile_id}") from exc


def load_sandbox_config(path: str | Path | None = None) -> tuple[SandboxConfig, Path]:
    resolved = Path(
        path or os.environ.get("TGA_SANDBOX_CONFIG_PATH") or DEFAULT_CONFIG_PATH
    ).expanduser().resolve()
    def reject_duplicates(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"duplicate sandbox config key: {key}")
            value[key] = item
        return value

    payload = json.loads(
        resolved.read_text(encoding="utf-8"),
        object_pairs_hook=reject_duplicates,
    )
    runtime_override = os.environ.get("TGA_SANDBOX_RUNTIME")
    if runtime_override:
        payload["runtime"] = runtime_override
    config = SandboxConfig.model_validate(payload)
    config._digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return config, resolved
