"""Markdown report generation."""

from __future__ import annotations

from typing import Any

from tga.reporting.report_model import tools_used


def render_markdown_report(snapshot: dict[str, Any]) -> str:
    task = snapshot.get("task") or {}
    lines = [
        "# TGA Report",
        "",
        "## Summary",
        f"- Task: {task.get('name', '')}",
        f"- Mode: {task.get('mode', '')}",
        f"- Target: {task.get('target', '')}",
        f"- Scope: {', '.join(task.get('scope') or [])}",
        f"- Intensity: {task.get('intensity', '')}",
        f"- Tools Used: {', '.join(tools_used(snapshot)) or 'none'}",
        "",
        "## Confirmed Findings",
    ]
    confirmed = [f for f in snapshot.get("findings", []) if f.get("status") == "confirmed"]
    if not confirmed:
        lines.append("- none")
    for finding in confirmed:
        lines.extend([
            f"### {finding.get('title')}",
            f"- Severity: {finding.get('severity')}",
            f"- Target: {finding.get('target')}",
            f"- Evidence Artifact: {finding.get('evidence_artifact_id')}",
            f"- Evidence Excerpt: {finding.get('evidence_excerpt') or ''}",
            "- Reproduction Steps:",
        ])
        for step in finding.get("reproduction_steps") or []:
            lines.append(f"  - {step}")
        if finding.get("remediation"):
            lines.append(f"- Remediation: {finding.get('remediation')}")
    lines.extend(["", "## CTF Flags"])
    flags = snapshot.get("flags", [])
    if not flags:
        lines.append("- none")
    for flag in flags:
        lines.append(f"- {flag.get('value')} (artifact: {flag.get('evidence_artifact_id')})")
    lines.extend(["", "## Unverified Leads"])
    candidates = [f for f in snapshot.get("findings", []) if f.get("status") != "confirmed"]
    if not candidates:
        lines.append("- none")
    for finding in candidates:
        lines.append(f"- {finding.get('title')} [{finding.get('status')}]")
    lines.extend(["", "## Artifacts"])
    for artifact in snapshot.get("artifacts", []):
        lines.append(f"- {artifact.get('id')} {artifact.get('kind')} {artifact.get('tool') or ''} {artifact.get('target') or ''}")
    lines.extend(["", "## Limitations", "- Week 1 MVP output; manual verification is still recommended."])
    return "\n".join(lines) + "\n"

