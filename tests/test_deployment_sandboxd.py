"""`tga up` has to start sandboxd, not just complain that it is not running."""

from __future__ import annotations

import re

import pytest

from tga.deployment import lifecycle, service_manager
from tga.deployment.errors import ErrorCode
from tga.deployment.paths import project_root
from tga.deployment.state import DeploymentState

UNIT = project_root() / "deploy" / "systemd" / "tga-sandboxd.service"
PROVISION = project_root() / "deploy" / "wsl-rootfs" / "provision.sh"


class _Config:
    """Only the parts of SandboxConfig this step looks at."""

    def __init__(self, runtime: str = "enforced", socket: str = "/run/x/s.sock"):
        self.runtime = runtime
        self.sandboxd = type("S", (), {"socket_path": socket})()


class _Units:
    """A stand-in systemd that records what it was asked to do."""

    def __init__(self, *, installed: bool = True, starts_ok: bool = True):
        self.installed = installed
        self.starts_ok = starts_ok
        self.active = False
        self.actions: list[str] = []

    def install(self, monkeypatch) -> "_Units":
        monkeypatch.setattr(service_manager, "unit_installed", lambda unit=None: self.installed)
        monkeypatch.setattr(service_manager, "state", lambda unit=None: self._state())
        monkeypatch.setattr(service_manager, "start", lambda unit=None: self._start())
        monkeypatch.setattr(service_manager, "stop", lambda unit=None: self._stop())
        return self

    def _state(self):
        return service_manager.ServiceState(managed=True, active=self.active, detail="")

    def _start(self):
        self.actions.append("start")
        self.active = self.starts_ok
        return self._state()

    def _stop(self):
        self.actions.append("stop")
        self.active = False
        return self._state()


@pytest.fixture
def answering(monkeypatch):
    """Make the socket look healthy so the step's own logic is what is tested."""
    monkeypatch.setattr(lifecycle.readiness, "_sandboxd_health", lambda config: object())


def _use_config(monkeypatch, config: _Config) -> None:
    monkeypatch.setattr(lifecycle, "load_sandbox_config", lambda *a, **k: (config, None))


def test_up_starts_the_unit_when_it_is_installed_but_stopped(monkeypatch, answering):
    """The step that reports sandboxd is down must be the one that brings it up."""
    units = _Units().install(monkeypatch)
    _use_config(monkeypatch, _Config())
    state = DeploymentState()

    result = lifecycle._step_sandboxd(state)

    assert result.ok, result.detail
    assert units.actions == ["start"]
    assert state.completed("start_sandboxd")


def test_up_does_not_restart_a_unit_that_is_already_active(monkeypatch, answering):
    units = _Units().install(monkeypatch)
    units.active = True
    _use_config(monkeypatch, _Config())

    result = lifecycle._step_sandboxd(DeploymentState())

    assert result.ok
    assert units.actions == []


def test_a_unit_that_will_not_start_is_reported_with_its_code(monkeypatch, answering):
    _Units(starts_ok=False).install(monkeypatch)
    _use_config(monkeypatch, _Config())

    result = lifecycle._step_sandboxd(DeploymentState())

    assert not result.ok
    assert result.code is ErrorCode.SANDBOXD_SOCKET_MISSING
    assert "did not become active" in result.detail


def test_a_silent_socket_is_still_reported_after_a_successful_start(monkeypatch):
    """An active unit is not proof; the socket has to answer."""
    _Units().install(monkeypatch)
    _use_config(monkeypatch, _Config())
    monkeypatch.setattr(lifecycle.readiness, "_sandboxd_health", lambda config: None)
    monkeypatch.setattr(lifecycle, "_await_socket", lambda config, **kwargs: None)

    result = lifecycle._step_sandboxd(DeploymentState())

    assert not result.ok
    assert result.code is ErrorCode.SANDBOXD_SOCKET_MISSING


def test_a_disabled_runtime_skips_the_step_instead_of_starting_anything(monkeypatch):
    units = _Units().install(monkeypatch)
    _use_config(monkeypatch, _Config(runtime="disabled"))

    result = lifecycle._step_sandboxd(DeploymentState())

    assert result.skipped
    assert units.actions == []


