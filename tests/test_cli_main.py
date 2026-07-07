from pathlib import Path

from tga.cli.main import main


def test_cli_run_writes_report(tmp_path: Path):
    config = tmp_path / "task.json"
    config.write_text(
        '{"id":"task_cli","name":"demo","mode":"code_audit","target":".","scope":["."],"intensity":"passive","allow_active_scan":false,"goal":"scan","flag_format":null}',
        encoding="utf-8",
    )
    run_root = tmp_path / "runs"

    assert main(["run", str(config), "--run-root", str(run_root)]) == 0

    report_path = run_root / "task_cli" / "reports" / "report.md"
    assert report_path.exists()
    assert "# TGA Report" in report_path.read_text(encoding="utf-8")


def test_cli_legacy_invocation_still_writes_report(tmp_path: Path):
    config = tmp_path / "task.json"
    config.write_text(
        '{"id":"task_legacy","name":"demo","mode":"ctf","target":"http://127.0.0.1:1","scope":["127.0.0.1:1"],"goal":"solve","flag_format":"flag\\\\{[^}]+\\\\}"}',
        encoding="utf-8",
    )
    run_root = tmp_path / "runs"

    assert main([str(config), "--run-root", str(run_root)]) == 0

    assert (run_root / "task_legacy" / "reports" / "report.md").exists()
