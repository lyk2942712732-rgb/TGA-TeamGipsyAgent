"""Authoritative task requirements, separate from unverified runtime input."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from tga.domain.task.models import ResourceRef


DirectiveKind = Literal["instruction", "constraint", "success_criterion"]


class TaskDirective(BaseModel):
    model_config = {"extra": "forbid"}

    id: str
    task_id: str
    kind: DirectiveKind
    content: str = Field(min_length=1, max_length=8_000)
    source: Literal["user", "operator", "system", "legacy_import"]
    created_at: str
    provenance: dict[str, Any] = Field(default_factory=dict)


class TaskSpec(BaseModel):
    """Creation-time source of truth for what the task asks TGA to do."""

    model_config = {"extra": "forbid"}

    task_id: str
    objective: str = Field(min_length=1, max_length=8_000)
    instructions: list[TaskDirective] = Field(default_factory=list, max_length=128)
    constraints: list[TaskDirective] = Field(default_factory=list, max_length=128)
    success_criteria: list[TaskDirective] = Field(default_factory=list, max_length=128)
    resources: list[ResourceRef] = Field(default_factory=list, max_length=256)
    legacy_import: bool = False
    provenance: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_directive_ownership(self) -> "TaskSpec":
        groups = (
            ("instructions", "instruction", self.instructions),
            ("constraints", "constraint", self.constraints),
            ("success_criteria", "success_criterion", self.success_criteria),
        )
        directive_ids: list[str] = []
        for field_name, expected_kind, directives in groups:
            for directive in directives:
                if directive.task_id != self.task_id or directive.kind != expected_kind:
                    raise ValueError(f"{field_name} directive ownership or kind does not match TaskSpec")
                directive_ids.append(directive.id)
        if len(directive_ids) != len(set(directive_ids)):
            raise ValueError("TaskSpec directive ids must be unique")
        if any(resource.role != "target" for resource in self.resources):
            raise ValueError("TaskSpec resources may contain only authoritative target resources")
        return self


__all__ = ["DirectiveKind", "TaskDirective", "TaskSpec"]

