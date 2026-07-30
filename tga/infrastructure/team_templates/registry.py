"""Strict JSON-compatible YAML TeamTemplate registry."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import ValidationError

from tga.domain.solver.teams import TeamTemplate
from tga.infrastructure.solver_definitions.registry import SolverDefinitionRegistry
from tga.modes import TASK_MODES, TaskMode


class TeamTemplateRegistry:
    def __init__(self, root: str | Path, *, definitions: SolverDefinitionRegistry) -> None:
        self.root = Path(root)
        self.definitions = definitions
        self._templates = self._load()

    @classmethod
    def builtin(
        cls,
        *,
        definitions: SolverDefinitionRegistry | None = None,
    ) -> "TeamTemplateRegistry":
        registry = cls(
            Path(__file__).parents[3] / "resources" / "team_templates",
            definitions=definitions or SolverDefinitionRegistry.builtin(),
        )
        if set(registry.modes()) != set(TASK_MODES):
            raise ValueError("builtin TeamTemplates must cover exactly the five task modes")
        return registry

    def modes(self) -> tuple[TaskMode, ...]:
        return tuple(sorted(self._templates))  # type: ignore[return-value]

    def all(self) -> tuple[TeamTemplate, ...]:
        return tuple(self._templates[mode] for mode in self.modes())

    def get(self, mode: TaskMode) -> TeamTemplate | None:
        return self._templates.get(mode)

    def require(self, mode: TaskMode) -> TeamTemplate:
        template = self.get(mode)
        if template is None:
            raise KeyError(f"no TeamTemplate for mode: {mode}")
        return template

    def _load(self) -> dict[TaskMode, TeamTemplate]:
        templates: dict[TaskMode, TeamTemplate] = {}
        for path in sorted(self.root.glob("*.yaml")):
            raw_bytes = path.read_bytes()
            try:
                # JSON is a strict YAML 1.2 subset and needs no extra dependency.
                payload = json.loads(raw_bytes.decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("root must be an object")
                template = TeamTemplate.model_validate({
                    **payload,
                    "content_sha256": hashlib.sha256(raw_bytes).hexdigest(),
                })
            except (UnicodeError, json.JSONDecodeError, ValidationError, ValueError) as exc:
                raise ValueError(f"invalid TeamTemplate {path}: {exc}") from exc
            if template.mode in templates:
                raise ValueError(f"duplicate TeamTemplate mode: {template.mode}")
            if path.stem != template.mode:
                raise ValueError(f"TeamTemplate filename must match mode: {path}")
            self._validate_definitions(template)
            templates[template.mode] = template
        if not templates:
            raise ValueError(f"no TeamTemplate resources found under {self.root}")
        return templates

    def _validate_definitions(self, template: TeamTemplate) -> None:
        supervisor = self.definitions.require(template.supervisor_definition_id)
        reviewer = self.definitions.require(template.reviewer_definition_id)
        reporter = self.definitions.require(template.reporter_definition_id)
        if supervisor.orchestration_role != "supervisor":
            raise ValueError("TeamTemplate supervisor definition has wrong role")
        if reviewer.orchestration_role != "reviewer":
            raise ValueError("TeamTemplate reviewer definition has wrong role")
        if reporter.orchestration_role != "reporter":
            raise ValueError("TeamTemplate reporter definition has wrong role")
        referenced = {
            template.supervisor_definition_id,
            template.reviewer_definition_id,
            template.reporter_definition_id,
            *template.required_solver_definition_ids,
            *template.available_solver_definition_ids,
            *(rule.definition_id for rule in template.spawn_rules),
        }
        for definition_id in referenced:
            definition = self.definitions.require(definition_id)
            if template.mode not in definition.supported_modes:
                raise ValueError(
                    f"SolverDefinition {definition_id} does not support TeamTemplate mode {template.mode}"
                )
        for definition_id in template.available_solver_definition_ids:
            if self.definitions.require(definition_id).orchestration_role != "worker":
                raise ValueError("available solver definitions must have worker role")


__all__ = ["TeamTemplateRegistry"]

