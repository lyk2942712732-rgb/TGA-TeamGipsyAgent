"""Preview or apply the MCP v1 -> sandbox-profile v2 migration."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


CORE_PROFILES = {
    "binwalk": "offline-analysis",
    "nuclei": "web-assessment",
    "ffuf": "web-assessment",
    "nmap": "tcp-assessment",
}


def migrate(payload: dict) -> tuple[dict, list[str]]:
    value = json.loads(json.dumps(payload))
    changes: list[str] = []
    value["version"] = 2
    for server_id, server in value.get("servers", {}).items():
        profile = CORE_PROFILES.get(server_id)
        if profile:
            server["executionProfileId"] = profile
            server["enabled"] = False
            changes.append(
                f"{server_id}: bound to {profile} but disabled until its image is digest-pinned"
            )
        elif server.get("transport") == "stdio":
            if server.get("enabled", True):
                changes.append(f"{server_id}: disabled pending profile review")
            server["enabled"] = False
    return value, changes


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?", default="config/mcp.json")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    path = Path(args.path).resolve()
    payload = json.loads(path.read_text(encoding="utf-8"))
    migrated, changes = migrate(payload)
    print(json.dumps({"path": str(path), "changes": changes}, ensure_ascii=False, indent=2))
    if args.apply:
        backup = path.with_suffix(path.suffix + ".v1.bak")
        if not backup.exists():
            shutil.copy2(path, backup)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(migrated, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        temporary.replace(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
