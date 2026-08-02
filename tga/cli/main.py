"""TGA CLI."""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import replace
from pathlib import Path

from tga.bootstrap.container import Container
from tga.cli.config_loader import TaskConfigError, load_task_request
from tga.runtime.service import TaskRuntimeService
from tga.runtime.task_creation import (
    CreateTaskCommand,
    CreatedTask,
    TaskCreationError,
    TaskCreationService,
)


def run_from_config(config: str, *, run_root: str, report_out: str | None = None) -> Path:
    """Create a task through the shared creation flow, then run it inline."""
    created = _create_task(load_task_request(config), run_root=run_root, schedule=False)
    service = _service(run_root)
    service.run_task(created.task_id)
    return service.write_report(created.task_id, output=report_out)


def _service(run_root: str) -> TaskRuntimeService:
    return Container(run_root).runtime_service()


def _create_task(
    command: CreateTaskCommand, *, run_root: str, schedule: bool
) -> CreatedTask:
    """Create a task through the single Preflight -> create -> schedule path.

    CLI, Web and API all go through TaskCreationService, so Skill selection,
    policy validation and the preflight fingerprint cannot diverge per surface.
    """
    from tga.runtime.manager import get_manager

    container = Container(run_root)
    scheduler = container.scheduler()
    service = TaskCreationService(
        run_root=run_root,
        mcp_manager=get_manager().mcp_manager,
        schedule=(scheduler.schedule if schedule else (lambda task_id: False)),
        runtime_service=container.runtime_service(),
    )
    preflight = service.preflight(command)
    return service.create(
        replace(command, preflight_fingerprint=preflight.fingerprint)
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tga")
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", help="Run a TGA task from task.json")
    run_parser.add_argument("config", help="Path to task.json")
    run_parser.add_argument("--run-root", default="runs")
    run_parser.add_argument("--report-out", default=None)

    create_parser = subparsers.add_parser(
        "create", help="Create a schema-v6 task through Preflight without running it"
    )
    create_parser.add_argument("config", help="Path to task.json")
    create_parser.add_argument("--run-root", default="runs")
    create_parser.add_argument(
        "--schedule", action="store_true",
        help="Hand the created task to the Runtime scheduler",
    )

    start_parser = subparsers.add_parser("start", help="Run or recover an existing task")
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

    migrate_parser = subparsers.add_parser(
        "migrate", help="Plan, apply, or verify the offline schema-v5 to schema-v6 migration"
    )
    migrate_parser.add_argument("--db", required=True, type=Path, help="Path to evidence.db")
    migrate_operation = migrate_parser.add_mutually_exclusive_group(required=True)
    migrate_operation.add_argument("--dry-run", action="store_true")
    migrate_operation.add_argument("--apply", action="store_true")
    migrate_operation.add_argument("--verify", action="store_true")
    migrate_parser.add_argument(
        "--backup", action="store_true",
        help="Explicit backup-first acknowledgement; apply always creates backups",
    )
    migrate_parser.add_argument("--report", type=Path, default=None)

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
    if args.command == "migrate":
        from tga.migrations.schema_v5_to_v6 import main as migration_main

        migration_args = ["--db", str(args.db)]
        migration_args.append(
            "--verify" if args.verify else "--apply" if args.apply else "--dry-run"
        )
        if args.backup:
            migration_args.append("--backup")
        if args.report is not None:
            migration_args.extend(("--report", str(args.report)))
        return migration_main(migration_args)
    if args.command == "create":
        try:
            created = _create_task(
                load_task_request(args.config),
                run_root=args.run_root,
                schedule=args.schedule,
            )
        except (TaskConfigError, TaskCreationError, KeyError, ValueError) as exc:
            parser.error(str(exc))
        print(json.dumps({
            "task_id": created.task_id,
            "status": created.status,
            "scheduled": created.scheduled,
            "mcp_catalog_version": created.mcp_capabilities.catalog_version,
        }, ensure_ascii=False))
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
    except (TaskConfigError, TaskCreationError) as exc:
        parser.error(str(exc))
    print(f"Wrote {report_path}")
    return 0


def _snapshot_summary(snapshot: dict) -> dict:
    session = snapshot.get("session") or {}
    return {
        "schema_version": int(snapshot.get("schema_version") or 6),
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
