"""Capability-graded readiness for the whole TGA deployment.

`tga up` must not report success on the strength of `/api/health` alone: that
endpoint proves a process is listening, not that tasks can run or that tool
execution is actually isolated.  This module probes every layer independently
and grades the result, so an operator can tell the difference between "TGA is
serving with real sandbox isolation" and "TGA is serving but tools would run
unconfined".

Grading contract:

``ready``
    Core serving path (API + storage) is usable.  ``tga up`` succeeds.
``degraded``
    Core is usable but sandbox isolation is not enforced.  ``tga up`` still
    succeeds and prints exactly which capability is missing.
``failed``
    Core is broken.  ``tga up`` fails.
"""

from __future__ import annotations

import re
import shutil
import socket
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from tga.deployment.errors import ErrorCode
from tga.deployment.paths import run_root

CheckStatus = Literal["ready", "unavailable", "disabled", "unknown"]
OverallStatus = Literal["ready", "degraded", "failed"]

_DIGEST_PINNED = re.compile(r"@sha256:[a-f0-9]{64}$")


@dataclass(frozen=True, slots=True)
class Check:
    """One independently observable deployment capability."""

    name: str
    status: CheckStatus
    detail: str = ""
    code: ErrorCode | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ready"

    def to_dict(self) -> dict[str, str]:
        payload: dict[str, str] = {"status": self.status}
        if self.detail:
            payload["detail"] = self.detail
        if self.code is not None:
            payload["code"] = str(self.code)
        return payload


@dataclass(slots=True)
class ReadinessReport:
    """Aggregated deployment readiness, shaped for the launcher contract."""

    api: Check
    storage: Check
    sandbox_runtime: str
    sandbox: list[Check] = field(default_factory=list)
    profiles: list[Check] = field(default_factory=list)

    @property
    def core_ready(self) -> bool:
        return self.api.ok and self.storage.ok

    @property
    def sandbox_ready(self) -> bool:
        return bool(self.sandbox) and all(check.ok for check in self.sandbox)

    @property
    def profiles_ready(self) -> bool:
        return bool(self.profiles) and all(check.ok for check in self.profiles)

    @property
    def status(self) -> OverallStatus:
        if not self.core_ready:
            return "failed"
        if self.sandbox_runtime != "enforced":
            # Isolation was deliberately switched off; that is a degraded
            # posture, never a healthy one.
            return "degraded"
        return "ready" if self.sandbox_ready and self.profiles_ready else "degraded"

    @property
    def ready(self) -> bool:
        """Whether TGA can serve. Degraded deployments still serve."""
        return self.core_ready

    def failures(self) -> list[Check]:
        checks = [self.api, self.storage, *self.sandbox, *self.profiles]
        return [check for check in checks if check.status not in {"ready", "disabled"}]

    def to_dict(self) -> dict[str, object]:
        return {
            "ready": self.ready,
            "status": self.status,
            "api": self.api.to_dict(),
            "storage": self.storage.to_dict(),
            "sandbox": {
                "runtime": self.sandbox_runtime,
                **{check.name: check.to_dict() for check in self.sandbox},
            },
            "profiles": {check.name: check.to_dict() for check in self.profiles},
            "errors": [
                {"component": check.name, "code": str(check.code), "detail": check.detail}
                for check in self.failures()
                if check.code is not None
            ],
        }


def evaluate() -> ReadinessReport:
    """Probe every layer once and grade the deployment."""
    return ReadinessReport(
        api=_check_api(),
        storage=_check_storage(),
        sandbox_runtime=_sandbox_runtime(),
        sandbox=_check_sandbox(),
        profiles=_check_profiles(),
    )


def _check_api() -> Check:
    # Evaluated in-process by the API itself; reaching this code proves the
    # application object imported and its routes resolved.
    return Check("api", "ready")


def _check_storage() -> Check:
    try:
        root = run_root()
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ".tga-readiness-probe"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return Check("storage", "unavailable", f"{run_root()}: {exc}", ErrorCode.RUN_ROOT_UNWRITABLE)
    return Check("storage", "ready", str(run_root()))


def _sandbox_runtime() -> str:
    try:
        from tga.sandbox.config import load_sandbox_config

        return load_sandbox_config()[0].runtime
    except Exception:
        return "unknown"


