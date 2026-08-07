"""Supervision backend selection: systemd where available, child process else.

A packaged Linux install is supervised by systemd, which restarts the API on
failure and starts it at boot.  A development checkout, a container, or a
Windows host has no systemd, so the launcher supervises the process itself.

`tga up` and `tga down` must work identically on both.  Without this
indirection, `tga down` on a systemd host would kill a process that systemd
immediately restarts, and `tga status` would disagree with `systemctl`.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

API_UNIT = "tga-api.service"
SANDBOXD_UNIT = "tga-sandboxd.service"
API_ENV_FILE = "tga-api.env"
_SAFE_HOST = re.compile(r"[A-Za-z0-9_.:-]+\Z")


@dataclass(frozen=True, slots=True)
class ServiceState:
    """What the supervision backend reports about a unit."""

    managed: bool
    active: bool
    main_pid: int | None = None
    detail: str = ""


def systemd_available() -> bool:
    """Whether this host is actually running systemd as PID 1."""
    if os.name == "nt":
        return False
    if not Path("/run/systemd/system").is_dir():
        return False
    return shutil.which("systemctl") is not None


def unit_installed(unit: str = API_UNIT) -> bool:
    """Whether a unit file exists, regardless of its current state."""
    if not systemd_available():
        return False
    completed = _systemctl("cat", unit)
    return completed is not None and completed.returncode == 0


def manages_api() -> bool:
    """Whether systemd owns the API lifecycle on this host."""
    return unit_installed(API_UNIT)


def state(unit: str = API_UNIT) -> ServiceState:
    """Report a unit's state without changing it."""
    if not unit_installed(unit):
        return ServiceState(managed=False, active=False)
    active = _systemctl("is-active", unit)
    is_active = active is not None and active.stdout.strip() == "active"
    pid = None
    shown = _systemctl("show", unit, "-p", "MainPID", "--value")
    if shown is not None:
        try:
            pid = int(shown.stdout.strip()) or None
        except ValueError:
            pid = None
    return ServiceState(
        managed=True,
        active=is_active,
        main_pid=pid,
        detail=(active.stdout.strip() if active else ""),
    )


def start(unit: str = API_UNIT) -> ServiceState:
    """Start a unit, returning its resulting state."""
    return _change_state("start", unit)


def stop(unit: str = API_UNIT) -> ServiceState:
    """Stop a unit, returning its resulting state."""
    return _change_state("stop", unit)


def restart(unit: str = API_UNIT) -> ServiceState:
    """Restart a unit, returning its resulting state."""
    return _change_state("restart", unit)


def configure_api(host: str, port: int) -> bool:
    """Persist the systemd API bind parameters, returning whether they changed.

    The unit reads this file directly.  Only two validated values are written,
    so a CLI argument cannot inject another EnvironmentFile assignment.
    """
    if not host or _SAFE_HOST.fullmatch(host) is None:
        raise ValueError(f"invalid API host: {host!r}")
    if not 1 <= int(port) <= 65535:
        raise ValueError(f"invalid API port: {port}")

    from tga.deployment.paths import state_dir

    directory = state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / API_ENV_FILE
    content = f"TGA_API_HOST={host}\nTGA_API_PORT={int(port)}\n"
    try:
        if target.read_text(encoding="utf-8") == content:
            return False
    except FileNotFoundError:
        pass
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def _change_state(action: str, unit: str) -> ServiceState:
    completed = _systemctl(action, unit, privileged=True)
    if completed is None or completed.returncode != 0:
        detail = "systemctl could not be executed"
        if completed is not None:
            detail = completed.stderr.strip() or completed.stdout.strip() or detail
        # Callers interpret active=False as a failed start and active=True as a
        # failed stop.  Never let a denied control operation look successful.
        return ServiceState(
            managed=True,
            active=(action == "stop"),
            detail=detail,
        )
    return state(unit)


def _systemctl(*args: str, privileged: bool = False) -> subprocess.CompletedProcess | None:
    systemctl = shutil.which("systemctl") or "systemctl"
    command = [systemctl, *args]
    geteuid = getattr(os, "geteuid", None)
    if privileged and geteuid is not None and geteuid() != 0:
        sudo = shutil.which("sudo")
        if sudo is None:
            return subprocess.CompletedProcess(
                command, 1, stdout="", stderr="sudo is required to manage TGA systemd units"
            )
        command = [sudo, "-n", systemctl, *args]
    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
