"""Host-specific sandbox configuration generation and validation.

Operators must never hand-edit ``allowed_client_uids`` or paste image digests:
both are host facts, and getting them wrong either breaks startup or silently
weakens isolation.  Provisioning derives them and this module writes them.

The validator refuses to certify a configuration that claims to enforce
isolation but cannot: placeholder digests, mutable tags and an empty client-UID
allowlist are all rejected under ``runtime = "enforced"``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

_PLACEHOLDER = "REPLACE_WITH_RELEASE_DIGEST"
_DIGEST_PINNED = re.compile(r"@sha256:[a-f0-9]{64}$")


@dataclass
class ValidationReport:
    """Whether a configuration may be used, and why not when it may not."""

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict:
        return {"ok": self.ok, "errors": self.errors, "warnings": self.warnings}


def resolve_uid(user: str) -> int | None:
    """Resolve a system account to its UID, or None off Linux."""
    try:
        import pwd
    except ImportError:
        return None
    try:
        return pwd.getpwnam(user).pw_uid
    except KeyError:
        return None


def bind_to_host(
    payload: dict,
    *,
    run_root: str,
    client_uids: tuple[int, ...] = (),
) -> dict:
    """Return a copy of the configuration bound to this host's facts."""
    bound = json.loads(json.dumps(payload))
    sandboxd = bound.setdefault("sandboxd", {})
    sandboxd["run_root"] = run_root
    if client_uids:
        sandboxd["allowed_client_uids"] = sorted(set(client_uids))
    docker = bound.setdefault("docker_sandbox", {})
    docker["task_root"] = run_root
    return bound


def validate(payload: dict) -> ValidationReport:
    """Check a configuration against the enforcement rules."""
    report = ValidationReport()
    runtime = payload.get("runtime", "disabled")
    profiles = payload.get("profiles") or {}
    sandboxd = payload.get("sandboxd") or {}

    if runtime != "enforced":
        report.warnings.append(
            "runtime is not 'enforced'; tool execution will not be isolated"
        )

    if runtime == "enforced" and not sandboxd.get("allowed_client_uids"):
        report.errors.append(
            "allowed_client_uids is empty under enforcement; sandboxd would "
            "reject every client"
        )

    for profile_id, profile in sorted(profiles.items()):
        if (profile or {}).get("provider") == "remote_http":
            continue
        image = (profile or {}).get("image") or ""
        if _PLACEHOLDER in image:
            message = f"profile {profile_id!r} still carries the release-digest placeholder"
            (report.errors if runtime == "enforced" else report.warnings).append(message)
            continue
        if not _DIGEST_PINNED.search(image):
            message = (
                f"profile {profile_id!r} is not digest-pinned; a mutable tag "
                "cannot be verified"
            )
            (report.errors if runtime == "enforced" else report.warnings).append(message)
        if runtime == "enforced" and not (profile or {}).get("toolset_digest"):
            report.errors.append(f"profile {profile_id!r} has no toolset digest")

    template = (payload.get("docker_sandbox") or {}).get("template", "")
    if template and _PLACEHOLDER in template and runtime == "enforced":
        report.errors.append("docker_sandbox.template still carries the placeholder digest")

    return report


def generate(
    config_path: Path,
    *,
    run_root: str,
    client_user: str | None = None,
    client_uids: tuple[int, ...] = (),
    write: bool = True,
) -> tuple[dict, ValidationReport]:
    """Bind a configuration to this host, validate it, and optionally write it."""
    payload = json.loads(config_path.read_text(encoding="utf-8"))

    uids = list(client_uids)
    if client_user:
        resolved = resolve_uid(client_user)
        if resolved is not None:
            uids.append(resolved)

    bound = bind_to_host(payload, run_root=run_root, client_uids=tuple(uids))
    report = validate(bound)

    if write:
        temporary = config_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(bound, indent=2, sort_keys=False) + "\n", encoding="utf-8"
        )
        temporary.replace(config_path)
    return bound, report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tga.deployment.config_generator",
        description="Bind sandbox.json to this host and validate it.",
    )
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--run-root", required=True)
    parser.add_argument("--client-user", default=None)
    parser.add_argument("--client-uid", type=int, action="append", default=[])
    parser.add_argument("--check-only", action="store_true",
                        help="Validate without writing")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))

    try:
        _, report = generate(
            args.config,
            run_root=args.run_root,
            client_user=args.client_user,
            client_uids=tuple(args.client_uid),
            write=not args.check_only,
        )
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False))
    else:
        for warning in report.warnings:
            print(f"warning: {warning}", file=sys.stderr)
        for problem in report.errors:
            print(f"enforcement gap: {problem}", file=sys.stderr)
        if not args.check_only:
            print(f"sandbox configuration bound to {args.run_root}")
        if not report.ok:
            print(
                "this configuration cannot deliver the isolation it declares; "
                "affected profiles are refused at the execution boundary",
                file=sys.stderr,
            )

    # Binding and certifying are different questions. Writing host facts into
    # the configuration can succeed while the deployment still lacks published
    # images, and provisioning must not report that as a failure to bind --
    # the execution boundary is what refuses an unenforceable profile.
    if args.check_only:
        return 0 if report.ok else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
