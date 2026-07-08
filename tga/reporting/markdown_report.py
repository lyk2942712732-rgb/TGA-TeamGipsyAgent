"""Markdown report generation."""

from __future__ import annotations

from typing import Any

from tga.reporting.evidence_renderer import format_list, quote_excerpt
from tga.reporting.report_model import events_by_type, findings_by_status, tools_used


def render_markdown_report(snapshot: dict[str, Any]) -> str:
    task = snapshot.get("task") or {}
    lines = [
        "# TGA Report",
        "",
        "## Summary",
        f"- Task: {task.get('name', '')}",
        f"- Mode: {task.get('mode', '')}",
        f"- Target: {task.get('target', '')}",
        f"- Scope: {format_list(task.get('scope'))}",
        f"- Intensity: {task.get('intensity', '')}",
        f"- Allow Active Scan: {task.get('allow_active_scan', False)}",
        f"- Tools Used: {format_list(tools_used(snapshot))}",
        "",
        "## Decision Trace",
    ]
    plan_events = events_by_type(snapshot, "PLAN_CREATED")
    decision_events = events_by_type(
        snapshot,
        "DECISION_TRACE",
        "SAFETY_DECISION",
        "INTENT_RESULT",
        "ADAPTATION_DECISION",
    )
    if not plan_events and not decision_events:
        lines.append("- none")
    for event in plan_events:
        payload = event.get("payload") or {}
        lines.append(f"- Plan: {quote_excerpt(payload.get('rationale') or payload.get('summary') or '')}")
        plan = payload.get("plan") or {}
        for step in plan.get("steps") or []:
            lines.append(
                f"  - {step.get('order')}. {step.get('kind')} risk={step.get('risk')} "
                f"tools={format_list(step.get('required_tools'))}: "
                f"{quote_excerpt(step.get('rationale') or '')}"
            )
    for event in decision_events:
        payload = event.get("payload") or {}
        label = event.get("type")
        summary = payload.get("summary") or payload.get("reason") or payload.get("status") or payload
        intent_id = event.get("intent_id") or payload.get("intent_id") or "task"
        lines.append(f"- {label} [{intent_id}]: {quote_excerpt(str(summary))}")
        rationale = payload.get("rationale")
        if rationale:
            lines.append(f"  - Rationale: {quote_excerpt(str(rationale))}")
    lines.extend([
        "",
        "## Confirmed Findings",
    ]
    )
    confirmed = findings_by_status(snapshot, "confirmed")
    if not confirmed:
        lines.append("- none")
    for finding in confirmed:
        lines.extend([
            f"### {finding.get('title')}",
            f"- Severity: {finding.get('severity')}",
            f"- Target: {finding.get('target')}",
            f"- Evidence Artifact: {finding.get('evidence_artifact_id')}",
            f"- Evidence Excerpt: {quote_excerpt(finding.get('evidence_excerpt') or '')}",
            "- Reproduction Steps:",
        ])
        for step in finding.get("reproduction_steps") or []:
            lines.append(f"  - {step}")
        if not finding.get("reproduction_steps"):
            lines.append("  - none")
        if finding.get("remediation"):
            lines.append(f"- Remediation: {finding.get('remediation')}")
    lines.extend(["", "## CTF Flags"])
    flags = snapshot.get("flags", [])
    if not flags:
        lines.append("- none")
    for flag in flags:
        lines.append(f"- {flag.get('value')} (artifact: {flag.get('evidence_artifact_id')})")
    lines.extend(["", "## Unverified Leads"])
    candidates = [
        finding
        for finding in snapshot.get("findings", [])
        if finding.get("status") != "confirmed"
    ]
    if not candidates:
        lines.append("- none")
    for finding in candidates:
        lines.append(
            f"- {finding.get('title')} [{finding.get('status')}]"
            f" target={finding.get('target')} severity={finding.get('severity')}"
        )
    lead_events = events_by_type(snapshot, "unverified_lead", "lead")
    for event in lead_events:
        payload = event.get("payload") or {}
        lines.append(f"- {quote_excerpt(str(payload.get('text') or payload))}")

    lines.extend(["", "## Dead Ends"])
    deadend_events = events_by_type(snapshot, "deadend", "dead_end")
    if not deadend_events:
        lines.append("- none")
    for event in deadend_events:
        payload = event.get("payload") or {}
        lines.append(f"- {quote_excerpt(str(payload.get('reason') or payload))}")

    lines.extend(["", "## Artifacts"])
    artifacts = snapshot.get("artifacts", [])
    if not artifacts:
        lines.append("- none")
    for artifact in artifacts:
        lines.append(
            f"- {artifact.get('id')} kind={artifact.get('kind')} "
            f"tool={artifact.get('tool') or 'none'} target={artifact.get('target') or 'none'} "
            f"path={artifact.get('path')}"
        )
    lines.extend(["", "## Limitations", "- Week 1 MVP output; manual verification is still recommended."])
    return "\n".join(lines) + "\n"

