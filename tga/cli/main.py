"""TGA CLI."""

from __future__ import annotations

import argparse
import sys
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
    try:
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
    finally:
        store.close()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tga")
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", help="Run a TGA task from task.json")
    run_parser.add_argument("config", help="Path to task.json")
    run_parser.add_argument("--run-root", default="runs")
    run_parser.add_argument("--report-out", default=None)

    go_parser = subparsers.add_parser("go", help="Launch the local TGA desktop window")
    go_parser.add_argument("--host", default="127.0.0.1")
    go_parser.add_argument("--port", type=int, default=8123)
    go_parser.add_argument("--no-build", action="store_true", help="Use an existing apps/web/dist bundle")

    web_parser = subparsers.add_parser("web", help="Launch the local TGA web interface in a browser")
    web_parser.add_argument("--host", default="127.0.0.1")
    web_parser.add_argument("--port", type=int, default=5173)
    web_parser.add_argument("--no-build", action="store_true", help="Use an existing apps/web/dist bundle")
    return parser


def main(argv: list[str] | None = None) -> int:
    # Console-script entry points call ``main()`` without arguments.  Preserve
    # explicit test arguments, but otherwise use the actual command line so
    # `tga go` is not silently reduced to an empty argv list.
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "go":
        from tga.cli.desktop import DesktopLaunchError, launch_desktop

        try:
            return launch_desktop(host=args.host, port=args.port, build=not args.no_build)
        except DesktopLaunchError as exc:
            parser.error(str(exc))
    if args.command == "web":
        from tga.cli.desktop import DesktopLaunchError, launch_web

        try:
            return launch_web(host=args.host, port=args.port, build=not args.no_build)
        except DesktopLaunchError as exc:
            parser.error(str(exc))
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

