"""Build, publish and pin the universal Kali image referenced by sandbox.json.

`sandbox.json` ships with `REPLACE_WITH_RELEASE_DIGEST` in every profile image.
Those placeholders cannot be edited by hand into something meaningful: a
`repo@sha256:...` reference is a *registry manifest digest*, which only comes
into existence when an image is pushed. This script closes that loop --

    build -> push -> read back the real digest -> rewrite sandbox.json

-- against whatever registry is given, so the same command serves a local
registry during development and GHCR during a release.

It also removes the namespace assumption baked into sandbox.json: the registry
is a parameter, not the hardcoded `ghcr.io/team-gipsy`.

Examples::

    # Local development against a throwaway registry.
    docker run -d -p 5000:5000 --name tga-registry registry:2
    python scripts/resolve_sandbox_digests.py --registry localhost:5000

    # Build the universal target explicitly.
    python scripts/resolve_sandbox_digests.py --registry localhost:5000 \
        --only tga-kali-universal

    # Report what is still unresolved, changing nothing.
    python scripts/resolve_sandbox_digests.py --check
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

PLACEHOLDER = "REPLACE_WITH_RELEASE_DIGEST"
DIGEST_RE = re.compile(r"@sha256:[a-f0-9]{64}$")
BASE_IMAGE_NAME = "tga-kali-base"

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = REPO_ROOT / "config" / "sandbox.json"
CONTAINERS = REPO_ROOT / "containers" / "kali"


@dataclass(frozen=True, slots=True)
class BuildTarget:
    """One row of the build matrix."""

    image: str
    context: str

    @property
    def context_path(self) -> Path:
        return CONTAINERS / self.context


class BuildError(RuntimeError):
    """A step of the build/publish pipeline failed."""


def run(*command: str, capture: bool = False, check: bool = True) -> str:
    completed = subprocess.run(
        command,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        check=False,
    )
    if check and completed.returncode != 0:
        detail = (completed.stderr or "").strip() or f"exit {completed.returncode}"
        raise BuildError(f"{' '.join(command[:3])}...: {detail}")
    return (completed.stdout or "").strip()


def load_matrix() -> list[BuildTarget]:
    """Read the build matrix through the project's own parser.

    Uses the library function rather than the CLI entrypoint: `main()` parses
    ``sys.argv``, so calling it here would make this script's own flags leak
    into the matrix parser.
    """
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from scripts.kali_build_matrix import load_matrix as read_matrix  # noqa: PLC0415

    targets = [
        BuildTarget(
            image=row["image"],
            context=row["context"],
        )
        for row in read_matrix()
    ]
    if not targets:
        raise BuildError("the build matrix produced no targets")
    return targets


def repo_digest(reference: str) -> str:
    """Return the registry manifest digest recorded for a local image."""
    raw = run(
        "docker", "image", "inspect", reference,
        "--format", "{{json .RepoDigests}}", capture=True,
    )
    try:
        digests = json.loads(raw or "[]")
    except json.JSONDecodeError as exc:
        raise BuildError(f"could not read RepoDigests for {reference}") from exc
    for entry in digests:
        if "@sha256:" in entry:
            return entry.split("@", 1)[1]
    raise BuildError(
        f"{reference} has no repository digest; it must be pushed before it can be pinned"
    )


def toolset_digest_of(image: str) -> str:
    """sha256 of /opt/tga/toolset.json as it exists inside the image."""
    container = run("docker", "create", image, capture=True)
    if not container:
        raise BuildError(f"docker create returned no container id for {image}")
    try:
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "toolset.json"
            run("docker", "cp", f"{container}:/opt/tga/toolset.json", str(destination))
            return hashlib.sha256(destination.read_bytes()).hexdigest()
    finally:
        subprocess.run(
            ["docker", "rm", "--force", container],
            check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )


def build_base(*, registry: str, tag: str, push: bool) -> str:
    """Build the shared Kali base and return a digest-pinned reference."""
    local = f"{BASE_IMAGE_NAME}:{tag}"
    print(f"[base] building {local}", flush=True)
    run("docker", "build", "--tag", local, str(CONTAINERS / "base"))
    if not push:
        # Without a push there is no manifest digest; downstream builds then
        # have to use the mutable tag.
        return local
    remote = f"{registry}/{BASE_IMAGE_NAME}:{tag}"
    run("docker", "tag", local, remote)
    print(f"[base] pushing {remote}", flush=True)
    run("docker", "push", remote)
    digest = repo_digest(remote)
    print(f"[base] {digest}", flush=True)
    return f"{registry}/{BASE_IMAGE_NAME}@{digest}"


def build_solver(
    target: BuildTarget, *, base_reference: str, registry: str, tag: str, push: bool
) -> tuple[str, str]:
    """Build the universal Solver image; return its reference and toolset digest."""
    local = f"{target.image}:{tag}"
    print(f"[{target.image}] building", flush=True)
    run(
        "docker", "build",
        "--build-arg", f"BASE_IMAGE={base_reference}",
        "--tag", local,
        str(target.context_path),
    )
    toolset = toolset_digest_of(local)
    if not push:
        return local, toolset
    remote = f"{registry}/{target.image}:{tag}"
    run("docker", "tag", local, remote)
    print(f"[{target.image}] pushing {remote}", flush=True)
    run("docker", "push", remote)
    digest = repo_digest(remote)
    print(f"[{target.image}] {digest}", flush=True)
    return f"{registry}/{target.image}@{digest}", toolset


def local_profiles(config: dict) -> dict[str, dict]:
    """Profiles backed by the one universal local Kali image."""
    return {
        profile_id: profile
        for profile_id, profile in (config.get("profiles") or {}).items()
        if (profile or {}).get("provider") != "remote_http"
    }


def unresolved(config: dict) -> list[str]:
    """Profiles whose image cannot be verified as an immutable digest."""
    problems = []
    for profile_id, profile in sorted((config.get("profiles") or {}).items()):
        if (profile or {}).get("provider") == "remote_http":
            continue
        image = (profile or {}).get("image") or ""
        if PLACEHOLDER in image or not DIGEST_RE.search(image):
            problems.append(profile_id)
    template = (config.get("docker_sandbox") or {}).get("template", "")
    if template and (PLACEHOLDER in template or not DIGEST_RE.search(template)):
        problems.append("docker_sandbox.template")
    return problems


def apply_published(config: dict, listing: Path) -> tuple[int, list[str]]:
    """Pin profiles from a release listing of immutable references.

    The release workflow already resolves every digest and records it in
    ``published-images.txt``, but nothing wrote those values back, so a
    successful release still left sandbox.json full of placeholders. This
    closes that gap without rebuilding anything.
    """
    known_images = {target.image for target in load_matrix()}
    changed = 0
    problems: list[str] = []
    for line in listing.read_text(encoding="utf-8").splitlines():
        reference = line.strip()
        if not reference:
            continue
        if not DIGEST_RE.search(reference):
            problems.append(f"{reference} is not digest-pinned")
            continue
        name = reference.rsplit("@", 1)[0].rsplit("/", 1)[-1]
        if name == BASE_IMAGE_NAME:
            continue
        if name not in known_images:
            problems.append(f"{name} does not map to any profile")
            continue
        profiles = local_profiles(config)
        if not profiles:
            problems.append("the configuration has no local Kali profiles")
            continue
        for profile in profiles.values():
            if profile.get("image") != reference:
                profile["image"] = reference
                changed += 1
    return changed, problems


def report(config_path: Path) -> int:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    problems = unresolved(config)
    total = sum(
        1 for profile in (config.get("profiles") or {}).values()
        if (profile or {}).get("provider") != "remote_http"
    )
    print(f"config     {config_path}")
    print(f"runtime    {config.get('runtime')}")
    print(f"profiles   {total - len([p for p in problems if p != 'docker_sandbox.template'])}/{total} pinned")
    for problem in problems:
        print(f"  unresolved: {problem}")
    if not problems:
        print("all image references are digest-pinned")
    return 0 if not problems else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="resolve_sandbox_digests",
        description="Build, publish and pin the universal Kali image in sandbox.json.",
    )
    parser.add_argument("--registry", default=None,
                        help="Target registry, e.g. localhost:5000 or ghcr.io/<owner>")
    parser.add_argument("--tag", default="release", help="Tag to publish under")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--only", default="",
                        help="Comma-separated image names or contexts to limit the build to")
    parser.add_argument("--no-push", action="store_true",
                        help="Build without pushing; leaves images unpinnable")
    parser.add_argument("--dry-run", action="store_true",
                        help="Do everything except writing sandbox.json")
    parser.add_argument("--check", action="store_true",
                        help="Only report which references are still unresolved")
    parser.add_argument("--from-published", type=Path, default=None,
                        help="Pin from a release listing of immutable references "
                             "(published-images.txt) instead of building")
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))

    if args.check:
        return report(args.config)

    if args.from_published:
        config = json.loads(args.config.read_text(encoding="utf-8"))
        changed, problems = apply_published(config, args.from_published)
        for problem in problems:
            print(f"error: {problem}", file=sys.stderr)
        if problems:
            print("sandbox.json was not written", file=sys.stderr)
            return 1
        if args.dry_run:
            print(f"dry run: {changed} profile image(s) would be updated")
            return 0
        temporary = args.config.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        temporary.replace(args.config)
        print(f"updated {changed} profile image(s) in {args.config}")
        remaining = unresolved(config)
        if remaining:
            print("still unresolved: " + ", ".join(remaining))
        return 0

    if not args.registry:
        parser.error("--registry, --check or --from-published is required")

    config = json.loads(args.config.read_text(encoding="utf-8"))
    selected = {name.strip() for name in args.only.split(",") if name.strip()}
    targets = [
        target for target in load_matrix()
        if not selected or target.image in selected or target.context in selected
    ]
    if selected:
        matched = {value for target in targets for value in (target.image, target.context)}
        unknown = selected - matched
        if unknown:
            parser.error(f"unknown image target(s): {', '.join(sorted(unknown))}")
    print(f"resolving {len(targets)} image(s) against {args.registry}\n")

    push = not args.no_push
    try:
        base_reference = build_base(registry=args.registry, tag=args.tag, push=push)
        resolved: list[tuple[str, str]] = []
        for target in targets:
            reference, toolset = build_solver(
                target, base_reference=base_reference,
                registry=args.registry, tag=args.tag, push=push,
            )
            resolved.append((reference, toolset))
    except BuildError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    # Refuse to write a reference that is not actually immutable; a mutable
    # tag in sandbox.json would be verified by nothing.
    changed = mismatched = 0
    profiles = local_profiles(config)
    for reference, toolset in resolved:
        if not DIGEST_RE.search(reference):
            print(
                f"error: universal image resolved to {reference}, which is not "
                "digest-pinned (was it pushed?)",
                file=sys.stderr,
            )
            mismatched += 1
            continue
        for profile_id, profile in sorted(profiles.items()):
            expected = profile.get("toolset_digest")
            if expected and expected != toolset:
                print(
                    f"error: {profile_id} toolset digest {toolset} does not match the "
                    f"configured {expected}; the image and the profile disagree",
                    file=sys.stderr,
                )
                mismatched += 1
                continue
            if profile.get("image") != reference:
                profile["image"] = reference
                changed += 1

    if mismatched:
        print(f"\n{mismatched} image(s) failed verification; sandbox.json was not written",
              file=sys.stderr)
        return 1

    if args.dry_run:
        print(f"\ndry run: {changed} profile image(s) would be updated")
        return 0

    temporary = args.config.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    temporary.replace(args.config)
    print(f"\nupdated {changed} profile image(s) in {args.config}")

    remaining = unresolved(config)
    if remaining:
        print("still unresolved: " + ", ".join(remaining))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
