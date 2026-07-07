"""Rule-based Week 1 planner."""

from __future__ import annotations

from tga.contracts import TGATask
from tga.core.intents import make_intent


def plan_initial_intents(task: TGATask):
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

