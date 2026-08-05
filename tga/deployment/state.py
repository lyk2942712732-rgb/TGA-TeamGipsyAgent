"""Durable deployment state with a cross-process lock.

`tga up` must be idempotent and interruptible.  Running it twice may not start
two API processes, and killing it halfway through provisioning may not leave an
installation that can only be repaired by hand: the next `tga up` resumes from
the last completed step.  Both properties need state that outlives the process,
so it is written to disk and guarded by an OS-level lock.
"""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator, Literal

from tga.deployment.errors import DeploymentError, ErrorCode
from tga.deployment.paths import state_dir

DeploymentPhase = Literal[
    "uninstalled",
    "installing",
    "installed",
    "starting",
    "ready",
    "degraded",
    "stopping",
    "stopped",
    "failed",
]

#: Steps recorded as they complete so an interrupted `tga up` can resume.
PROVISION_STEPS = (
    "detect_platform",
    "ensure_runtime_installed",
    "ensure_configuration",
    "ensure_images",
    "start_container_engine",
    "start_sandboxd",
    "start_api",
    "wait_for_readiness",
)


@dataclass
class DeploymentState:
    """The persisted view of what has been provisioned and what is running."""

    phase: DeploymentPhase = "uninstalled"
    completed_steps: list[str] = field(default_factory=list)
    api_pid: int | None = None
    api_url: str = ""
    host: str = "127.0.0.1"
    port: int = 0
    #: Which backend owns the process: "systemd" or "launcher".
    supervisor: str = ""
    last_error_code: str = ""
    last_error_detail: str = ""
    updated_at: str = ""

    def completed(self, step: str) -> bool:
        return step in self.completed_steps

    def mark_completed(self, step: str) -> None:
        if step not in self.completed_steps:
            self.completed_steps.append(step)

    def reset_steps(self) -> None:
        self.completed_steps = []


def state_path() -> Path:
    return state_dir() / "deployment.json"


def _lock_path() -> Path:
    return state_dir() / "deployment.lock"


def load() -> DeploymentState:
    """Read persisted state, tolerating absence and corruption."""
    path = state_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return DeploymentState()
    known = {item.name for item in DeploymentState.__dataclass_fields__.values()}
    return DeploymentState(**{k: v for k, v in payload.items() if k in known})


def save(state: DeploymentState) -> None:
    """Persist state atomically so a crash cannot truncate it."""
    state.updated_at = datetime.now(UTC).isoformat()
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(asdict(state), indent=2, sort_keys=True), encoding="utf-8"
    )
    temporary.replace(path)


@contextmanager
def locked(timeout_seconds: float = 30.0) -> Iterator[None]:
    """Hold the exclusive deployment lock for the duration of the block.

    Uses atomic ``O_EXCL`` creation, which behaves consistently on Windows and
    Linux.  A lock whose owning PID is gone is treated as stale and reclaimed,
    so a killed `tga up` does not wedge every later invocation.
    """
    path = _lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_seconds
    handle = None
    while True:
        try:
            handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            if _reclaim_if_stale(path):
                continue
            if time.monotonic() >= deadline:
                raise DeploymentError(
                    ErrorCode.STATE_LOCKED, f"{path} held by another process"
                ) from None
            time.sleep(0.2)
    try:
        os.write(handle, str(os.getpid()).encode())
        os.close(handle)
        handle = None
        yield
    finally:
        if handle is not None:
            os.close(handle)
        path.unlink(missing_ok=True)


#: How long a lock may carry an unreadable owner before it is presumed stale.
#: Acquisition creates the file and writes the PID as two operations, so a
#: lock observed empty may simply be one microsecond old.  Reclaiming it
#: immediately would let two callers hold the lock at once; waiting out this
#: grace period cannot, while still healing a genuinely corrupt file.
STALE_GRACE_SECONDS = 30.0


def _reclaim_if_stale(path: Path) -> bool:
    """Remove a lock file that no live process can still own."""
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return False

    try:
        owner = int(raw)
    except ValueError:
        owner = 0

    if owner > 0:
        # A named owner is authoritative: reclaim as soon as it is gone.
        if _process_alive(owner):
            return False
        path.unlink(missing_ok=True)
        return True

    # No usable owner. Either the writer has not got there yet, or the file is
    # corrupt; only age can tell the two apart.
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        return False
    if age < STALE_GRACE_SECONDS:
        return False
    path.unlink(missing_ok=True)
    return True


def _process_alive(pid: int) -> bool:
    if os.name == "nt":
        import subprocess

        try:
            completed = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return True
        return str(pid) in completed.stdout
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def process_alive(pid: int | None) -> bool:
    """Public probe used to decide whether a recorded API process still runs."""
    return bool(pid) and _process_alive(int(pid))
