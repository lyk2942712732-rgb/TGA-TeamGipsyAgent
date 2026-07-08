"""TGA CLI."""

from __future__ import annotations

import argparse
from pathlib import Path

from tga.cli.config_loader import TaskConfigError, load_task_config
from tga.evidence.artifacts import ArtifactStore
from tga.evidence.store import EvidenceStore
from tga.orchestrator.run_loop import run_task
from tga.reporting.markdown_report import render_markdown_report
from tga.tools.bootstrap import build_tool_runner_from_env
from tga.workers.subprocess_worker import SubprocessWorker


def run_from_config(config: str, *, run_root: str, report_out: str | None = None) -> Path:
    task = load_task_config(config)
    task_root = Path(run_root) / task.id
    artifact_store = ArtifactStore(task_root / "artifacts")
    store = EvidenceStore(task_root / "evidence.db")
    worker = SubprocessWorker(
        artifact_store=artifact_store,
        tool_runner=build_tool_runner_from_env(artifact_store),
    )
    run_task(task=task, store=store, worker=worker, run_root=run_root)

    report = render_markdown_report(store.task_snapshot(task.id))
    report_path = Path(report_out) if report_out else task_root / "reports" / "report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")
    return report_path


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tga")
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", help="Run a TGA task from task.json")
    run_parser.add_argument("config", help="Path to task.json")
    run_parser.add_argument("--run-root", default="runs")
    run_parser.add_argument("--report-out", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(argv or [])
    if argv and argv[0] != "run":
        argv.insert(0, "run")

    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command != "run":
        parser.print_help()
        return 2

    try:
        report_path = run_from_config(args.config, run_root=args.run_root, report_out=args.report_out)
    except TaskConfigError as exc:
        parser.error(str(exc))
    print(f"Wrote {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

