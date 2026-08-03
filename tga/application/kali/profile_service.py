"""Kali Profile projections backed by the enforced sandbox configuration."""

from __future__ import annotations

import json
import hashlib
import os
import re
from pathlib import Path
from uuid import uuid4

from tga.domain.kali import KaliProfile, KaliResourceLimits, KaliToolInfo
from tga.application.kali.models import (
    KaliProfileCreateCommand,
    KaliProfileDetail,
    KaliProfileUpdateCommand,
)
from tga.infrastructure.file_lock import advisory_file_lock
from tga.infrastructure.persistence.errors import PersistenceConflict
from tga.sandbox.config import DEFAULT_CONFIG_PATH, SandboxConfig, load_sandbox_config
from tga.sandbox.models import ResourceLimits, SandboxProfile


PROJECT_ROOT = Path(__file__).resolve().parents[3]
TOOLSET_ROOT = PROJECT_ROOT / "containers" / "kali" / "toolsets" / "generated"


class KaliProfileService:
    def __init__(
        self,
        config: SandboxConfig | None = None,
        *,
        config_path: str | Path | None = None,
        toolset_root: str | Path = TOOLSET_ROOT,
    ) -> None:
        if config is None:
            self.config, loaded_path = load_sandbox_config(config_path)
            self.persisted_config, _ = load_sandbox_config(
                loaded_path, apply_environment=False
            )
            self.config_path = loaded_path
        else:
            self.config = config
            self.persisted_config = config
            self.config_path = Path(config_path or DEFAULT_CONFIG_PATH).resolve()
        self.toolset_root = Path(toolset_root)
        self.config_sha256 = hashlib.sha256(self.config_path.read_bytes()).hexdigest()

    def all(self) -> tuple[KaliProfile, ...]:
        return tuple(
            self._project(profile_id)
            for profile_id, value in sorted(self.config.profiles.items())
            if value.provider != "remote_http"
        )

    def require(self, profile_id: str) -> KaliProfile:
        try:
            raw = self.config.profile(profile_id)
        except ValueError as exc:
            raise KeyError(f"unknown Kali Profile: {profile_id}") from exc
        if raw.provider == "remote_http":
            raise KeyError(f"not a Kali Profile: {profile_id}")
        return self._project(profile_id)

    def verify_binding(self, profile_id: str, capabilities: tuple[str, ...]) -> KaliProfile:
        profile = self.require(profile_id)
        if not profile.enabled:
            raise ValueError(f"Kali Profile {profile_id} is disabled")
        unsupported = set(capabilities) - set(profile.supported_capabilities)
        if unsupported:
            raise ValueError(
                f"Kali Profile {profile_id} does not support capabilities: {sorted(unsupported)}"
            )
        return profile

    def refresh_tool_inventory(self, profile_id: str) -> KaliProfile:
        # Inventory files are immutable image build outputs. Refreshing validates
        # and rereads the current file rather than mutating product configuration.
        return self.require(profile_id)

    def detail(
        self, profile_id: str, *, assigned_solver_ids: tuple[str, ...] = ()
    ) -> KaliProfileDetail:
        profile = self.require(profile_id)
        return KaliProfileDetail.model_validate({
            **profile.model_dump(mode="json"),
            "image": f"{profile.image_name}:{profile.image_tag}",
            "assigned_solver_count": len(assigned_solver_ids),
            "assigned_solver_ids": assigned_solver_ids,
            "config_sha256": self.config_sha256,
        })

    def create(self, command: KaliProfileCreateCommand) -> KaliProfile:
        image = self._command_image(command)
        template = next(
            (
                item
                for item in self.config.profiles.values()
                if item.provider != "remote_http"
                and item.image == image
                and item.allowed_executables == command.allowed_executables
                and item.session_executables == command.session_executables
            ),
            None,
        )
        profile = self._sandbox_profile(command, current=template)
        if profile.id in self.config.profiles:
            raise FileExistsError(f"Kali Profile already exists: {profile.id}")
        return self._replace(profile.id, profile, create=True)

    def update(
        self, profile_id: str, command: KaliProfileUpdateCommand
    ) -> KaliProfile:
        if profile_id not in self.config.profiles:
            raise KeyError(f"unknown Kali Profile: {profile_id}")
        profile = self._sandbox_profile(
            command, current=self.config.profile(profile_id)
        )
        if profile.id != profile_id:
            raise ValueError("Kali Profile path id must match body id")
        return self._replace(profile_id, profile, create=False)

    def delete(self, profile_id: str) -> None:
        current = self.require(profile_id)
        profiles = dict(self.config.profiles)
        del profiles[current.id]
        self._commit_candidate(profiles)

    def _replace(
        self, profile_id: str, profile: SandboxProfile, *, create: bool
    ) -> KaliProfile:
        if profile.provider == "remote_http":
            raise ValueError("remote_http is not a Kali Profile provider")
        profiles = dict(self.config.profiles)
        if create and profile_id in profiles:
            raise FileExistsError(f"Kali Profile already exists: {profile_id}")
        profiles[profile_id] = profile
        self._commit_candidate(profiles)
        return self.require(profile_id)

    @classmethod
    def _sandbox_profile(
        cls,
        command: KaliProfileCreateCommand | KaliProfileUpdateCommand,
        *,
        current: SandboxProfile | None = None,
    ) -> SandboxProfile:
        image = cls._command_image(command)
        base = current.model_dump(mode="json") if current is not None else {
            "id": command.id,
            "provider": "sandboxd",
            "allow_net_raw": False,
            "allow_ptrace": False,
            "toolset_digest": None,
        }
        base.update({
            "id": command.id,
            "display_name": command.display_name,
            "enabled": command.enabled,
            "image": image,
            "network_mode": {
                "disabled": "none",
                "task_target_allowlist": "target_allowlist",
                "unrestricted_with_approval": "public_http",
            }[command.network_mode],
            "supported_capabilities": list(command.supported_capabilities),
            "allowed_executables": list(command.allowed_executables),
            "session_executables": list(command.session_executables),
            "limits": ResourceLimits(
                timeout_seconds=command.limits.timeout_seconds,
                memory_bytes=command.limits.memory_mb * 1024 * 1024,
                cpu_count=command.limits.cpu_cores,
                pids_limit=command.limits.max_processes,
                max_output_bytes=(
                    current.limits.max_output_bytes if current is not None else 262_144
                ),
            ).model_dump(mode="json"),
        })
        return SandboxProfile.model_validate(base)

    @staticmethod
    def _command_image(
        command: KaliProfileCreateCommand | KaliProfileUpdateCommand,
    ) -> str:
        image = command.image_name
        if command.image_tag != "latest":
            image += f":{command.image_tag}"
        if command.image_digest:
            image += "@" + command.image_digest
        return image

    def _commit_candidate(self, profiles: dict[str, SandboxProfile]) -> None:
        effective_candidate = SandboxConfig.model_validate({
            **self.config.model_dump(mode="json"),
            "profiles": {
                profile_id: profile.model_dump(mode="json")
                for profile_id, profile in profiles.items()
            },
        })
        persisted_candidate = SandboxConfig.model_validate({
            **self.persisted_config.model_dump(mode="json"),
            "profiles": {
                profile_id: profile.model_dump(mode="json")
                for profile_id, profile in profiles.items()
            },
        })
        candidate_service = KaliProfileService(
            effective_candidate,
            config_path=self.config_path,
            toolset_root=self.toolset_root,
        )
        from tga.infrastructure.solver_definitions.registry import SolverDefinitionRegistry

        SolverDefinitionRegistry.builtin(kali_profiles=candidate_service)
        payload = persisted_candidate.model_dump(mode="json")
        temporary = self.config_path.with_name(f".{self.config_path.name}.{uuid4().hex}.tmp")
        with advisory_file_lock(self.config_path):
            current_sha256 = hashlib.sha256(self.config_path.read_bytes()).hexdigest()
            if current_sha256 != self.config_sha256:
                raise PersistenceConflict("Kali Profile configuration changed concurrently")
            try:
                temporary.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                os.replace(temporary, self.config_path)
            finally:
                temporary.unlink(missing_ok=True)
        self.config = effective_candidate
        self.persisted_config = persisted_candidate
        self.config_sha256 = hashlib.sha256(self.config_path.read_bytes()).hexdigest()

    def _project(self, profile_id: str) -> KaliProfile:
        raw = self.config.profile(profile_id)
        image_name, image_tag, image_digest = _image_parts(raw.image or "")
        tools = self._tools(profile_id, raw.allowed_executables)
        return KaliProfile(
            id=raw.id,
            display_name=raw.display_name or raw.id,
            image_name=image_name,
            image_tag=image_tag,
            image_digest=image_digest,
            tools=tools,
            supported_capabilities=raw.supported_capabilities,
            allowed_executables=raw.allowed_executables,
            session_executables=raw.session_executables,
            network_mode={
                "none": "disabled",
                "target_allowlist": "task_target_allowlist",
                "public_http": "unrestricted_with_approval",
                "remote": "disabled",
            }[raw.network_mode],
            limits=KaliResourceLimits(
                cpu_cores=raw.limits.cpu_count,
                memory_mb=raw.limits.memory_bytes // (1024 * 1024),
                timeout_seconds=raw.limits.timeout_seconds,
                max_processes=raw.limits.pids_limit,
            ),
            enabled=raw.enabled,
        )

    def _tools(
        self, profile_id: str, allowed_executables: tuple[str, ...]
    ) -> tuple[KaliToolInfo, ...]:
        path = self.toolset_root / f"{profile_id}.json"
        if path.is_file():
            payload = json.loads(path.read_text(encoding="utf-8"))
            source = payload.get("tools") or {}
            if not isinstance(source, dict):
                raise ValueError(f"invalid Kali tool inventory: {path}")
            names = tuple(str(name) for name in source)
        else:
            names = allowed_executables
        return tuple(
            KaliToolInfo(name=name, executable=name)
            for name in names
            if name in allowed_executables
        )


def _image_parts(image: str) -> tuple[str, str, str | None]:
    digest = None
    base = image
    if "@sha256:" in image:
        base, digest_value = image.rsplit("@sha256:", 1)
        if re.fullmatch(r"[a-f0-9]{64}", digest_value) or digest_value == "REPLACE_WITH_RELEASE_DIGEST":
            digest = f"sha256:{digest_value}"
    slash = base.rfind("/")
    colon = base.rfind(":")
    if colon > slash:
        return base[:colon], base[colon + 1 :], digest
    return base, "latest", digest


__all__ = ["KaliProfileService"]
