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
from tga.deployment import lifecycle
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

    # One `status` verb serves both scopes: with a task id it prints that
    # task's snapshot, without one it prints deployment state.
    status_parser = subparsers.add_parser(
        "status", help="Show deployment state, or a task snapshot when given a task id"
    )
    status_parser.add_argument("task_id", nargs="?", default=None)
    status_parser.add_argument("--run-root", default="runs")
    status_parser.add_argument("--json", action="store_true")

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

    # Deployment lifecycle. `tga up` is the single supported way to start TGA;
    # the former `go` and `web` entrypoints are gone, so there is exactly one
    # startup path to keep correct.
    up_parser = subparsers.add_parser("up", help="Start TGA and open the interface")
    up_parser.add_argument("--host", default=lifecycle.DEFAULT_HOST)
    up_parser.add_argument("--port", type=int, default=lifecycle.DEFAULT_PORT)
    up_parser.add_argument("--no-open", action="store_true", help="Do not open a browser")
    up_parser.add_argument("--public", action="store_true", help="Serve for remote access")
    up_parser.add_argument("--timeout", type=float, default=90.0)
    up_parser.add_argument("--json", action="store_true")

    for name, help_text in (
        ("down", "Stop TGA, preserving all task data"),
        ("doctor", "Diagnose the deployment and print fixes"),
    ):
        lifecycle_parser = subparsers.add_parser(name, help=help_text)
        lifecycle_parser.add_argument("--json", action="store_true")

    logs_parser = subparsers.add_parser("logs", help="Show component logs")
    logs_parser.add_argument("--component", default="api")
    logs_parser.add_argument("--lines", type=int, default=200)
    logs_parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    # Console-script entry points call ``main()`` without arguments.  Preserve
    # explicit test arguments, but otherwise use the actual command line so
    # `tga go` is not silently reduced to an empty argv list.
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command in {"up", "down", "doctor", "logs"} or (
        args.command == "status" and args.task_id is None
    ):
        return _run_lifecycle(args)
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


def _run_lifecycle(args) -> int:
    """Run a deployment verb through the shared internal implementation.

    The Go launcher and this CLI both delegate here, so a `tga up` typed on
    Windows and one typed on a Linux server cannot drift apart.
    """
    from tga.cli.internal import _render
    from tga.deployment.errors import DeploymentError

    try:
        if args.command == "up":
            host = "0.0.0.0" if getattr(args, "public", False) else args.host
            payload = lifecycle.up(
                host=host,
                port=args.port,
                open_browser=not (args.no_open or getattr(args, "public", False)),
                timeout_seconds=args.timeout,
            ).to_dict()
        elif args.command == "down":
            payload = lifecycle.down()
        elif args.command == "status":
            payload = lifecycle.status()
        elif args.command == "doctor":
            payload = lifecycle.doctor()
        else:
            payload = lifecycle.logs(component=args.component, lines=args.lines)
    except DeploymentError as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": exc.to_dict()}, ensure_ascii=False))
        else:
            print(f"error: {exc.detail or exc.code}", file=sys.stderr)
            if exc.remediation:
                print(f"  -> {exc.remediation}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        _render(args.command, payload)
    return 0 if payload.get("ok", False) else 1


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
