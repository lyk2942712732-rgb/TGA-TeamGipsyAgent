"""Strict registry for immutable JSON SolverDefinition resources."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from pydantic import ValidationError

from tga.application.capabilities.registry_service import HostCapabilityRegistry
from tga.application.kali import KaliProfileService
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


def solver_definition_root() -> Path:
    configured = os.environ.get("TGA_SOLVER_DEFINITION_ROOT")
    return Path(configured).expanduser().resolve() if configured else (
        Path(__file__).parents[3] / "resources" / "solver_definitions"
    )


class SolverDefinitionRegistry:
    def __init__(
        self,
        root: str | Path,
        *,
        skill_catalog: FileSkillCatalog | None = None,
        host_registry: HostCapabilityRegistry | None = None,
        kali_profiles: KaliProfileService | None = None,
    ) -> None:
        self.root = Path(root)
        self.skill_catalog = skill_catalog or FileSkillCatalog.builtin()
        self.host_registry = host_registry or HostCapabilityRegistry()
        self.kali_profiles = kali_profiles or KaliProfileService()
        self._definitions = self._load()

    @classmethod
    def builtin(
        cls,
        *,
        host_registry: HostCapabilityRegistry | None = None,
        kali_profiles: KaliProfileService | None = None,
    ) -> "SolverDefinitionRegistry":
        registry = cls(
            solver_definition_root(),
            host_registry=host_registry,
            kali_profiles=kali_profiles,
        )
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
            try:
                self._validate_references(definition)
            except (KeyError, ValueError) as exc:
                raise ValueError(
                    f"invalid SolverDefinition references in {path}: {exc}"
                ) from exc
            definitions[definition.id] = definition
        if not definitions:
            raise ValueError(f"no SolverDefinition resources found under {self.root}")
        return definitions

    def _validate_references(self, definition: SolverDefinition) -> None:
        profile = self.host_registry.require_profile(
            definition.host_capability_profile_id
        )
        selected = set(profile.capability_ids)
        selected.update(definition.host_capability_overrides.add)
        selected.difference_update(definition.host_capability_overrides.remove)
        for capability_id in selected:
            capability = self.host_registry.require(capability_id)
            if definition.orchestration_role not in capability.allowed_roles:
                raise ValueError(
                    f"SolverDefinition {definition.id} role {definition.orchestration_role} "
                    f"cannot use Host capability {capability_id}"
                )
        if definition.kali is not None:
            self.kali_profiles.verify_binding(
                definition.kali.profile_id, definition.kali.capabilities
            )
        elif definition.orchestration_role == "worker" and not selected:
            raise ValueError(
                f"SolverDefinition {definition.id} has no Host or Kali capabilities"
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


__all__ = [
    "BUILTIN_DEFINITION_IDS", "SolverDefinitionRegistry", "solver_definition_root",
]
