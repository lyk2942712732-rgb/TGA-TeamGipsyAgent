"""Markdown report generation."""

from __future__ import annotations

from typing import Any

from tga.reporting.evidence_renderer import format_list, quote_excerpt
import re

from tga.reporting.report_model import events_by_type, findings_by_status, read_artifact_payload, runtime_actions, runtime_events, tools_used


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
        "## Execution Evidence",
    ]
    web_payloads = []
    for artifact in snapshot.get("artifacts", []):
        if artifact.get("tool") == "web-flag-hunter":
            payload = read_artifact_payload(snapshot, artifact)
            if payload:
                web_payloads.append((artifact, payload))
    if not web_payloads:
        lines.append("- No web hunter evidence was recorded.")
    for artifact, payload in web_payloads:
        lines.append(f"### Web Flag Hunter ({artifact.get('id')})")
        visited = payload.get("visited") or []
        lines.append(f"- Visited URLs: {len(visited)}")
        for url in visited[:20]:
            lines.append(f"  - {url}")
        for lead in payload.get("leads") or []:
            lines.append(f"- Lead/Error: {quote_excerpt(str(lead))}")
        responses = payload.get("responses") or []
        for response in responses[:12]:
            text = str(response.get("text") or "")
            flags = _find_flags(text, (snapshot.get("task") or {}).get("flag_format"))
            summary = _summarize_response(text)
            lines.append(
                f"- HTTP {response.get('status')} {response.get('url')}"
                + (f" flags={format_list(flags)}" if flags else "")
            )
            if response.get("error"):
                lines.append(f"  - Error: {quote_excerpt(str(response.get('error')))}")
            if summary:
                lines.append(f"  - Excerpt: {quote_excerpt(summary)}")
    lines.extend([
        "",
        "## Decision Trace",
    ]
    )
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
    if snapshot.get("session"):
        _append_runtime_sections(lines, snapshot)
    lines.extend(["", "## Limitations", "- Only evidence captured in artifacts is treated as ground truth."])
    return "\n".join(lines) + "\n"


def _find_flags(text: str, flag_format: str | None) -> list[str]:
    pattern = flag_format or r"flag\{[^}]+\}"
    try:
        regex = re.compile(pattern)
    except re.error:
        regex = re.compile(r"flag\{[^}]+\}")
    seen = []
    for match in regex.finditer(text):
        value = match.group(0)
        if value not in seen:
            seen.append(value)
    return seen


def _summarize_response(text: str) -> str:
    clean = re.sub(r"\s+", " ", text).strip()
    return clean[:260]


def _append_runtime_sections(lines: list[str], snapshot: dict[str, Any]) -> None:
    """Render the two v2 report layers from durable runtime state only."""
    session = snapshot.get("session") or {}
    board = snapshot.get("board") or {}
    events = runtime_events(snapshot)
    actions = {item.get("id"): item for item in runtime_actions(snapshot)}
    solvers = snapshot.get("solvers") or []
    lines.extend([
        "", "## Session Outcome",
        f"- Status: {session.get('status', 'unknown')}",
        f"- Turns: {session.get('turn_count', 0)}/{session.get('max_turns', 'unknown')}",
        f"- Stop Reason: {session.get('stop_reason') or 'none'}",
        f"- Active Solver: {session.get('active_solver_id') or 'none'}",
        "", "## Validated Hypotheses",
    ])
    verified = [item for item in board.get("hypotheses") or [] if item.get("status") == "verified"]
    _append_hypotheses(lines, verified)
    lines.extend(["", "## Inconclusive / Rejected Hypotheses"])
    unresolved = [item for item in board.get("hypotheses") or [] if item.get("status") in {"inconclusive", "rejected", "superseded"}]
    _append_hypotheses(lines, unresolved)
    lines.extend(["", "## Tools, Capabilities and Policy Refusals"])
    capabilities = sorted({str(item.get("capability")) for item in actions.values() if item.get("capability")})
    lines.append(f"- Capabilities Used: {format_list(capabilities)}")
    refusals = [event for event in events if event.get("type") in {"GATE_REJECTED", "ACTION_BUDGET_EXCEEDED", "POLICY_REJECTED", "RESULT_REJECTED"}]
    if not refusals:
        lines.append("- Policy Refusals: none")
    for event in refusals:
        payload = event.get("payload") or {}
        lines.append(f"- seq {event.get('seq')}: {event.get('type')} — {_safe_summary(payload)}")
    lines.extend(["", "## Runtime Report (seq ordered)", "### Solver Lifecycle"])
    if not solvers:
        lines.append("- none")
    for solver in solvers:
        lines.append(f"- {solver.get('id')} role={solver.get('role')} status={solver.get('status')} started={solver.get('started_at')} finished={solver.get('finished_at') or 'ongoing'}")
    lines.extend(["", "### Action Specs and Results"])
    if not actions:
        lines.append("- none")
    for action in actions.values():
        lines.append(f"- {action.get('id')} {action.get('status')} capability={action.get('capability')} target={_redact(str(action.get('target') or ''))} artifacts={format_list(action.get('artifact_ids') or [])} summary={_redact(str(action.get('summary') or ''))}")
    lines.extend(["", "### Timeline"])
    if not events:
        lines.append("- none")
    for event in events:
        lines.append(f"- seq {event.get('seq', event.get('id', '?'))} {event.get('created_at', '')} {event.get('type')}: {_safe_summary(event.get('payload') or {})}")


def _append_hypotheses(lines: list[str], hypotheses: list[dict[str, Any]]) -> None:
    if not hypotheses:
        lines.append("- none")
        return
    for item in hypotheses:
        lines.append(f"- {item.get('statement')} [{item.get('status')}] class={item.get('attack_class')} entry={item.get('entry_point')} artifacts={format_list(item.get('evidence_artifact_ids') or [])} result={_redact(str(item.get('last_result') or ''))}")


def _safe_summary(payload: dict[str, Any]) -> str:
    value = payload.get("summary") or payload.get("reason") or payload.get("status") or payload.get("message") or str(payload)
    return quote_excerpt(_redact(str(value)))


def _redact(value: str) -> str:
    return re.sub(r"(?i)((?:authorization|cookie|set-cookie|token|secret|api[_-]?key|password)\s*[:=]\s*)([^\s;,]+)", r"\1[REDACTED]", value)

