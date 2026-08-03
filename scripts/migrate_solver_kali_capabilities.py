"""One-shot JSON migration for pre-cutover Solver Kali bindings.

This command is intentionally not imported by the application runtime.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def migrate_document(payload: dict[str, Any]) -> dict[str, Any]:
    result = json.loads(json.dumps(payload))
    binding = result.get("kali")
    if binding is None:
        return result
    if not isinstance(binding, dict):
        raise ValueError("kali must be an object or null")
    legacy_keys = {"allow_exec", "allow_session"}.intersection(binding)
    if not legacy_keys:
        return result
    if "capabilities" in binding:
        raise ValueError("kali cannot contain both legacy booleans and capabilities")
    unknown = set(binding) - {"profile_id", "allow_exec", "allow_session"}
    if unknown:
        raise ValueError(f"unknown Kali binding fields: {sorted(unknown)}")
    capabilities = []
    if binding.get("allow_exec") is True:
        capabilities.append("kali.exec")
    if binding.get("allow_session") is True:
        capabilities.append("kali.session")
    if any(
        value not in {True, False, None}
        for key, value in binding.items()
        if key in {"allow_exec", "allow_session"}
    ):
        raise ValueError("legacy Kali permission fields must be booleans")
    result["kali"] = (
        {"profile_id": binding.get("profile_id"), "capabilities": capabilities}
        if capabilities
        else None
    )
    if capabilities and not binding.get("profile_id"):
        raise ValueError("enabled Kali binding requires profile_id")
    return result


def migrate_path(source: Path, destination: Path) -> int:
    files = [source] if source.is_file() else sorted(source.rglob("*.json"))
    if not files:
        raise ValueError(f"no JSON files found under {source}")
    count = 0
    for path in files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"JSON root must be an object: {path}")
        migrated = migrate_document(payload)
        target = destination if source.is_file() else destination / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(migrated, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        count += 1
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--in-place", action="store_true")
    args = parser.parse_args()
    if args.in_place == (args.output is not None):
        parser.error("choose exactly one of --in-place or --output")
    destination = args.source if args.in_place else args.output
    assert destination is not None
    print(f"migrated {migrate_path(args.source, destination)} JSON file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
