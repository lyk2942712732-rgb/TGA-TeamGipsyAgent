"""TGA CLI."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from tga.cli.config_loader import TaskConfigError, load_task_config
from tga.runtime.manager import Manager
from tga.runtime.service import TaskRuntimeService


def run_from_config(config: str, *, run_root: str, report_out: str | None = None) -> Path:
    task = load_task_config(config)
    service = _service(run_root)
    service.create_task(task)
    service.run_task(task.id)
    return service.write_report(task.id, output=report_out)


def _service(run_root: str) -> TaskRuntimeService:
    return TaskRuntimeService(run_root=run_root, manager=Manager(run_root=run_root))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tga")
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", help="Run a TGA task from task.json")
    run_parser.add_argument("config", help="Path to task.json")
    run_parser.add_argument("--run-root", default="runs")
    run_parser.add_argument("--report-out", default=None)

    create_parser = subparsers.add_parser("create", help="Create a durable v2 task without running it")
    create_parser.add_argument("config", help="Path to task.json")
    create_parser.add_argument("--run-root", default="runs")
    create_parser.add_argument("--hint", default=None)

    start_parser = subparsers.add_parser("start", help="Run or recover an existing v2 task")
    start_parser.add_argument("task_id")
    start_parser.add_argument("--run-root", default="runs")

    status_parser = subparsers.add_parser("status", help="Print a durable task snapshot summary")
    status_parser.add_argument("task_id")
    status_parser.add_argument("--run-root", default="runs")

    observe_parser = subparsers.add_parser("observe", help="Read the shared ordered Runtime event stream")
    observe_parser.add_argument("task_id")
    observe_parser.add_argument("--run-root", default="runs")
    observe_parser.add_argument("--after-seq", type=int, default=0)
    observe_parser.add_argument("--follow", action="store_true")
    observe_parser.add_argument("--interval", type=float, default=1.0)

    cancel_parser = subparsers.add_parser("cancel", help="Request cancellation through Runtime Manager")
    cancel_parser.add_argument("task_id")
    cancel_parser.add_argument("--run-root", default="runs")

    resume_parser = subparsers.add_parser("resume", help="Resume and run a paused or blocked task")
    resume_parser.add_argument("task_id")
    resume_parser.add_argument("--run-root", default="runs")

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
    if args.command == "create":
        try:
            task = load_task_config(args.config)
            result = _service(args.run_root).create_task(task, initial_hint=args.hint)
        except (TaskConfigError, KeyError, ValueError) as exc:
            parser.error(str(exc))
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if args.command in {"start", "status", "observe", "cancel", "resume"}:
        service = _service(args.run_root)
        try:
            if args.command == "start":
                snapshot = service.run_task(args.task_id)
                print(json.dumps(_snapshot_summary(snapshot), ensure_ascii=False))
            elif args.command == "status":
                print(json.dumps(_snapshot_summary(service.snapshot(args.task_id)), ensure_ascii=False))
            elif args.command == "cancel":
                print(json.dumps(service.command("control_session", args.task_id, action="cancel"), ensure_ascii=False))
            elif args.command == "resume":
                result = service.command("control_session", args.task_id, action="resume")
                if result.get("accepted"):
                    snapshot = service.run_task(args.task_id)
                    result = {**result, "final": _snapshot_summary(snapshot)}
                print(json.dumps(result, ensure_ascii=False))
            else:
                _observe(service, args.task_id, after_seq=max(0, args.after_seq), follow=args.follow, interval=max(0.1, args.interval))
        except (KeyError, ValueError) as exc:
            parser.error(str(exc))
        return 0
    if args.command != "run":
        parser.print_help()
        return 2

    try:
        report_path = run_from_config(args.config, run_root=args.run_root, report_out=args.report_out)
    except TaskConfigError as exc:
        parser.error(str(exc))
    print(f"Wrote {report_path}")
    return 0


def _snapshot_summary(snapshot: dict) -> dict:
    session = snapshot.get("session") or {}
    return {
        "schema_version": int(snapshot.get("schema_version") or 2),
        "task_id": (snapshot.get("task") or {}).get("id"),
        "status": session.get("status"),
        "turn_count": session.get("turn_count", 0),
        "max_turns": session.get("max_turns", 0),
        "stop_reason": session.get("stop_reason", ""),
        "latest_seq": int(snapshot.get("latest_seq") or max((item.get("seq", 0) for item in snapshot.get("agent_events") or []), default=0)),
        "solvers": len(snapshot.get("solvers") or []),
        "artifacts": len(snapshot.get("artifacts") or []),
        "flags": len(snapshot.get("flags") or []),
    }


def _observe(service: TaskRuntimeService, task_id: str, *, after_seq: int, follow: bool, interval: float) -> None:
    cursor = after_seq
    while True:
        events = service.events(task_id, after_seq=cursor, limit=200)
        for event in events:
            cursor = max(cursor, int(event["seq"]))
            print(json.dumps(event, ensure_ascii=False), flush=True)
        if not follow:
            return
        status = (service.snapshot(task_id).get("session") or {}).get("status")
        if status in {"completed", "failed", "cancelled"} and not events:
            return
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())

