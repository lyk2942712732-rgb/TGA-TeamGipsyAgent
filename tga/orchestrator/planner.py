"""Rule-based Week 1 planner with explicit rationale."""

from __future__ import annotations

from tga.contracts import Intent, TGATask
from tga.core.intents import make_intent


def plan_initial_intents(task: TGATask) -> list[Intent]:
    if task.mode == "ctf":
        return [
            make_intent(task=task, kind="recon", goal="Identify reachable services and challenge surface", required_tools=["whatweb"]),
            make_intent(task=task, kind="exploit_ctf", goal="Recover a flag from real target output", risk="active"),
            make_intent(task=task, kind="report", goal="Generate CTF report"),
        ]
    if task.mode == "code_audit":
        return [
            make_intent(task=task, kind="code_scan", goal="Run static analysis and secret scanning", required_tools=["semgrep", "gitleaks"]),
            make_intent(task=task, kind="report", goal="Generate code audit report"),
        ]
    return [
        make_intent(task=task, kind="recon", goal="Perform in-scope reconnaissance", required_tools=["whatweb", "nmap"]),
        make_intent(task=task, kind="verify", goal="Verify likely vulnerabilities with evidence", risk="active"),
        make_intent(task=task, kind="report", goal="Generate audit report"),
    ]


def explain_plan(task: TGATask, intents: list[Intent]) -> dict:
    """Return a serializable plan explanation for UI/reporting.

    Week 1 still uses deterministic planning so runs stay reproducible, but the
    rationale is first-class data instead of a hidden assumption in code.
    """
    return {
        "mode": task.mode,
        "goal": task.goal,
        "target": task.target,
        "strategy": _strategy_for(task),
        "steps": [
            {
                "order": index,
                "intent_id": intent.id,
                "kind": intent.kind,
                "goal": intent.goal,
                "risk": intent.risk,
                "required_tools": intent.required_tools,
                "rationale": _intent_rationale(task, intent),
            }
            for index, intent in enumerate(intents, start=1)
        ],
    }


def explain_intent_execution(task: TGATask, intent: Intent) -> dict:
    return {
        "intent_id": intent.id,
        "kind": intent.kind,
        "summary": f"Run {intent.kind} intent",
        "rationale": _intent_rationale(task, intent),
        "selected_tools": intent.required_tools,
        "risk": intent.risk,
        "scope": task.scope,
    }


def explain_adaptation(task: TGATask, intent: Intent, *, status: str, errors: list[str]) -> dict:
    if errors:
        return {
            "intent_id": intent.id,
            "summary": "Continue with explicit tool error evidence",
            "rationale": "The worker returned errors, so the run keeps the artifact and records the failure instead of hiding it.",
            "next_action": "review_tool_setup_or_adjust_plan",
            "errors": errors,
        }
    if intent.kind == "recon" and task.mode in {"web_audit", "ctf"}:
        return {
            "intent_id": intent.id,
            "summary": "Use reconnaissance output to guide verification",
            "rationale": "Reconnaissance is treated as evidence for later verification, not as a vulnerability by itself.",
            "next_action": "verify_or_exploit_based_on_evidence",
            "status": status,
        }
    return {
        "intent_id": intent.id,
        "summary": "Proceed to next planned intent",
        "rationale": "No blocking condition was produced by the worker result.",
        "next_action": "continue_plan",
        "status": status,
    }


def _strategy_for(task: TGATask) -> str:
    if task.mode == "ctf":
        return "Recon the target, attempt flag recovery, then accept only flags backed by real artifact output."
    if task.mode == "code_audit":
        return "Run static analysis and secret scanning, then report only evidence-backed findings."
    if task.mode == "binary_ctf":
        return "Classify the binary challenge surface before exploitation; Week 1 records the gap for follow-up."
    return "Recon in scope, verify likely issues with evidence, and produce a reproducible audit report."


def _intent_rationale(task: TGATask, intent: Intent) -> str:
    if intent.kind == "recon":
        return "Map the reachable surface before making higher-risk decisions."
    if intent.kind == "verify":
        return "Confirm candidate vulnerabilities with scoped evidence instead of reporting scanner guesses."
    if intent.kind == "exploit_ctf":
        return "Attempt flag recovery only after reconnaissance and require provenance before accepting a flag."
    if intent.kind == "code_scan":
        return "Use static tools for broad coverage, then rely on evidence gates before confirmation."
    if intent.kind == "report":
        return "Summarize confirmed evidence, rejected leads, artifacts, and limitations for review."
    return f"Advance the task goal: {task.goal}"
