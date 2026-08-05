"""Query OSV for every Go tool version pinned in the solver Dockerfiles.

Trivy in CI stops at the first image with a CRITICAL, so each run reveals one
vulnerable binary at a time. OSV answers the same question for every pinned
version in one pass, which turns a sequence of CI rounds into a single fix.
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

OSV_API = "https://api.osv.dev/v1/query"

# module path -> the ARG holding its version
TOOLS = {
    "github.com/securego/gosec/v2": "GOSEC_VERSION",
    "github.com/projectdiscovery/nuclei/v3": "NUCLEI_VERSION",
    "github.com/hahwul/dalfox/v2": "DALFOX_VERSION",
    "github.com/ffuf/ffuf/v2": "FFUF_VERSION",
    "github.com/projectdiscovery/httpx": "HTTPX_VERSION",
    "github.com/projectdiscovery/dnsx": "DNSX_VERSION",
    "github.com/projectdiscovery/subfinder/v2": "SUBFINDER_VERSION",
    "github.com/projectdiscovery/naabu/v2": "NAABU_VERSION",
}


def pinned_versions(root: Path) -> dict[str, str]:
    found: dict[str, str] = {}
    for dockerfile in sorted((root / "containers/kali/solvers").glob("*/Dockerfile")):
        for line in dockerfile.read_text(encoding="utf-8").splitlines():
            match = re.match(r"\s*ARG\s+([A-Z_]+_VERSION)=(\S+)", line)
            if match:
                found.setdefault(match.group(1), match.group(2))
    return found


def query(module: str, version: str) -> list[dict]:
    payload = json.dumps(
        {"package": {"name": module, "ecosystem": "Go"}, "version": version}
    ).encode()
    request = urllib.request.Request(
        OSV_API, data=payload, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read()).get("vulns", []) or []


def severity_of(vuln: dict) -> str:
    for item in vuln.get("severity", []) or []:
        if item.get("type", "").startswith("CVSS"):
            return item.get("score", "")
    database = vuln.get("database_specific", {}) or {}
    return str(database.get("severity", "")) or "-"


def fixed_versions(vuln: dict, module: str) -> str:
    fixes: set[str] = set()
    for affected in vuln.get("affected", []) or []:
        if affected.get("package", {}).get("name") != module:
            continue
        for ranges in affected.get("ranges", []) or []:
            for event in ranges.get("events", []) or []:
                if "fixed" in event:
                    fixes.add(event["fixed"])
    return ", ".join(sorted(fixes)) or "-"


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    versions = pinned_versions(root)

    total = 0
    for module, arg in TOOLS.items():
        raw = versions.get(arg)
        if not raw:
            print(f"{module}: no {arg} found")
            continue
        version = raw.lstrip("v")
        try:
            vulns = query(module, version)
        except Exception as exc:  # noqa: BLE001 - report and keep scanning
            print(f"{module}@{raw}: query failed ({exc})")
            continue
        if not vulns:
            print(f"[ok]   {module}@{raw}")
            continue
        for vuln in vulns:
            total += 1
            ident = vuln.get("id", "?")
            aliases = ", ".join(vuln.get("aliases", []) or [])
            print(
                f"[VULN] {module}@{raw}\n"
                f"       {ident} {aliases}\n"
                f"       severity: {severity_of(vuln)}\n"
                f"       fixed in: {fixed_versions(vuln, module)}"
            )
    print(f"\n{total} advisory match(es) across {len(TOOLS)} pinned Go tools")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
