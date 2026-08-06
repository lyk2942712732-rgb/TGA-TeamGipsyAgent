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
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from tga.deployment.errors import ErrorCode
from tga.sandbox.config import RELEASE_DIGEST_PLACEHOLDER, SandboxConfig

#: A heavy Solver image can take a long while on a slow link; the default is
#: generous because a timeout here means re-downloading gigabytes.
PULL_TIMEOUT_SECONDS = 1800

#: How often a running pull says it is still moving. Docker emits a line per
#: layer transition -- hundreds per image -- which is noise rather than news,
#: but silence for tens of minutes is indistinguishable from a hang.
PROGRESS_INTERVAL_SECONDS = 15.0

#: Where progress lines go. `None` keeps the pull silent, which is what a
#: plain availability check wants.
Progress = Callable[[str], None] | None

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


def _pull(image: str, *, timeout: float, progress: Progress = None) -> tuple[bool, str]:
    """Pull one image, forwarding docker's own account of what it is doing.

    This used to run with the output captured, so a first install fetching tens
    of gigabytes printed nothing at all -- not the image being fetched, not a
    byte count, and not the failure reason, which surfaced only once the whole
    of `tga up` had finished. An operator watching that could not tell a slow
    download from a wedged one.

    stdout and stderr are merged because docker splits an explanation across
    both, and the last line is the one that says why a pull failed.
    """
    try:
        process = subprocess.Popen(
            ["docker", "pull", image],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except OSError as exc:
        return False, str(exc)

    # The watchdog has to interrupt a blocking read, so the timeout cannot be
    # a deadline checked between lines: a stalled pull emits no lines at all.
    timed_out = threading.Event()

    def give_up() -> None:
        timed_out.set()
        process.kill()

    watchdog = threading.Timer(timeout, give_up)
    watchdog.start()

    last_line = ""
    last_report = 0.0
    try:
        for raw in process.stdout or ():
            line = raw.strip()
            if not line:
                continue
            last_line = line
            now = time.monotonic()
            if progress is not None and now - last_report >= PROGRESS_INTERVAL_SECONDS:
                last_report = now
                progress(f"      {line[:110]}")
        process.wait()
    finally:
        watchdog.cancel()
        if process.stdout is not None:
            process.stdout.close()

    if timed_out.is_set():
        return False, f"pull timed out after {timeout:.0f}s"
    if process.returncode == 0:
        return True, ""
    return False, (last_line or "docker pull failed")[:300]


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
    progress: Progress = None,
) -> ImageReport:
    """Report which profile images are on this host, optionally fetching them.

    Never raises for a missing or unpullable image. Sandbox availability is
    graded, not binary: a host with no images still serves the interface and
    reports `degraded`, and turning that into a startup failure would take the
    machine away from a user who could still do useful work on it.

    `progress` receives one line per image and, during a pull, docker's own
    status at intervals. Fetching tens of gigabytes is the longest thing TGA
    ever does; doing it silently leaves nothing to distinguish work from a
    hang, and no way to see which image failed until the very end.
    """
    report = ImageReport(docker_available=shutil.which("docker") is not None)

    wanted = wanted_images(config)
    total = len(wanted)
    for index, (profile_id, image) in enumerate(wanted, start=1):
        position = f"[{index}/{total}]"
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
            _say(progress, f"  {position} {profile_id}: already present")
            continue

        if not pull:
            status.code = ErrorCode.PROFILE_IMAGE_MISSING
            status.detail = "not present locally"
            continue

        _say(progress, f"  {position} {profile_id}: pulling {_repository(image)}")
        started = time.monotonic()
        ok, detail = _pull(image, timeout=timeout_seconds, progress=progress)
        elapsed = time.monotonic() - started
        if ok:
            status.present = True
            status.pulled = True
            _say(progress, f"  {position} {profile_id}: pulled in {elapsed:.0f}s")
        else:
            status.code = ErrorCode.PROFILE_IMAGE_MISSING
            status.detail = detail
            # Named here as well as in the report: at twenty-two images the
            # summary arrives long after the operator stopped watching.
            _say(progress, f"  {position} {profile_id}: FAILED after {elapsed:.0f}s -- {detail}")

    return report


def _say(progress: Progress, line: str) -> None:
    if progress is not None:
        progress(line)


def _repository(image: str) -> str:
    """The image without its digest, which is 71 characters of no help here."""
    return image.split("@", 1)[0]
