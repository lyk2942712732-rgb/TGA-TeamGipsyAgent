"""Non-invasive Kali readiness inspection and execution-time profile gates."""

from __future__ import annotations

import platform
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

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
    """Return lightweight status without pulling images or contacting providers."""
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

    profiles = {
        profile_id: inspect_kali_profile(config, profile)
        for profile_id, profile in sorted(config.profiles.items())
        if profile.provider != "remote_http"
    }
    statuses = {item.status for item in profiles.values()}
    if any(status in {"unresolved_digest", "unknown", "toolset_mismatch", "tools_missing"} for status in statuses):
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
    config: SandboxConfig, profile: SandboxProfile
) -> KaliProfileReadiness:
    image = profile.image
    image_reason = _image_readiness_reason(image)
    runtime_status = _runtime_status(config, profile)
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
    if runtime_status.endswith("_unavailable"):
        return KaliProfileReadiness(
            status="runtime_unavailable",
            image=image,
            image_status="image_unverified",
            runtime_status=runtime_status,
            reasons=(f"runtime provider is unavailable: {runtime_status}",),
            expected_toolset_digest=profile.toolset_digest,
        )
    return KaliProfileReadiness(
        status="image_unverified",
        image=image,
        image_status="image_unverified",
        runtime_status=runtime_status,
        reasons=("image has not completed the project verification workflow",),
        expected_toolset_digest=profile.toolset_digest,
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


def _runtime_status(config: SandboxConfig, profile: SandboxProfile) -> str:
    if config.runtime == "disabled":
        return "disabled"
    if profile.provider == "sandboxd":
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


__all__ = [
    "KaliHealthStatus",
    "KaliProfileNotReadyError",
    "KaliProfileReadiness",
    "KaliReadinessReport",
    "ensure_kali_profile_ready",
    "inspect_kali_profile",
    "inspect_kali_runtime_readiness",
]
