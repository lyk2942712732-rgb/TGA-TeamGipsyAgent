"""Non-invasive Kali readiness inspection and execution-time profile gates."""

from __future__ import annotations

import platform
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from tga.sandbox.config import RELEASE_DIGEST_PLACEHOLDER, SandboxConfig, load_sandbox_config
from tga.sandbox.models import SandboxProfile
from tga.sandbox.provider import SandboxError


KaliHealthStatus = Literal[
    "host_only",
    "unknown",
    "runtime_disabled",
    "unresolved_digest",
    "image_unreachable",
    "image_not_found",
    "image_unverified",
    "toolset_mismatch",
    "tools_missing",
    "runtime_unavailable",
    "healthy",
]


class KaliProfileReadiness(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: KaliHealthStatus
    image: str | None
    image_status: str
    runtime_status: str
    reasons: tuple[str, ...] = ()
    missing_executables: tuple[str, ...] = ()
    expected_toolset_digest: str | None = None
    actual_toolset_digest: str | None = None
    image_store_status: Literal["not_applicable", "unknown", "unreadable", "readable"] = "unknown"
    image_store_error: str | None = None


class KaliReadinessReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    overall: Literal["disabled", "degraded", "not_ready", "healthy"]
    runtime_mode: Literal["disabled", "enforced", "unknown"]
    profiles: dict[str, KaliProfileReadiness] = Field(default_factory=dict)
    checked_at: str
    reasons: tuple[str, ...] = ()


class KaliProfileNotReadyError(SandboxError):
    """A known Profile configuration problem detected before provider access."""

    def __init__(self, profile_id: str, reason: str, message: str):
        super().__init__(
            f"Kali Profile {profile_id} is not ready: {message}",
            code="KALI_PROFILE_NOT_READY",
        )
        self.profile_id = profile_id
        self.reason = reason


def inspect_kali_runtime_readiness(
    config: SandboxConfig | None = None,
    *,
    config_path: str | Path | None = None,
) -> KaliReadinessReport:
    """Return status from configuration plus sandboxd's non-mutating Health RPC."""
    checked_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    if config is None:
        try:
            config, _ = load_sandbox_config(config_path)
        except (OSError, ValueError) as exc:
            return KaliReadinessReport(
                overall="not_ready",
                runtime_mode="unknown",
                checked_at=checked_at,
                reasons=(f"sandbox configuration could not be loaded: {exc}",),
            )

    sandboxd_health = _sandboxd_health(config) if config.runtime == "enforced" else None
    profiles = {
        profile_id: inspect_kali_profile(config, profile, sandboxd_health=sandboxd_health)
        for profile_id, profile in sorted(config.profiles.items())
        if profile.provider != "remote_http"
    }
    statuses = {item.status for item in profiles.values()}
    if any(status in {
        "unresolved_digest", "unknown", "image_unreachable", "image_not_found",
        "toolset_mismatch", "tools_missing",
    } for status in statuses):
        overall = "not_ready"
    elif config.runtime == "disabled":
        overall = "disabled"
    elif any(status == "runtime_unavailable" for status in statuses):
        overall = "not_ready"
    elif any(status == "image_unverified" for status in statuses):
        overall = "degraded"
    else:
        overall = "healthy"
    return KaliReadinessReport(
        overall=overall,
        runtime_mode=config.runtime,
        profiles=profiles,
        checked_at=checked_at,
    )


def inspect_kali_profile(
    config: SandboxConfig, profile: SandboxProfile, *, sandboxd_health: Any | None = None,
) -> KaliProfileReadiness:
    image = profile.image
    image_reason = _image_readiness_reason(image)
    runtime_status = _runtime_status(config, profile, sandboxd_health=sandboxd_health)
    if image_reason is not None:
        return KaliProfileReadiness(
            status="unresolved_digest",
            image=image,
            image_status="unresolved_digest",
            runtime_status=runtime_status,
            reasons=(image_reason[1],),
            expected_toolset_digest=profile.toolset_digest,
        )
    if not profile.enabled:
        return KaliProfileReadiness(
            status="unknown",
            image=image,
            image_status="image_unverified",
            runtime_status=runtime_status,
            reasons=("profile is disabled",),
            expected_toolset_digest=profile.toolset_digest,
        )
    if profile.toolset_digest is None:
        return KaliProfileReadiness(
            status="toolset_mismatch",
            image=image,
            image_status="image_unverified",
            runtime_status=runtime_status,
            reasons=("profile has no expected toolset digest",),
        )
    if config.runtime == "disabled":
        return KaliProfileReadiness(
            status="runtime_disabled",
            image=image,
            image_status="image_unverified",
            runtime_status="disabled",
            reasons=("Kali runtime is disabled",),
            expected_toolset_digest=profile.toolset_digest,
        )
    if runtime_status.endswith("_unavailable") and profile.provider != "sandboxd":
        return KaliProfileReadiness(
            status="runtime_unavailable",
            image=image,
            image_status="image_unverified",
            runtime_status=runtime_status,
            reasons=(f"runtime provider is unavailable: {runtime_status}",),
            expected_toolset_digest=profile.toolset_digest,
        )
    if profile.provider == "sandboxd":
        if sandboxd_health is None:
            return KaliProfileReadiness(
                status="runtime_unavailable",
                image=image,
                image_status="image_unverified",
                runtime_status="sandboxd_unavailable",
                reasons=("sandboxd Health RPC did not answer",),
                expected_toolset_digest=profile.toolset_digest,
                image_store_status="unknown",
            )
        runtime_reason = _sandboxd_runtime_reason(sandboxd_health)
        runtime_status = "sandboxd_unavailable" if runtime_reason else "sandboxd_available"
        if not getattr(sandboxd_health, "image_store_readable", False):
            error = str(
                getattr(sandboxd_health, "image_store_error", "")
                or "Docker image store is not readable"
            )
            reasons = (error,) if not runtime_reason else (error, runtime_reason)
            return KaliProfileReadiness(
                status="image_unreachable",
                image=image,
                image_status="image_unreachable",
                runtime_status=runtime_status,
                reasons=reasons,
                expected_toolset_digest=profile.toolset_digest,
                image_store_status="unreadable",
                image_store_error=error,
            )
        digest = image.rsplit("@", 1)[-1]
        local_digests = {
            str(value).strip()
            for value in getattr(sandboxd_health, "local_image_digests", ())
            if str(value).strip()
        }
        if digest not in local_digests:
            reasons = ("digest-pinned image is not present in the local Docker image store",)
            if runtime_reason:
                reasons += (runtime_reason,)
            return KaliProfileReadiness(
                status="image_not_found",
                image=image,
                image_status="image_not_found",
                runtime_status=runtime_status,
                reasons=reasons,
                expected_toolset_digest=profile.toolset_digest,
                image_store_status="readable",
            )
        if runtime_reason:
            return KaliProfileReadiness(
                status="runtime_unavailable",
                image=image,
                image_status="healthy",
                runtime_status="sandboxd_unavailable",
                reasons=(runtime_reason,),
                expected_toolset_digest=profile.toolset_digest,
                image_store_status="readable",
            )
        return KaliProfileReadiness(
            status="healthy",
            image=image,
            image_status="healthy",
            runtime_status="sandboxd_available",
            expected_toolset_digest=profile.toolset_digest,
            image_store_status="readable",
        )
    return KaliProfileReadiness(
        status="image_unverified",
        image=image,
        image_status="image_unverified",
        runtime_status=runtime_status,
        reasons=("image has not completed the project verification workflow",),
        expected_toolset_digest=profile.toolset_digest,
        image_store_status="not_applicable",
    )


def ensure_kali_profile_ready(
    profile_id: str,
    config: SandboxConfig | None = None,
    *,
    profile: SandboxProfile | None = None,
    config_path: str | Path | None = None,
) -> None:
    """Reject known unsafe Profile state at the Kali execution boundary."""
    if config is None:
        config, _ = load_sandbox_config(config_path)
    try:
        selected = profile or config.profile(profile_id)
    except ValueError as exc:
        raise KaliProfileNotReadyError(profile_id, "profile_not_found", str(exc)) from exc
    if selected.id != profile_id:
        raise KaliProfileNotReadyError(
            profile_id,
            "profile_snapshot_mismatch",
            f"snapshot belongs to {selected.id}",
        )
    image_reason = _image_readiness_reason(selected.image)
    if image_reason is not None:
        raise KaliProfileNotReadyError(profile_id, *image_reason)
    if not selected.enabled:
        raise KaliProfileNotReadyError(profile_id, "profile_disabled", "profile is disabled")
    if selected.toolset_digest is None:
        raise KaliProfileNotReadyError(
            profile_id,
            "toolset_digest_missing",
            "expected toolset digest is not configured",
        )
    if config.runtime != "enforced":
        raise KaliProfileNotReadyError(
            profile_id,
            "runtime_disabled",
            "Kali runtime is disabled",
        )
    if selected.provider == "docker_sandbox":
        template_reason = _image_readiness_reason(config.docker_sandbox.template)
        if template_reason is not None:
            raise KaliProfileNotReadyError(
                profile_id,
                "unresolved_sandbox_template_digest",
                template_reason[1],
            )
    if selected.provider == "sandboxd" and not config.sandboxd.allowed_client_uids:
        raise KaliProfileNotReadyError(
            profile_id,
            "sandboxd_client_policy_missing",
            "sandboxd allowed client UID policy is not configured",
        )


def _image_readiness_reason(image: str | None) -> tuple[str, str] | None:
    if not image:
        return "unresolved_image_digest", "image is not configured"
    if RELEASE_DIGEST_PLACEHOLDER in image:
        return "unresolved_image_digest", "image digest has not been resolved"
    if not re.search(r"@sha256:[a-f0-9]{64}$", image):
        return "unresolved_image_digest", "image does not use a valid immutable digest"
    return None


def _runtime_status(
    config: SandboxConfig, profile: SandboxProfile, *, sandboxd_health: Any | None = None,
) -> str:
    if config.runtime == "disabled":
        return "disabled"
    if profile.provider == "sandboxd":
        if sandboxd_health is not None:
            return "sandboxd_available"
        if platform.system() != "Linux":
            return "sandboxd_unavailable"
        if not Path(config.sandboxd.socket_path).exists():
            return "sandboxd_unavailable"
        return "sandboxd_available"
    if profile.provider == "docker_sandbox":
        return (
            "docker_sandbox_available"
            if shutil.which(config.docker_sandbox.executable)
            else "docker_sandbox_unavailable"
        )
    return "provider_unavailable"


def _sandboxd_health(config: SandboxConfig):
    """Return privileged runtime and image-store facts from sandboxd."""
    try:
        from tga.sandbox.api.sandbox.v1 import sandbox_pb2
        from tga.sandbox.sandboxd_provider import SandboxdProvider

        provider = SandboxdProvider(config)
        return provider._client().Health(
            sandbox_pb2.HealthRequest(
                protocol_major=config.sandboxd.protocol_major,
                config_digest=config.digest,
            ),
            timeout=config.sandboxd.rpc_timeout_seconds,
        )
    except Exception:
        return None


def _sandboxd_runtime_reason(health: Any) -> str | None:
    checks = (
        ("docker_available", "Docker is unavailable to sandboxd"),
        ("runsc_available", "runsc is unavailable"),
        ("runsc_runtime_registered", "Docker has not registered the runsc runtime"),
        ("nftables_available", "nftables is unavailable"),
        ("cgroup_v2_available", "cgroup v2 is unavailable"),
        ("client_uid_policy_active", "sandboxd client UID policy is inactive"),
    )
    for field, reason in checks:
        if not getattr(health, field, True):
            return reason
    return None


__all__ = [
    "KaliHealthStatus",
    "KaliProfileNotReadyError",
    "KaliProfileReadiness",
    "KaliReadinessReport",
    "ensure_kali_profile_ready",
    "inspect_kali_profile",
    "inspect_kali_runtime_readiness",
]
