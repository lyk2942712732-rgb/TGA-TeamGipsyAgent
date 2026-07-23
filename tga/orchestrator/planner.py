"""Rule-based Week 1 planner with explicit rationale."""

from __future__ import annotations

from tga.contracts import Intent, TGATask
from tga.agent.llm_planner import reorder_with_llm
from tga.core.intents import make_intent
from tga.models.bootstrap import model_config_status
from tga.modes import mode_profile


def plan_initial_intents(task: TGATask) -> list[Intent]:
    factories = {
        "ctf": lambda: [
            make_intent(task=task, kind="recon", goal="Classify the challenge and identify its evidence-producing attack surface"),
            make_intent(task=task, kind="exploit_ctf", goal="Recover a flag from real target or Artifact output", risk="active"),
            make_intent(task=task, kind="report", goal="Record the verified flag path and evidence"),
        ],
        "penetration_test": lambda: [
            make_intent(task=task, kind="recon", goal="Confirm scope and map the authorized attack surface", required_tools=["whatweb", "nmap"]),
            make_intent(task=task, kind="verify", goal="Validate vulnerability hypotheses and real impact with evidence", risk="active"),
            make_intent(task=task, kind="report", goal="Report coverage, confirmed findings, leads, and limitations"),
        ],
        "incident_response": lambda: [
            make_intent(task=task, kind="recon", goal="Preserve and inventory available evidence"),
            make_intent(task=task, kind="verify", goal="Build an evidence-backed timeline, IOC set, root cause, and impact assessment"),
            make_intent(task=task, kind="report", goal="Report conclusions, coverage, containment, and recovery guidance"),
        ],
        "vulnerability_research": lambda: [
            make_intent(task=task, kind="code_scan", goal="Map structure and candidate vulnerability surface", required_tools=["semgrep", "gitleaks"]),
            make_intent(task=task, kind="verify", goal="Reproduce and minimize supported vulnerability hypotheses", risk="active"),
            make_intent(task=task, kind="report", goal="Report root cause, impact, prerequisites, coverage, and limitations"),
        ],
        "reverse_engineering": lambda: [
            make_intent(task=task, kind="recon", goal="Identify format, architecture, and analysis surface"),
            make_intent(task=task, kind="verify", goal="Recover requested logic, behavior, configuration, or data with analysis Artifacts"),
            make_intent(task=task, kind="report", goal="Preserve scripts, key outputs, conclusions, and limitations"),
        ],
    }
    intents = factories[task.mode]()
    return reorder_with_llm(task, intents)[0]


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
        "llm": model_config_status(),
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
    if intent.kind == "recon" and task.mode in {"penetration_test", "ctf"}:
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
    return mode_profile(task.mode).prompt()


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
