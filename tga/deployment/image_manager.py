"""Getting the images a profile names onto the host, and saying so honestly.

Every enforced profile is pinned to `repo@sha256:...`, which names one exact
image. That makes "is it here?" and "is it the right one?" the same question:
`docker image inspect` on the digest reference answers both, and a pull by
digest cannot quietly hand back something else. Nothing here re-hashes an
image after pulling it, because the registry contract already did.

`tga up` does not pull by default. The twenty-two Solver images run to tens of
gigabytes, and a first run that silently spends an hour downloading -- inside a
ninety-second readiness budget -- would be worse than one that says what is
missing and how to fetch it. `ensure_images(pull=True)` is the explicit path.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field

from tga.deployment.errors import ErrorCode
from tga.sandbox.config import RELEASE_DIGEST_PLACEHOLDER, SandboxConfig

#: A heavy Solver image can take a long while on a slow link; the default is
#: generous because a timeout here means re-downloading gigabytes.
PULL_TIMEOUT_SECONDS = 1800

#: Profiles served over HTTP have no image of their own to fetch.
IMAGELESS_PROVIDERS = frozenset({"remote_http"})


@dataclass
class ImageStatus:
    profile_id: str
    image: str
    present: bool = False
    pulled: bool = False
    code: ErrorCode | None = None
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "profile_id": self.profile_id,
            "image": self.image,
            "present": self.present,
            "pulled": self.pulled,
            **({"code": str(self.code)} if self.code else {}),
            **({"detail": self.detail} if self.detail else {}),
        }


@dataclass
class ImageReport:
    statuses: list[ImageStatus] = field(default_factory=list)
    docker_available: bool = True

    @property
    def present(self) -> list[ImageStatus]:
        return [status for status in self.statuses if status.present]

    @property
    def missing(self) -> list[ImageStatus]:
        return [status for status in self.statuses if not status.present]

    @property
    def unpinned(self) -> list[ImageStatus]:
        return [
            status
            for status in self.statuses
            if status.code is ErrorCode.PROFILE_DIGEST_INVALID
        ]

    @property
    def ok(self) -> bool:
        """True only when every image a profile names is on this host."""
        return bool(self.statuses) and not self.missing

    def summary(self) -> str:
        return f"{len(self.present)}/{len(self.statuses)} images present"

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "docker_available": self.docker_available,
            "present": len(self.present),
            "total": len(self.statuses),
            "images": [status.to_dict() for status in self.statuses],
        }


def _pinned(image: str) -> bool:
    """A reference is usable only if it names a digest that actually exists."""
    if RELEASE_DIGEST_PLACEHOLDER in image:
        return False
    return "@sha256:" in image


def _docker(*args: str, timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _present_locally(image: str) -> bool:
    try:
        return _docker("image", "inspect", image, timeout=60).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _pull(image: str, *, timeout: float) -> tuple[bool, str]:
    try:
        completed = _docker("pull", image, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"pull timed out after {timeout:.0f}s"
    except OSError as exc:
        return False, str(exc)
    if completed.returncode == 0:
        return True, ""
    # Docker puts the useful line on stderr; the last one carries the reason.
    lines = [line for line in completed.stderr.strip().splitlines() if line.strip()]
    return False, (lines[-1] if lines else "docker pull failed")[:300]


def wanted_images(config: SandboxConfig) -> list[tuple[str, str]]:
    """Every (profile_id, image) this configuration expects to be able to run."""
    wanted: list[tuple[str, str]] = []
    for profile_id, profile in sorted(config.profiles.items()):
        if profile.provider in IMAGELESS_PROVIDERS:
            continue
        image = profile.image or ""
        if not image:
            continue
        wanted.append((profile_id, image))
    return wanted


def ensure_images(
    config: SandboxConfig,
    *,
    pull: bool = False,
    timeout_seconds: float = PULL_TIMEOUT_SECONDS,
) -> ImageReport:
    """Report which profile images are on this host, optionally fetching them.

    Never raises for a missing or unpullable image. Sandbox availability is
    graded, not binary: a host with no images still serves the interface and
    reports `degraded`, and turning that into a startup failure would take the
    machine away from a user who could still do useful work on it.
    """
    report = ImageReport(docker_available=shutil.which("docker") is not None)

    for profile_id, image in wanted_images(config):
        status = ImageStatus(profile_id=profile_id, image=image)
        report.statuses.append(status)

        if not _pinned(image):
            status.code = ErrorCode.PROFILE_DIGEST_INVALID
            status.detail = "image is not pinned to a real digest"
            continue

        if not report.docker_available:
            status.code = ErrorCode.DOCKER_UNAVAILABLE
            status.detail = "docker is not installed, so images cannot be checked"
            continue

        if _present_locally(image):
            status.present = True
            continue

        if not pull:
            status.code = ErrorCode.PROFILE_IMAGE_MISSING
            status.detail = "not present locally"
            continue

        ok, detail = _pull(image, timeout=timeout_seconds)
        if ok:
            status.present = True
            status.pulled = True
        else:
            status.code = ErrorCode.PROFILE_IMAGE_MISSING
            status.detail = detail

    return report
