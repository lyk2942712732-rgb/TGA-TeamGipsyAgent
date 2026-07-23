from pathlib import Path
import os
import json

import pytest

from tga.cli.desktop import _prepare_frontend
from tga.cli.main import main


def test_cli_run_writes_report(tmp_path: Path):
    config = tmp_path / "task.json"
    config.write_text(
        '{"id":"task_cli","name":"demo","mode":"vulnerability_research","target":".","scope":["."],"intensity":"passive","allow_active_scan":false,"goal":"scan","flag_format":null}',
        encoding="utf-8",
    )
    run_root = tmp_path / "runs"

    assert main(["run", str(config), "--run-root", str(run_root)]) == 0

    report_path = run_root / "task_cli" / "reports" / "report.md"
    assert report_path.exists()
    assert "# TGA Report" in report_path.read_text(encoding="utf-8")


def test_cli_go_delegates_to_desktop_launcher(monkeypatch):
    calls: list[dict] = []

    def fake_launch(**kwargs):
        calls.append(kwargs)
        return 0

    monkeypatch.setattr("tga.cli.desktop.launch_desktop", fake_launch)

    assert main(["go", "--host", "127.0.0.1", "--port", "8123", "--no-build"]) == 0
    assert calls == [{"host": "127.0.0.1", "port": 8123, "build": False}]


def test_console_entrypoint_reads_real_command_line(monkeypatch):
    calls: list[dict] = []

    def fake_launch(**kwargs):
        calls.append(kwargs)
        return 0

    monkeypatch.setattr("tga.cli.desktop.launch_desktop", fake_launch)
    monkeypatch.setattr("sys.argv", ["tga", "go", "--no-build"])

    assert main() == 0
    assert calls == [{"host": "127.0.0.1", "port": 8123, "build": False}]


def test_cli_web_delegates_to_browser_launcher(monkeypatch):
    calls: list[dict] = []

    def fake_launch(**kwargs):
        calls.append(kwargs)
        return 0

    monkeypatch.setattr("tga.cli.desktop.launch_web", fake_launch)

    assert main(["web", "--no-build"]) == 0
    assert calls == [{"host": "127.0.0.1", "port": 5173, "build": False}]


def test_cli_help_is_not_reinterpreted_as_a_run_command():
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0


def test_desktop_uses_existing_bundle_when_npm_is_not_on_path(tmp_path: Path, monkeypatch):
    root = tmp_path / "project"
    dist = root / "apps" / "web" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html>", encoding="utf-8")
    monkeypatch.setattr("tga.cli.desktop.subprocess.run", lambda *args, **kwargs: (_ for _ in ()).throw(FileNotFoundError()))

    assert _prepare_frontend(root=root, host="127.0.0.1", port=8000, build=True) == dist


def test_desktop_uses_project_relative_mcp_hub(tmp_path: Path, monkeypatch):
    root = tmp_path / "project"
    dist = root / "apps" / "web" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html>", encoding="utf-8")
    (root / "mcp-security-hub").mkdir()
    monkeypatch.delenv("TGA_MCP_SECURITY_HUB_ROOT", raising=False)

    assert _prepare_frontend(root=root, host="127.0.0.1", port=8123, build=False) == dist
    assert Path(os.environ["TGA_MCP_SECURITY_HUB_ROOT"]).resolve() == root / "mcp-security-hub"


def test_desktop_prefers_windows_npm_cmd(tmp_path: Path, monkeypatch):
    root = tmp_path / "project"
    web_root = root / "apps" / "web"
    web_root.mkdir(parents=True)
    calls: list[list[str]] = []

    monkeypatch.setattr("tga.cli.desktop.os.name", "nt", raising=False)
    monkeypatch.setattr("tga.cli.desktop.shutil.which", lambda value: r"D:\\nodejs\\npm.cmd" if value == "npm.cmd" else None)
    monkeypatch.setattr("tga.cli.desktop.subprocess.run", lambda args, **kwargs: calls.append(args))

    # The mocked build does not create dist, so confirm the subprocess target
    # before the expected post-build validation raises a launch error.
    from tga.cli.desktop import DesktopLaunchError
    try:
        _prepare_frontend(root=root, host="127.0.0.1", port=8000, build=True)
    except DesktopLaunchError:
        pass
    assert calls == [[r"D:\\nodejs\\npm.cmd", "run", "build"]]