def _check_sandbox() -> list[Check]:
    """Probe the isolation stack, one capability per check."""
    try:
        from tga.sandbox.config import load_sandbox_config

        config, _ = load_sandbox_config()
    except Exception as exc:
        detail = f"sandbox.json unreadable: {exc}"
        return [
            Check(name, "unknown", detail, ErrorCode.SANDBOX_RUNTIME_DISABLED)
            for name in ("sandboxd", "docker", "runsc", "nftables", "cgroup_v2")
        ]

    if config.runtime != "enforced":
        detail = "sandbox runtime is 'disabled'; tool execution is not isolated"
        return [
            Check(name, "disabled", detail, ErrorCode.SANDBOX_RUNTIME_DISABLED)
            for name in ("sandboxd", "docker", "runsc", "nftables", "cgroup_v2")
        ]

    health = _sandboxd_health(config)
    if health is None:
        return [
            Check(
                "sandboxd",
                "unavailable",
                f"no response on {config.sandboxd.socket_path}",
                ErrorCode.SANDBOXD_SOCKET_MISSING,
            ),
            *(
                Check(name, "unknown", "sandboxd did not answer", ErrorCode.SANDBOXD_SOCKET_MISSING)
                for name in ("docker", "runsc", "nftables", "cgroup_v2")
            ),
        ]

    checks = [Check("sandboxd", "ready")]
    for name, available, code in (
        ("docker", health.docker_available, ErrorCode.DOCKER_UNAVAILABLE),
        ("runsc", health.runsc_available, ErrorCode.RUNSC_NOT_REGISTERED),
        ("nftables", health.nftables_available, ErrorCode.NFTABLES_UNAVAILABLE),
        ("cgroup_v2", health.cgroup_v2_available, ErrorCode.CGROUP_V2_UNAVAILABLE),
    ):
        checks.append(
            Check(name, "ready") if available else Check(name, "unavailable", "", code)
        )
    if not health.client_uid_policy_active:
        checks.append(
            Check("client_uid_policy", "unavailable", "", ErrorCode.SANDBOXD_UID_DENIED)
        )
    return checks


def _sandboxd_health(config):
    """Call the sandboxd Health RPC, returning None when it is unreachable."""
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


def _check_profiles() -> list[Check]:
    """Verify each sandbox profile is digest-pinned and locally present."""
    try:
        from tga.sandbox.config import load_sandbox_config

        config, _ = load_sandbox_config()
    except Exception as exc:
        return [Check("sandbox.json", "unknown", str(exc), ErrorCode.PROFILE_DIGEST_INVALID)]

    enforced = config.runtime == "enforced"
    local_images = _local_image_digests() if enforced else None
    checks: list[Check] = []
    for profile_id, profile in sorted(config.profiles.items()):
        if profile.provider == "remote_http":
            checks.append(Check(profile_id, "ready", "remote_http"))
            continue
        image = profile.image or ""
        if not _DIGEST_PINNED.search(image):
            checks.append(
                Check(
                    profile_id,
                    "disabled" if not enforced else "unavailable",
                    "image is not digest-pinned",
                    ErrorCode.PROFILE_DIGEST_INVALID,
                )
            )
            continue
        if not enforced:
            checks.append(Check(profile_id, "disabled", "sandbox runtime disabled"))
            continue
        digest = image.rsplit("@", 1)[-1]
        if local_images is None:
            # The store said nothing, so neither do we: claiming the profile is
            # ready would be asserting a fact we never established.
            checks.append(
                Check(
                    profile_id,
                    "unknown",
                    "cannot list the local image store",
                    ErrorCode.PROFILE_IMAGE_MISSING,
                )
            )
            continue
        if digest not in local_images:
            checks.append(
                Check(profile_id, "unavailable", "image not present locally", ErrorCode.PROFILE_IMAGE_MISSING)
            )
            continue
        checks.append(Check(profile_id, "ready"))
    return checks


def _local_image_digests() -> set[str] | None:
    """Digests of images already present in the local container store.

    ``None`` and the empty set mean opposite things, and conflating them is
    what let a host holding no images at all grade every profile ready:

    ``None``
        The store could not be listed -- no docker binary, a daemon that
        refused, a timeout.  That is evidence about nothing, so no profile
        may be graded on it.
    ``set()``
        The store answered, and it holds nothing.  Every profile is missing.
    """
    docker = shutil.which("docker")
    if not docker:
        return None
    try:
        completed = subprocess.run(
            [docker, "images", "--digests", "--format", "{{.Digest}}"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return {line.strip() for line in completed.stdout.splitlines() if line.strip().startswith("sha256:")}


def port_is_free(host: str, port: int) -> bool:
    """Whether a listener can still bind host:port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True
