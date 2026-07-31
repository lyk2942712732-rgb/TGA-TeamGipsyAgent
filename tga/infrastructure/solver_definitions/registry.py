"""Strict registry for immutable JSON SolverDefinition resources."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import ValidationError

from tga.capabilities.registry import build_default_registry
from tga.domain.skills.models import SkillDocument
from tga.domain.solver.definitions import SolverDefinition
from tga.infrastructure.skills.catalog import FileSkillCatalog


BUILTIN_DEFINITION_IDS = {
    "architecture-analyst",
    "binary-triage-solver",
    "challenge-classifier",
    "code-audit-solver",
    "containment-advisor",
    "crash-root-cause-solver",
    "ctf-crypto-solver",
    "ctf-forensics-solver",
    "ctf-pwn-solver",
    "ctf-reverse-solver",
    "ctf-supervisor",
    "ctf-web-solver",
    "dynamic-analysis-solver",
    "dynamic-fuzzing-solver",
    "evidence-reviewer",
    "evidence-triage-solver",
    "flag-verifier",
    "host-network-forensics-solver",
    "incident-supervisor",
    "logic-config-recovery-solver",
    "malware-solver",
    "pentest-supervisor",
    "poc-reproduction-solver",
    "research-supervisor",
    "reverse-supervisor",
    "security-reporter",
    "static-analysis-solver",
    "surface-mapper",
    "timeline-ioc-solver",
    "vulnerability-validator",
    "web-api-analyst",
}


class SolverDefinitionRegistry:
    def __init__(
        self,
        root: str | Path,
        *,
        skill_catalog: FileSkillCatalog | None = None,
        capability_names: set[str] | None = None,
    ) -> None:
        self.root = Path(root)
        self.skill_catalog = skill_catalog or FileSkillCatalog.builtin()
        self.capability_names = capability_names or {
            item["name"] for item in build_default_registry().snapshot()["capabilities"]
        }
        self._definitions = self._load()

    @classmethod
    def builtin(cls) -> "SolverDefinitionRegistry":
        registry = cls(Path(__file__).parents[3] / "resources" / "solver_definitions")
        actual = set(registry.ids())
        if actual != BUILTIN_DEFINITION_IDS:
            missing = sorted(BUILTIN_DEFINITION_IDS - actual)
            extra = sorted(actual - BUILTIN_DEFINITION_IDS)
            raise ValueError(f"builtin SolverDefinition set mismatch; missing={missing}, extra={extra}")
        return registry

    def ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._definitions))

    def all(self) -> tuple[SolverDefinition, ...]:
        return tuple(self._definitions[name] for name in self.ids())

    def get(self, definition_id: str) -> SolverDefinition | None:
        return self._definitions.get(definition_id)

    def require(self, definition_id: str) -> SolverDefinition:
        definition = self.get(definition_id)
        if definition is None:
            raise KeyError(f"unknown SolverDefinition: {definition_id}")
        return definition

    def _load(self) -> dict[str, SolverDefinition]:
        definitions: dict[str, SolverDefinition] = {}
        for path in sorted(self.root.rglob("*.json")):
            raw_bytes = path.read_bytes()
            try:
                payload = json.loads(raw_bytes.decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("root must be an object")
                definition = SolverDefinition.model_validate({
                    **payload,
                    "content_sha256": hashlib.sha256(raw_bytes).hexdigest(),
                })
            except (UnicodeError, json.JSONDecodeError, ValidationError, ValueError) as exc:
                raise ValueError(f"invalid SolverDefinition {path}: {exc}") from exc
            if definition.id in definitions:
                raise ValueError(f"duplicate SolverDefinition id: {definition.id}")
            self._validate_references(definition)
            definitions[definition.id] = definition
        if not definitions:
            raise ValueError(f"no SolverDefinition resources found under {self.root}")
        return definitions

    def _validate_references(self, definition: SolverDefinition) -> None:
        unknown_capabilities = set(definition.required_capabilities) - self.capability_names
        if unknown_capabilities:
            raise ValueError(
                f"SolverDefinition {definition.id} references unknown capabilities: "
                f"{sorted(unknown_capabilities)}"
            )
        for skill_name in definition.required_skill_names:
            skill: SkillDocument | None = self.skill_catalog.get(skill_name)
            if skill is None:
                raise ValueError(f"SolverDefinition {definition.id} references unknown Skill {skill_name}")
            unsupported_modes = set(definition.supported_modes) - set(skill.modes)
            if unsupported_modes:
                raise ValueError(
                    f"required Skill {skill_name} does not support Definition modes: "
                    f"{sorted(unsupported_modes)}"
                )
            missing = set(skill.required_capabilities) - set(definition.required_capabilities)
            if missing:
                raise ValueError(
                    f"SolverDefinition {definition.id} omits capabilities required by Skill "
                    f"{skill_name}: {sorted(missing)}"
                )


__all__ = ["BUILTIN_DEFINITION_IDS", "SolverDefinitionRegistry"]
