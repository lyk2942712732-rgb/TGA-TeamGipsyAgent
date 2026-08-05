"""`tga down` must stop through whichever backend actually started the API."""

from __future__ import annotations

import subprocess

import pytest

from tga.deployment import lifecycle, service_manager


class FakeSystemctl:
    """Records systemctl invocations and answers them from a script."""

    def __init__(self, responses: dict[str, tuple[int, str]]):
        self.responses = responses
        self.calls: list[list[str]] = []

    def __call__(self, args, **_kwargs):
        self.calls.append(list(args))
        key = " ".join(args[1:3]) if len(args) > 2 else " ".join(args[1:])
        code, stdout = self.responses.get(key, (1, ""))
        return subprocess.CompletedProcess(args, code, stdout=stdout, stderr="")


@pytest.fixture
def systemd_host(monkeypatch):
    """Present a host where systemd owns tga-api.service."""
    monkeypatch.setattr(service_manager, "systemd_available", lambda: True)
    fake = FakeSystemctl({
        "cat tga-api.service": (0, "[Unit]"),
        "is-active tga-api.service": (0, "active"),
        "show tga-api.service": (0, "4242"),
        "start tga-api.service": (0, ""),
        "stop tga-api.service": (0, ""),
    })
    monkeypatch.setattr(subprocess, "run", fake)
    return fake


def test_no_systemd_means_no_managed_unit(monkeypatch):
    monkeypatch.setattr(service_manager, "systemd_available", lambda: False)
    assert not service_manager.manages_api()
    assert service_manager.state().managed is False


def test_state_reports_active_unit_and_pid(systemd_host):
    state = service_manager.state()
    assert state.managed and state.active
    assert state.main_pid == 4242


def test_zero_main_pid_is_reported_as_absent(monkeypatch):
    monkeypatch.setattr(service_manager, "systemd_available", lambda: True)
    monkeypatch.setattr(subprocess, "run", FakeSystemctl({
        "cat tga-api.service": (0, "[Unit]"),
        "is-active tga-api.service": (0, "inactive"),
        "show tga-api.service": (0, "0"),
    }))
    state = service_manager.state()
    assert state.active is False
    assert state.main_pid is None


def test_unparseable_main_pid_does_not_crash(monkeypatch):
    monkeypatch.setattr(service_manager, "systemd_available", lambda: True)
    monkeypatch.setattr(subprocess, "run", FakeSystemctl({
        "cat tga-api.service": (0, "[Unit]"),
        "is-active tga-api.service": (0, "active"),
        "show tga-api.service": (0, "not-a-number"),
    }))
    assert service_manager.state().main_pid is None


def test_missing_unit_file_is_not_managed(monkeypatch):
    monkeypatch.setattr(service_manager, "systemd_available", lambda: True)
    monkeypatch.setattr(subprocess, "run", FakeSystemctl({}))
    assert not service_manager.manages_api()


def test_systemctl_failure_is_tolerated(monkeypatch):
    monkeypatch.setattr(service_manager, "systemd_available", lambda: True)

    def explode(*_args, **_kwargs):
        raise OSError("systemctl vanished")

    monkeypatch.setattr(subprocess, "run", explode)
    assert not service_manager.unit_installed()


def test_down_stops_through_systemd_not_by_killing(monkeypatch, tmp_path, systemd_host):
    """Killing the pid would just make systemd restart it."""
    monkeypatch.setenv("TGA_STATE_DIR", str(tmp_path / "state"))
    systemd_host.responses["is-active tga-api.service"] = (1, "inactive")

    killed: list[int] = []
    monkeypatch.setattr(lifecycle, "_terminate", lambda pid: killed.append(pid) or True)

    result = lifecycle.down()
    assert result["supervisor"] == "systemd"
    assert result["stopped_process"] is True
    assert killed == []
    assert ["systemctl", "stop", "tga-api.service"] in systemd_host.calls


def test_down_kills_the_child_when_systemd_is_absent(monkeypatch, tmp_path):
    monkeypatch.setenv("TGA_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(service_manager, "manages_api", lambda: False)

    from tga.deployment import state as state_module

    state_module.save(state_module.DeploymentState(phase="ready", api_pid=1234))
    monkeypatch.setattr(state_module, "process_alive", lambda pid: pid == 1234)

    killed: list[int] = []
    monkeypatch.setattr(lifecycle, "_terminate", lambda pid: killed.append(pid) or True)

    result = lifecycle.down()
    assert result["supervisor"] == "launcher"
    assert killed == [1234]


def test_status_trusts_systemd_over_the_state_file(monkeypatch, tmp_path, systemd_host):
    """An out-of-band `systemctl stop` must be visible to `tga status`."""
    monkeypatch.setenv("TGA_STATE_DIR", str(tmp_path / "state"))
    systemd_host.responses["is-active tga-api.service"] = (1, "inactive")

    from tga.deployment import state as state_module

    # The state file still claims a healthy deployment.
    state_module.save(state_module.DeploymentState(
        phase="ready", api_pid=999, api_url="http://127.0.0.1:8123", port=8123
    ))

    payload = lifecycle.status()
    assert payload["supervisor"] == "systemd"
    assert payload["running"] is False
    assert payload["phase"] == "stopped"
