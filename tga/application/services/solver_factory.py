"""Create durable SolverInstance values without starting a runner."""

from __future__ import annotations

from tga.domain.planning.intents import Intent
from tga.domain.skills.models import SolverSkillSnapshot
from tga.domain.solver.definitions import SolverDefinition
from tga.domain.solver.instances import (
    SolverInstance,
    SolverTimestamps,
    ToolPolicySnapshot,
)
from tga.domain.task.models import ModelSnapshot, TGATask


class SolverFactory:
    def create(
        self,
        *,
        instance_id: str,
        task: TGATask,
        definition: SolverDefinition,
        intent: Intent | None,
        model_snapshot: ModelSnapshot,
        skill_snapshot: SolverSkillSnapshot | None,
        tool_policy_snapshot: ToolPolicySnapshot,
        parent_solver_id: str | None,
        created_at: str,
    ) -> SolverInstance:
        subtype = str(getattr(task.mode_config, "subtype", "") or "") or None
        if not definition.supports(mode=task.mode, subtype=subtype):
            raise ValueError(
                f"SolverDefinition {definition.id} does not support task mode/subtype"
            )
        if intent is not None:
            if intent.task_id != task.id:
                raise ValueError("assigned Intent belongs to a different task")
            if intent.kind not in definition.accepted_intent_kinds:
                raise ValueError(
                    f"SolverDefinition {definition.id} does not accept intent kind {intent.kind}"
                )
        if definition.orchestration_role == "worker" and intent is None:
            raise ValueError("worker SolverDefinition requires assigned Intent")
        if definition.orchestration_role == "supervisor" and intent is not None:
            raise ValueError("supervisor SolverDefinition does not take a worker Intent assignment")
        if not set(tool_policy_snapshot.allowed_tool_groups).issubset(
            definition.allowed_tool_groups
        ):
            raise ValueError("ToolPolicy enables a group outside SolverDefinition")
        missing_capabilities = set(definition.required_capabilities) - set(
            tool_policy_snapshot.allowed_capabilities
        )
        if missing_capabilities:
            raise ValueError(
                f"ToolPolicy omits required Solver capabilities: {sorted(missing_capabilities)}"
            )
        if skill_snapshot is not None:
            if (
                skill_snapshot.task_id != task.id
                or skill_snapshot.solver_id != instance_id
                or skill_snapshot.solver_definition_id != definition.id
            ):
                raise ValueError("SolverSkillSnapshot does not belong to requested instance")
            selected_names = {skill.name for skill in skill_snapshot.skills}
            if not set(definition.required_skill_names).issubset(selected_names):
                raise ValueError("SolverSkillSnapshot omits a required Skill")
            for skill in skill_snapshot.skills:
                denied = set(skill.required_capabilities) - set(
                    tool_policy_snapshot.allowed_capabilities
                )
                if denied:
                    raise ValueError(
                        f"Skill {skill.name} is incompatible with ToolPolicy: {sorted(denied)}"
                    )
        elif definition.required_skill_names:
            raise ValueError("SolverDefinition requires a SolverSkillSnapshot")

        return SolverInstance(
            id=instance_id,
            task_id=task.id,
            definition_id=definition.id,
            definition_version=definition.version,
            definition_content_sha256=definition.content_sha256,
            parent_solver_id=parent_solver_id,
            assigned_intent_id=intent.id if intent else None,
            orchestration_role=definition.orchestration_role,
            specialties=definition.specialties,
            status="created",
            model_snapshot=model_snapshot.model_copy(deep=True),
            skill_bundle_snapshot=skill_snapshot.model_copy(deep=True) if skill_snapshot else None,
            tool_policy_snapshot=tool_policy_snapshot.model_copy(deep=True),
            budget=definition.default_budget.model_copy(deep=True),
            completion_authority=definition.completion_authority,
            transcript_ref=f"solver://{instance_id}/transcript",
            private_workspace_ref=f"solver://{instance_id}/workspace",
            timestamps=SolverTimestamps(created_at=created_at, updated_at=created_at),
        )


__all__ = ["SolverFactory"]

