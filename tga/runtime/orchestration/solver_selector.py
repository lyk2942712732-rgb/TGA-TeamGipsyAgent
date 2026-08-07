"""Deterministic SolverDefinition selection for one runnable Intent."""

from __future__ import annotations


PREFERRED_BY_INTENT = {
    "challenge_classification": "challenge-classifier",
    "ctf_web": "ctf-web-solver",
    "ctf_pwn": "ctf-pwn-solver",
    "ctf_reverse": "ctf-reverse-solver",
    "ctf_crypto": "ctf-crypto-solver",
    "ctf_forensics": "ctf-forensics-solver",
    "flag_verification": "flag-verifier",
    "surface_mapping": "surface-mapper",
    "web_api_analysis": "web-api-analyst",
    "vulnerability_validation": "vulnerability-validator",
    "evidence_triage": "evidence-triage-solver",
    "timeline_ioc": "timeline-ioc-solver",
    "host_network_forensics": "host-network-forensics-solver",
    "malware_analysis": "malware-solver",
    "containment_advice": "containment-advisor",
    "architecture_analysis": "architecture-analyst",
    "code_audit": "code-audit-solver",
    "dynamic_fuzzing": "dynamic-fuzzing-solver",
    "crash_root_cause": "crash-root-cause-solver",
    "poc_reproduction": "poc-reproduction-solver",
    "binary_triage": "binary-triage-solver",
    "static_analysis": "static-analysis-solver",
    "dynamic_analysis": "dynamic-analysis-solver",
    "logic_config_recovery": "logic-config-recovery-solver",
}


class SolverSelector:
    def __init__(self, *, definitions, template, task) -> None:
        self.definitions = definitions
        self.template = template
        self.task = task

    def select(self, intent):
        candidates = [
            definition
            for definition in self._eligible_definitions()
            if intent.kind in definition.accepted_intent_kinds
        ]
        preferred = PREFERRED_BY_INTENT.get(intent.kind)
        selected = next((item for item in candidates if item.id == preferred), None)
        if selected is None:
            selected = candidates[0] if candidates else None
        if selected is None:
            raise ValueError(f"no Worker SolverDefinition accepts Intent kind: {intent.kind}")
        return selected

    def supported_intent_kinds(self) -> tuple[str, ...]:
        """Return the canonical Intent kinds dispatchable for this task."""
        return tuple(sorted({
            kind
            for definition in self._eligible_definitions()
            for kind in definition.accepted_intent_kinds
        }))

    def supports_kind(self, kind: str) -> bool:
        return kind in self.supported_intent_kinds()

    def _eligible_definitions(self) -> tuple:
        subtype = str(getattr(self.task.mode_config, "subtype", "") or "") or None
        return tuple(
            definition
            for definition_id in self.template.available_solver_definition_ids
            for definition in (self.definitions.require(definition_id),)
            if definition.supports(mode=self.task.mode, subtype=subtype)
            and self._allowed_by_mode_config(definition.id)
        )

    def _allowed_by_mode_config(self, definition_id: str) -> bool:
        config = self.task.mode_config
        if definition_id == "vulnerability-validator":
            return bool(getattr(config, "exploit_validation", False))
        if definition_id == "dynamic-fuzzing-solver":
            return bool(getattr(config, "allow_fuzzing", False))
        if definition_id == "poc-reproduction-solver":
            return bool(getattr(config, "allow_target_execution", False))
        if definition_id == "dynamic-analysis-solver":
            return bool(getattr(config, "allow_dynamic_execution", False))
        if definition_id == "containment-advisor":
            return str(getattr(config, "response_authority", "analysis_only")) != "analysis_only"
        return True


__all__ = ["SolverSelector"]
