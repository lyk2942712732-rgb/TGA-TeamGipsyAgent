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


def validate(image: str, profile_id: str, config_path: Path) -> None:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    profile = config["profiles"].get(profile_id)
    if not profile or profile.get("provider") != "sandboxd":
        raise ValueError(f"unknown sandboxd profile: {profile_id}")

    user = _run(
        "docker", "image", "inspect", image, "--format", "{{.Config.User}}", capture=True
    ).stdout.strip()
    if user != "10001:10001":
        raise RuntimeError(f"{image} runs as {user!r}, expected '10001:10001'")

    raw = _read_manifest(image)
    actual_digest = hashlib.sha256(raw).hexdigest()
    if actual_digest != profile.get("toolset_digest"):
        raise RuntimeError(
            f"{image} toolset digest {actual_digest} does not match profile {profile_id}"
        )
    manifest = json.loads(raw)
    if manifest.get("profile_id") != profile_id:
        raise RuntimeError(f"{image} toolset declares a different profile")
    missing_keys = sorted(set(profile["allowed_executables"]) - set(manifest.get("tools", {})))
    if missing_keys:
        raise RuntimeError(f"{image} toolset omits executables: {', '.join(missing_keys)}")

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
        json.dumps(profile["allowed_executables"], separators=(",", ":")),
    )
    _run(
        "docker", "run", "--rm", "--network", "none", "--read-only",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
        "--entrypoint", "/bin/sh", image, "-c",
        "test ! -e /usr/bin/sudo && "
        "test -z \"$(find /var/lib/apt/lists -mindepth 1 -print -quit)\"",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument(
        "--config", type=Path, default=REPO_ROOT / "config" / "sandbox.json"
    )
    args = parser.parse_args()
    validate(args.image, args.profile, args.config.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
