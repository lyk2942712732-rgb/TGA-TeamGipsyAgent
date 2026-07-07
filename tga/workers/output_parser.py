"""Parse worker marker lines."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from tga.contracts import Finding


@dataclass
class ParsedOutput:
    facts: list[str] = field(default_factory=list)
    leads: list[str] = field(default_factory=list)
    deadends: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def parse_markers(text: str, *, task_id: str) -> ParsedOutput:
    parsed = ParsedOutput()
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("VERIFIED_FACT="):
            parsed.facts.append(line.split("=", 1)[1].strip())
        elif line.startswith("UNVERIFIED_LEAD="):
            parsed.leads.append(line.split("=", 1)[1].strip())
        elif line.startswith("DEADEND="):
            parsed.deadends.append(line.split("=", 1)[1].strip())
        elif line.startswith("FOUND_FLAG="):
            parsed.flags.append(line.split("=", 1)[1].strip())
        elif line.startswith("ARTIFACT="):
            parsed.artifacts.append(line.split("=", 1)[1].strip())
        elif line.startswith("TOOL_ERROR="):
            parsed.errors.append(line.split("=", 1)[1].strip())
        elif line.startswith("CONFIRMED_FINDING_JSON="):
            raw = line.split("=", 1)[1].strip()
            try:
                payload = json.loads(raw)
                payload["task_id"] = payload.get("task_id") or task_id
                payload["status"] = "candidate"
                parsed.findings.append(Finding.model_validate(payload))
            except Exception as exc:  # noqa: BLE001
                parsed.errors.append(f"INVALID_FINDING_JSON: {exc}")
    return parsed

