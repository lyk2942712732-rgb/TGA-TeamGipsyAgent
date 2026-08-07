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
    monkeypatch.setattr(service_manager.os, "geteuid", lambda: 0, raising=False)
    fake = FakeSystemctl({
        "cat tga-api.service": (0, "[Unit]"),
        "is-active tga-api.service": (0, "active"),
        "show tga-api.service": (0, "4242"),
        "start tga-api.service": (0, ""),
        "stop tga-api.service": (0, ""),
        "restart tga-api.service": (0, ""),
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


def test_non_root_service_changes_use_scoped_noninteractive_sudo(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(service_manager.os, "geteuid", lambda: 1000, raising=False)
    monkeypatch.setattr(
        service_manager.shutil,
        "which",
        lambda name: {"systemctl": "/usr/bin/systemctl", "sudo": "/usr/bin/sudo"}.get(name),
    )

    def run(args, **_kwargs):
        calls.append(list(args))
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", run)

    completed = service_manager._systemctl(
        "restart", service_manager.API_UNIT, privileged=True
    )

    assert completed and completed.returncode == 0
    assert calls == [[
        "/usr/bin/sudo", "-n", "/usr/bin/systemctl", "restart", "tga-api.service"
    ]]


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


@pytest.mark.parametrize(
    ("host", "port"),
    [("127.0.0.1", 8123), ("0.0.0.0", 8173)],
)
def test_systemd_api_receives_requested_bind_and_restarts(
    monkeypatch, tmp_path, systemd_host, host, port
):
    """The system unit must not replace `up --public` with hard-coded localhost."""
    monkeypatch.setenv("TGA_STATE_DIR", str(tmp_path / "state"))

    result = lifecycle._step_start_api(
        lifecycle.state_module.DeploymentState(), host=host, port=port
    )

    assert result.ok
    assert f"({host}:{port})" in result.detail
    assert (tmp_path / "state" / "tga-api.env").read_text(encoding="utf-8") == (
        f"TGA_API_HOST={host}\nTGA_API_PORT={port}\n"
    )
    assert ["systemctl", "restart", "tga-api.service"] in systemd_host.calls


def test_unchanged_systemd_bind_reuses_the_active_unit(monkeypatch, tmp_path, systemd_host):
    monkeypatch.setenv("TGA_STATE_DIR", str(tmp_path / "state"))
    service_manager.configure_api("127.0.0.1", 8123)
    systemd_host.calls.clear()

    result = lifecycle._step_start_api(
        lifecycle.state_module.DeploymentState(), host="127.0.0.1", port=8123
    )

    assert result.ok
    assert not any(call[1] in {"start", "restart"} for call in systemd_host.calls)


def test_public_listener_is_probed_through_loopback():
    assert lifecycle._probe_origin("0.0.0.0", 8173) == "http://127.0.0.1:8173"
    assert lifecycle._probe_origin("127.0.0.1", 8123) == "http://127.0.0.1:8123"
