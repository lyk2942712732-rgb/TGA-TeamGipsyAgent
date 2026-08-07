"""Validate a built Solver image against its committed sandbox profile."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _run(*command: str, capture: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )


def _read_manifest(image: str) -> bytes:
    container = _run("docker", "create", image, capture=True).stdout.strip()
    if not container:
        raise RuntimeError("docker create returned no container id")
    try:
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "toolset.json"
            _run("docker", "cp", f"{container}:/opt/tga/toolset.json", str(destination))
            return destination.read_bytes()
    finally:
        subprocess.run(
            ["docker", "rm", "--force", container],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def validate_profiles(image: str, profile_ids: list[str], config_path: Path) -> None:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    profiles = []
    for profile_id in profile_ids:
        profile = config["profiles"].get(profile_id)
        if not profile or profile.get("provider") != "sandboxd":
            raise ValueError(f"unknown sandboxd profile: {profile_id}")
        profiles.append((profile_id, profile))
    if not profiles:
        raise ValueError("no sandboxd profiles selected")

    user = _run(
        "docker", "image", "inspect", image, "--format", "{{.Config.User}}", capture=True
    ).stdout.strip()
    if user != "10001:10001":
        raise RuntimeError(f"{image} runs as {user!r}, expected '10001:10001'")

    raw = _read_manifest(image)
    actual_digest = hashlib.sha256(raw).hexdigest()
    manifest = json.loads(raw)
    compatible = set(manifest.get("compatible_profiles") or ())
    tools = set(manifest.get("tools") or {})
    all_executables: set[str] = set()
    for profile_id, profile in profiles:
        if actual_digest != profile.get("toolset_digest"):
            raise RuntimeError(
                f"{image} toolset digest {actual_digest} does not match profile {profile_id}"
            )
        if manifest.get("schema_version") == 2:
            if manifest.get("image_role") != "universal" or profile_id not in compatible:
                raise RuntimeError(f"{image} universal toolset does not support {profile_id}")
        elif manifest.get("profile_id") != profile_id:
            raise RuntimeError(f"{image} toolset declares a different profile")
        missing_keys = sorted(set(profile["allowed_executables"]) - tools)
        if missing_keys:
            raise RuntimeError(
                f"{image} toolset omits {profile_id} executables: {', '.join(missing_keys)}"
            )
        all_executables.update(profile["allowed_executables"])

    verification = (
        "import json,shutil,sys;"
        "names=json.loads(sys.argv[1]);"
        "missing=[name for name in names if shutil.which(name) is None];"
        "print(json.dumps(missing));"
        "raise SystemExit(bool(missing))"
    )
    _run(
        "docker", "run", "--rm", "--network", "none", "--read-only",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
        "--entrypoint", "/usr/bin/python3", image, "-I", "-c", verification,
        json.dumps(sorted(all_executables), separators=(",", ":")),
    )
    _run(
        "docker", "run", "--rm", "--network", "none", "--read-only",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
        "--entrypoint", "/bin/sh", image, "-c",
        "test ! -e /usr/bin/sudo && "
        "test -z \"$(find /var/lib/apt/lists -mindepth 1 -print -quit)\"",
    )


def validate(image: str, profile_id: str, config_path: Path) -> None:
    """Backwards-compatible single-profile entry point used by local tooling."""
    validate_profiles(image, [profile_id], config_path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--profile")
    selection.add_argument("--all-profiles", action="store_true")
    parser.add_argument(
        "--config", type=Path, default=REPO_ROOT / "config" / "sandbox.json"
    )
    args = parser.parse_args()
    config_path = args.config.resolve()
    if args.all_profiles:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        profile_ids = sorted(
            profile_id
            for profile_id, profile in config["profiles"].items()
            if profile.get("provider") == "sandboxd"
        )
        validate_profiles(args.image, profile_ids, config_path)
    else:
        validate(args.image, args.profile, config_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
