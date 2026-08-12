"""CLI adapter for running the same application service without FastAPI."""

from __future__ import annotations

import argparse
import json

from tga2.bootstrap import get_container
from tga2.core.models import CreateTaskRequest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tga2")
    parser.add_argument("--run-root", default="runs2")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("--name", required=True)
    create.add_argument("--objective", required=True)
    create.add_argument("--mode", default="ctf")
    create.add_argument("--input", action="append", default=[])
    run = commands.add_parser("run")
    run.add_argument("task_id")
    show = commands.add_parser("show")
    show.add_argument("task_id")
    commands.add_parser("list")
    report = commands.add_parser("report")
    report.add_argument("task_id")
    approve = commands.add_parser("approve")
    approve.add_argument("task_id")
    approve.add_argument("--reject", action="store_true")
    args = parser.parse_args(argv)
    service = get_container(args.run_root).runtime
    if args.command == "create":
        result = service.create_task(
            CreateTaskRequest(
                name=args.name,
                objective=args.objective,
                mode=args.mode,
                input_paths=args.input,
            )
        )
    elif args.command == "run":
        result = service.run_task(args.task_id)
    elif args.command == "show":
        result = service.snapshot(args.task_id)
    elif args.command == "list":
        result = service.list_tasks()
    elif args.command == "report":
        result = service.report(args.task_id)
    else:
        result = service.resume_task(
            args.task_id,
            {
                "decisions": [
                    {"type": "reject" if args.reject else "approve"}
                ]
            },
        )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main"]
