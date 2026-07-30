"""Typed user-intervention ingestion without policy or knowledge promotion."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import uuid4

from tga.domain.task.hints import TaskHint
from tga.domain.task.interventions import InterventionKind, UserIntervention
from tga.domain.task.spec import TaskDirective, TaskSpec
from tga.evidence.database import utc_now


@dataclass(frozen=True)
class InterventionResult:
    intervention: UserIntervention
    hint: TaskHint | None = None
    directive: TaskDirective | None = None


class InterventionService:
    """Record user input and apply only its explicitly typed semantic effect."""

    def __init__(self, repositories) -> None:
        self.repositories = repositories

    def record(
        self,
        *,
        task_id: str,
        kind: InterventionKind,
        content: str,
        actor_id: str | None = None,
        scope: Literal["task", "solver", "intent"] = "task",
        target_id: str | None = None,
    ) -> InterventionResult:
        text = content.strip()
        if not text:
            raise ValueError("intervention content must not be empty")
        if len(text) > 8_000:
            raise ValueError("intervention content exceeds 8000 characters")
        task = self.repositories.tasks.get_task(task_id)
        if task is None:
            raise KeyError(f"task not found: {task_id}")
        now = utc_now()
        intervention = UserIntervention(
            id=f"intervention_{uuid4().hex[:12]}",
            task_id=task_id,
            kind=kind,
            content=text,
            scope=scope,
            target_id=target_id,
            created_at=now,
            actor_id=actor_id,
            provenance={"ingestion": "typed_intervention_v1"},
        )
        hint: TaskHint | None = None
        directive: TaskDirective | None = None
        with self.repositories.transaction():
            self.repositories.tasks.add_intervention(intervention)
            if kind == "hint":
                hint = TaskHint(
                    id=f"hint_{uuid4().hex[:12]}",
                    task_id=task_id,
                    content=text,
                    source=actor_id or "user",
                    status="unreviewed",
                    scope=scope,
                    target_id=target_id,
                    created_at=now,
                    provenance={"intervention_id": intervention.id},
                )
                self.repositories.tasks.save_hint(hint)
            elif kind in {"instruction", "constraint"}:
                spec = self.repositories.tasks.get_task_spec(task_id) or TaskSpec(
                    task_id=task_id,
                    objective=task.goal,
                    provenance={"created_by": "intervention_service"},
                )
                directive = TaskDirective(
                    id=f"directive_{uuid4().hex[:12]}",
                    task_id=task_id,
                    kind=kind,
                    content=text,
                    source="operator" if actor_id else "user",
                    created_at=now,
                    provenance={"intervention_id": intervention.id},
                )
                field = "instructions" if kind == "instruction" else "constraints"
                spec = spec.model_copy(
                    update={field: [*getattr(spec, field), directive]}, deep=True
                )
                self.repositories.tasks.save_task_spec(spec)

            self.repositories.events.append_agent_event(
                task_id,
                "USER_INTERVENTION_RECORDED",
                {
                    "intervention_id": intervention.id,
                    "kind": kind,
                    "scope": scope,
                    "target_id": target_id,
                    "hint_id": hint.id if hint else None,
                    "directive_id": directive.id if directive else None,
                },
            )
            if hint is not None:
                self.repositories.events.append_agent_event(
                    task_id,
                    "TASK_HINT_ADDED",
                    {
                        "intervention_id": intervention.id,
                        "hint_id": hint.id,
                        "status": hint.status,
                    },
                )
            if directive is not None:
                self.repositories.events.append_agent_event(
                    task_id,
                    "TASK_SPEC_UPDATED",
                    {
                        "intervention_id": intervention.id,
                        "directive_id": directive.id,
                        "directive_kind": directive.kind,
                    },
                )
        return InterventionResult(
            intervention=intervention,
            hint=hint,
            directive=directive,
        )


__all__ = ["InterventionResult", "InterventionService"]
