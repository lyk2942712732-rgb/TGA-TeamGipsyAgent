"""Deployment state must survive interruption and never double-start."""

from __future__ import annotations

import json
import os
import threading

import pytest

from tga.deployment import state as state_module
from tga.deployment.errors import DeploymentError, ErrorCode


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv("TGA_STATE_DIR", str(tmp_path / "state"))
    yield


def test_absent_state_reads_as_uninstalled():
    assert state_module.load().phase == "uninstalled"


def test_corrupt_state_does_not_crash_the_launcher():
    """A truncated write must degrade to a fresh state, not an exception."""
    path = state_module.state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    assert state_module.load().phase == "uninstalled"


def test_unknown_fields_are_ignored_across_versions():
    path = state_module.state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"phase": "ready", "port": 8123, "field_from_a_later_version": 1}),
        encoding="utf-8",
    )
    restored = state_module.load()
    assert restored.phase == "ready"
    assert restored.port == 8123


def test_state_round_trips_through_disk():
    original = state_module.DeploymentState(phase="ready", port=8123, api_pid=4321)
    original.mark_completed("start_api")
    state_module.save(original)

    restored = state_module.load()
    assert restored.phase == "ready"
    assert restored.api_pid == 4321
    assert restored.completed("start_api")
    assert restored.updated_at


def test_completed_steps_do_not_duplicate():
    current = state_module.DeploymentState()
    current.mark_completed("start_api")
    current.mark_completed("start_api")
    assert current.completed_steps == ["start_api"]


def test_lock_is_exclusive_between_concurrent_callers():
    """Two `tga up` invocations may not provision at the same time."""
    entered = threading.Event()
    release = threading.Event()
    failure: list[BaseException] = []

    def hold():
        with state_module.locked():
            entered.set()
            release.wait(timeout=5)

    holder = threading.Thread(target=hold)
    holder.start()
    assert entered.wait(timeout=5)

    try:
        with pytest.raises(DeploymentError) as excinfo:
            with state_module.locked(timeout_seconds=0.5):
                failure.append(AssertionError("acquired a held lock"))
        assert excinfo.value.code is ErrorCode.STATE_LOCKED
    finally:
        release.set()
        holder.join(timeout=5)

    assert not failure
    # The lock is released on exit, so the next caller succeeds.
    with state_module.locked(timeout_seconds=2):
        pass


def test_stale_lock_from_a_dead_process_is_reclaimed():
    """A killed `tga up` must not wedge every later invocation."""
    import subprocess
    import sys

    # Use a process that really has exited, so the PID is genuinely dead
    # rather than merely assumed to be.
    finished = subprocess.Popen([sys.executable, "-c", ""])
    finished.wait(timeout=30)
    lock = state_module.state_dir() / "deployment.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(str(finished.pid), encoding="utf-8")

    with state_module.locked(timeout_seconds=2):
        pass
    assert not lock.exists()


def test_a_freshly_created_lock_is_not_mistaken_for_stale():
    """An empty lock may be one microsecond old, not corrupt."""
    lock = state_module.state_dir() / "deployment.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("", encoding="utf-8")

    with pytest.raises(DeploymentError) as excinfo:
        with state_module.locked(timeout_seconds=0.5):
            pass
    assert excinfo.value.code is ErrorCode.STATE_LOCKED


def test_a_long_lived_unreadable_lock_is_eventually_reclaimed(monkeypatch):
    """A genuinely corrupt lock must heal rather than wedge forever."""
    lock = state_module.state_dir() / "deployment.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("not-a-pid", encoding="utf-8")
    monkeypatch.setattr(state_module, "STALE_GRACE_SECONDS", 0.0)

    with state_module.locked(timeout_seconds=2):
        pass
    assert not lock.exists()


def test_process_alive_reports_this_process():
    assert state_module.process_alive(os.getpid())
    assert not state_module.process_alive(None)