def test_without_a_unit_the_step_only_checks_the_socket(monkeypatch, answering):
    """A launcher-supervised host has no unit to start; it must not fail for that."""
    units = _Units(installed=False).install(monkeypatch)
    _use_config(monkeypatch, _Config())

    result = lifecycle._step_sandboxd(DeploymentState())

    assert result.ok
    assert units.actions == []


def test_down_stops_sandboxd_too(monkeypatch, tmp_path):
    """Leaving a privileged runtime up after `tga down` is a surprise."""
    monkeypatch.setenv("TGA_RUN_ROOT", str(tmp_path))
    units = _Units().install(monkeypatch)
    units.active = True
    monkeypatch.setattr(service_manager, "manages_api", lambda: False)

    payload = lifecycle.down()

    assert payload["stopped_sandboxd"] is True
    assert "stop" in units.actions


def test_the_unit_starts_the_binary_provisioning_installs():
    """The unit and the installer must agree on one path.

    sandboxd/deploy/ carries its own unit pointing at /usr/local/libexec, which
    is the standalone-host convention. The packaged deployment puts its
    binaries under /opt/tga/bin, and a unit pointing anywhere else fails at
    every boot with nothing but "no such file".
    """
    exec_start = re.search(r"^ExecStart=(\S+)", UNIT.read_text(encoding="utf-8"), re.M)
    assert exec_start, "the unit must declare ExecStart"

    provision = PROVISION.read_text(encoding="utf-8")
    prefix = re.search(r'^TGA_PREFIX="\$\{TGA_PREFIX:-([^}]+)\}"', provision, re.M)
    assert prefix, "provision.sh must define a default TGA_PREFIX"

    assert exec_start.group(1) == f"{prefix.group(1)}/bin/tga-sandboxd"
    assert '"$TGA_PREFIX/bin/tga-sandboxd"' in provision, "nothing installs the binary"


def test_the_unit_writes_where_tga_logs_reads():
    """`tga logs --component sandboxd` reads /var/log/tga/sandboxd.log."""
    unit = UNIT.read_text(encoding="utf-8")
    assert "append:/var/log/tga/sandboxd.log" in unit
    # ProtectSystem=strict makes every path read-only unless listed.
    read_write = re.search(r"^ReadWritePaths=(.+)$", unit, re.M)
    assert read_write and "/var/log/tga" in read_write.group(1)


def test_provisioning_only_enables_a_unit_whose_binary_exists():
    """An enabled unit with a missing ExecStart fails at boot and hides why."""
    provision = PROVISION.read_text(encoding="utf-8")
    guard = re.search(
        r"if \[ -x \"\$TGA_PREFIX/bin/tga-sandboxd\" \];\s*then\s*\n"
        r"\s*enable_unit tga-sandboxd\.service",
        provision,
    )
    assert guard, "enabling tga-sandboxd must be guarded by the binary existing"


def test_units_are_installed_even_where_systemd_is_not_running():
    """The image build has no systemd; the distribution it produces does.

    Gating installation on /run/systemd/system meant the WSL rootfs shipped
    with no units at all, and `tga up` inside it would have supervised the API
    itself while a perfectly good systemd sat unused.
    """
    provision = PROVISION.read_text(encoding="utf-8")

    install_line = re.search(
        r'^\s*install -m 0644 "\$UNIT_SOURCE/"\*\.service /etc/systemd/system/$',
        provision,
        re.M,
    )
    assert install_line, "unit installation must not depend on systemd running"

    # It has to be reachable without systemd, so the enclosing condition may
    # test for the unit files but not for /run/systemd/system.
    enclosing = re.search(r'^if \[ -d "\$UNIT_SOURCE" \]; then$', provision, re.M)
    assert enclosing, "installation should be gated on the units existing, nothing else"


def test_enabling_falls_back_to_the_link_systemctl_would_write():
    """`systemctl enable` needs a running systemd; an image build has none."""
    provision = PROVISION.read_text(encoding="utf-8")
    body = re.search(r"^enable_unit\(\) \{\n(.*?)^\}$", provision, re.M | re.S)
    assert body, "enable_unit is missing"

    assert "systemctl enable" in body.group(1)
    assert "multi-user.target.wants" in body.group(1), (
        "without systemd the unit must still be linked for first boot"
    )
