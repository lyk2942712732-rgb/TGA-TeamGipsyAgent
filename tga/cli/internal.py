"""`tga-internal`: the platform-agnostic worker behind every `tga` command.

Users never invoke this.  The Go launcher does: on Linux directly, on Windows
through `wsl.exe`.  Every subcommand supports `--json` so the launcher can
consume results without screen-scraping, and every failure carries a stable
error code from :mod:`tga.deployment.errors`.
"""

from __future__ import annotations

import argparse
import json
import sys

from tga.deployment import lifecycle
from tga.deployment.errors import DeploymentError, ErrorCode


def _build_parser() -> argparse.ArgumentParser:
    # `--json` is accepted both before and after the subcommand, so callers do
    # not have to remember which side argparse wants it on.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="Emit machine-readable JSON")

    parser = argparse.ArgumentParser(
        prog="tga-internal",
        description="Internal TGA runtime worker. Use `tga` instead.",
        parents=[common],
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve_parser = subparsers.add_parser("serve", parents=[common], help="Run the headless API + SPA server")
    serve_parser.add_argument("--host", default=lifecycle.DEFAULT_HOST)
    serve_parser.add_argument("--port", type=int, default=lifecycle.DEFAULT_PORT)
    serve_parser.add_argument("--web-dist", default=None)
    serve_parser.add_argument("--log-level", default="info")

    up_parser = subparsers.add_parser("up", parents=[common], help="Bring the deployment to a serving state")
    up_parser.add_argument("--host", default=lifecycle.DEFAULT_HOST)
    up_parser.add_argument("--port", type=int, default=lifecycle.DEFAULT_PORT)
    up_parser.add_argument("--no-open", action="store_true", help="Do not open a browser")
    up_parser.add_argument("--timeout", type=float, default=90.0)
    up_parser.add_argument(
        "--pull-images",
        action="store_true",
        help="Fetch any missing Solver images (tens of gigabytes on a first run)",
    )

    subparsers.add_parser("down", parents=[common], help="Stop the deployment, preserving data")
    subparsers.add_parser(
        "reset", parents=[common],
        help="Stop and forget what was provisioned, preserving task data",
    )
    subparsers.add_parser("status", parents=[common], help="Report deployment state")
    subparsers.add_parser("doctor", parents=[common], help="Diagnose every deployment capability")

    logs_parser = subparsers.add_parser("logs", parents=[common], help="Print a component log tail")
    logs_parser.add_argument("--component", default="api")
    logs_parser.add_argument("--lines", type=int, default=200)
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    args = _build_parser().parse_args(argv)

    if args.command == "serve":
        from pathlib import Path

        from tga.deployment.serve import serve

        try:
            return serve(
                host=args.host,
                port=args.port,
                web_root=Path(args.web_dist) if args.web_dist else None,
                log_level=args.log_level,
            )
        except DeploymentError as exc:
            return _fail(exc, as_json=args.json)

    try:
        payload = _dispatch(args)
    except DeploymentError as exc:
        return _fail(exc, as_json=args.json)

    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        _render(args.command, payload)
    return 0 if payload.get("ok", False) else 1


def _dispatch(args) -> dict:
    if args.command == "up":
        return lifecycle.up(
            host=args.host,
            port=args.port,
            open_browser=not args.no_open,
            timeout_seconds=args.timeout,
            pull_images=args.pull_images,
        ).to_dict()
    if args.command == "down":
        return lifecycle.down()
    if args.command == "reset":
        return lifecycle.reset()
    if args.command == "status":
        return lifecycle.status()
    if args.command == "doctor":
        return lifecycle.doctor()
    if args.command == "logs":
        return lifecycle.logs(component=args.component, lines=args.lines)
    raise DeploymentError(ErrorCode.API_START_FAILED, f"unknown command {args.command}")


def _fail(exc: DeploymentError, *, as_json: bool) -> int:
    if as_json:
        print(json.dumps({"ok": False, "error": exc.to_dict()}, ensure_ascii=False))
    else:
        print(f"error: {exc.detail or exc.code}", file=sys.stderr)
        if exc.remediation:
            print(f"  -> {exc.remediation}", file=sys.stderr)
    return 1


_MARK = {"ready": "OK", "unavailable": "!!", "disabled": "--", "unknown": "??"}


def _render(command: str, payload: dict) -> None:
    if command == "up":
        for step in payload.get("steps", []):
            mark = "--" if step.get("skipped") else ("OK" if step["ok"] else "!!")
            detail = f"  {step['detail']}" if step.get("detail") else ""
            code = f"  [{step['code']}]" if step.get("code") else ""
            print(f"[{mark}] {step['name']}{detail}{code}")
        if payload.get("error"):
            error = payload["error"]
            print(f"\nfailed: [{error['code']}] {error['detail']}", file=sys.stderr)
            if error.get("remediation"):
                print(f"  -> {error['remediation']}", file=sys.stderr)
            return
        status = payload.get("status", "")
        print(f"\nTGA is {status} at {payload.get('url', '')}")
        if status == "degraded":
            print("Sandbox isolation is not enforced. Run `tga doctor` for details.")
        return

    if command == "doctor":
        for check in payload.get("checks", []):
            mark = _MARK.get(check.get("status", ""), "??")
            detail = f"  {check['detail']}" if check.get("detail") else ""
            code = f"  [{check['code']}]" if check.get("code") else ""
            print(f"[{mark}] {check['name']:<24}{detail}{code}")
        for hint in payload.get("remediation", []):
            print(f"\n[{hint['code']}]\n  {hint['hint']}")
        print(f"\nstatus: {payload.get('status', 'unknown')}")
        return

    if command == "status":
        print(f"Platform   {payload.get('platform', '')}")
        print(f"Supervisor {payload.get('supervisor', '')}")
        print(f"Phase      {payload.get('phase', '')}")
        print(f"Running    {payload.get('running', False)}")
        print(f"URL        {payload.get('url', '') or '-'}")
        report = payload.get("readiness") or {}
        if report:
            sandbox = report.get("sandbox", {})
            print(f"Readiness  {report.get('status', 'unknown')}")
            print(f"Sandbox    {sandbox.get('runtime', 'unknown')}")
        if payload.get("last_error_code"):
            print(f"Last error {payload['last_error_code']}: {payload.get('last_error_detail', '')}")
        return

    if command == "logs":
        if not payload.get("ok"):
            print(f"no log at {payload.get('path', '')}", file=sys.stderr)
            return
        for line in payload.get("lines", []):
            print(line)
        return

    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
