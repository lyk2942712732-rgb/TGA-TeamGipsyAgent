"""Deterministic report rendering from persisted state and structured model prose."""

from __future__ import annotations

from typing import Any


def render_markdown(snapshot: dict[str, Any], draft: Any) -> str:
    task = snapshot["task"]
    lines = [
        f"# {task['name']}",
        "",
        "## Executive summary",
        "",
        draft.executive_summary,
        "",
        "## Task",
        "",
        f"- Mode: `{task['mode']}`",
        f"- Objective: {task['spec']['objective']}",
        "",
        "## Methodology",
        "",
    ]
    lines.extend(f"{index}. {item}" for index, item in enumerate(draft.methodology, 1))
    lines.extend(["", "## Findings", ""])
    findings = snapshot.get("findings", [])
    if not findings:
        lines.append("No evidence-backed finding was confirmed.")
    for finding in findings:
        lines.extend(
            [
                f"### {finding['title']}",
                "",
                f"- Severity: `{finding['severity']}`",
                f"- Status: `{finding['status']}`",
                "",
                finding.get("description") or "",
                "",
                "Evidence claims:",
                "",
            ]
        )
        lines.extend(
            f"- `{claim_id}`" for claim_id in finding.get("evidence_claim_ids", [])
        )
        if finding.get("remediation"):
            lines.extend(["", f"Remediation: {finding['remediation']}"])
        lines.append("")
    lines.extend(["## Evidence inventory", ""])
    for artifact in snapshot.get("artifacts", []):
        lines.append(
            f"- `{artifact['id']}` - {artifact['kind']} - SHA256 `{artifact['sha256']}`"
        )
    lines.extend(["", "## Limitations", ""])
    if draft.limitations:
        lines.extend(f"- {item}" for item in draft.limitations)
    else:
        lines.append("- None reported.")
    lines.append("")
    return "\n".join(lines)


__all__ = ["render_markdown"]
