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
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

API_UNIT = "tga-api.service"
SANDBOXD_UNIT = "tga-sandboxd.service"


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
    _systemctl("start", unit)
    return state(unit)


def stop(unit: str = API_UNIT) -> ServiceState:
    """Stop a unit, returning its resulting state."""
    _systemctl("stop", unit)
    return state(unit)


def _systemctl(*args: str) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(
            ["systemctl", *args],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
