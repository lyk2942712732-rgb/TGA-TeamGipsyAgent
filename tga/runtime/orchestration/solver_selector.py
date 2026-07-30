"""Deterministic SolverDefinition selection for one runnable Intent."""

from __future__ import annotations


PREFERRED_BY_INTENT = {
    "validation": "vulnerability-validator",
    "web_analysis": "web-network-analyst",
    "code_audit": "code-audit",
    "binary_analysis": "binary-analysis",
    "forensics": "forensics-analysis",
    "recon": "recon-triage",
}


class SolverSelector:
    def __init__(self, *, definitions, template) -> None:
        self.definitions = definitions
        self.template = template

    def select(self, intent):
        candidates = [
            self.definitions.require(definition_id)
            for definition_id in self.template.available_solver_definition_ids
            if intent.kind in self.definitions.require(definition_id).accepted_intent_kinds
        ]
        preferred = PREFERRED_BY_INTENT.get(intent.kind)
        selected = next((item for item in candidates if item.id == preferred), None)
        if selected is None:
            selected = candidates[0] if candidates else None
        if selected is None:
            raise ValueError(f"no Worker SolverDefinition accepts Intent kind: {intent.kind}")
        return selected


__all__ = ["SolverSelector"]
