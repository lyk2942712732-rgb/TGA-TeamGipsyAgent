from pathlib import Path
import json

import pytest

from tga.cli.main import main


def test_cli_run_fails_clearly_without_a_configured_model(tmp_path: Path, monkeypatch):
    config = tmp_path / "task.json"
    config.write_text(
        json.dumps(
            {
                "id": "task_cli",
                "name": "demo",
                "mode": "vulnerability_research",
                "goal": "scan",
                "input": {"text": "scan the authorized target"},
            }
        ),
        encoding="utf-8",
    )
    run_root = tmp_path / "runs"

    monkeypatch.delenv("TGA_LLM_API_KEY", raising=False)
    # The CLI shares the Web/API creation path, so an unconfigured model is
    # rejected by preflight before any task is persisted.
    with pytest.raises(SystemExit):
        main(["run", str(config), "--run-root", str(run_root)])
    assert not run_root.exists()


def test_cli_help_is_not_reinterpreted_as_a_run_command():
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0


@pytest.mark.parametrize("removed", ["go", "web", "serve"])
def test_retired_startup_entrypoints_are_gone(removed, capsys):
    """`tga up` is the only supported startup path.

    Keeping `go`/`web` alive would mean two startup code paths to keep
    correct, which is exactly how the run-root split-brain appeared.
    """
    with pytest.raises(SystemExit) as exc:
        main([removed])
    assert exc.value.code != 0


def test_up_delegates_to_the_shared_lifecycle(monkeypatch, capsys):
    calls: list[dict] = []

    class FakeResult:
        def to_dict(self):
            return {"ok": True, "status": "degraded", "url": "http://127.0.0.1:8123",
                    "steps": [{"name": "start_api", "ok": True, "detail": "pid 1"}]}

    def fake_up(**kwargs):
        calls.append(kwargs)
        return FakeResult()

    monkeypatch.setattr("tga.deployment.lifecycle.up", fake_up)
    assert main(["up", "--no-open", "--port", "8123"]) == 0
    assert calls == [{
        "host": "127.0.0.1", "port": 8123, "open_browser": False, "timeout_seconds": 90.0,
    }]
    assert "degraded" in capsys.readouterr().out


def test_up_public_binds_all_interfaces_and_never_opens_a_browser(monkeypatch):
    """A server deployment has no browser and must not bind localhost only."""
    calls: list[dict] = []

    class FakeResult:
        def to_dict(self):
            return {"ok": True, "status": "ready", "url": "http://0.0.0.0:8123", "steps": []}

    monkeypatch.setattr(
        "tga.deployment.lifecycle.up",
        lambda **kwargs: (calls.append(kwargs), FakeResult())[1],
    )
    assert main(["up", "--public"]) == 0
    assert calls[0]["host"] == "0.0.0.0"
    assert calls[0]["open_browser"] is False


def test_status_emits_json_when_asked(monkeypatch, capsys):
    monkeypatch.setattr(
        "tga.deployment.lifecycle.status",
        lambda: {"ok": True, "phase": "stopped", "running": False},
    )
    assert main(["status", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["phase"] == "stopped"


def test_lifecycle_failures_print_remediation(monkeypatch, capsys):
    from tga.deployment.errors import DeploymentError, ErrorCode

    def fail(**_kwargs):
        raise DeploymentError(ErrorCode.PORT_UNAVAILABLE, "127.0.0.1:8123 is in use")

    monkeypatch.setattr("tga.deployment.lifecycle.up", fail)
    assert main(["up", "--no-open"]) == 1
    captured = capsys.readouterr()
    assert "in use" in captured.err
    # The user is told what to do, not merely what broke.
    assert "--port" in captured.err
