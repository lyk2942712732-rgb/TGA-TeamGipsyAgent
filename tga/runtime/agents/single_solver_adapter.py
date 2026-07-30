"""Provision the one phase-5 main Solver on schema-v6 domain state."""

from __future__ import annotations

import hashlib
import json

from tga.application.services.solver_factory import SolverFactory
from tga.domain.planning.global_plan import GlobalPlan
from tga.domain.planning.intents import Intent
from tga.domain.planning.local_plan import LocalPlan, LocalPlanStep
from tga.domain.skills.compatibility import legacy_skill_bundle_to_solver
from tga.domain.skills.compatibility import legacy_skill_bundle_to_task_common
from tga.domain.solver.instances import ToolPolicySnapshot
from tga.domain.task.models import ModelSnapshot, TGATask
from tga.domain.task.spec import TaskDirective, TaskSpec
from tga.capabilities.registry import build_default_registry
from tga.evidence.database import utc_now
from tga.infrastructure.persistence import PersistenceBundle
from tga.infrastructure.solver_definitions.registry import SolverDefinitionRegistry


class SingleSolverProvisioner:
    """Idempotently create one durable SolverInstance and its initial plans."""

    def __init__(self, repositories: PersistenceBundle) -> None:
        self.repositories = repositories
        self.definitions = SolverDefinitionRegistry.builtin()

    def ensure(
        self,
        *,
        task: TGATask,
        solver_id: str,
        model_name: str = "",
    ):
        now = utc_now()
        if self.repositories.tasks.get_task_spec(task.id) is None:
            prompt = task.session_input.prompt.strip()
            self.repositories.tasks.save_task_spec(TaskSpec(
                task_id=task.id,
                objective=task.goal,
                instructions=[TaskDirective(
                    id=f"directive_initial_{task.id}",
                    task_id=task.id,
                    kind="instruction",
                    content=prompt,
                    source="user",
                    created_at=now,
                    provenance={"source": "session_input.prompt", "compatibility": True},
                )] if prompt else [],
                provenance={"source": "phase5_single_solver_adapter"},
            ))
        if (
            task.skill_bundle_snapshot is not None
            and self.repositories.tasks.get_task_common_skill_snapshot(task.id) is None
        ):
            self.repositories.tasks.save_task_common_skill_snapshot(
                legacy_skill_bundle_to_task_common(
                    task.skill_bundle_snapshot,
                    task_id=task.id,
                    created_at=now,
                )
            )
        existing = self.repositories.solvers.get_solver(solver_id)
        if existing is not None:
            self._ensure_plan(task=task, solver_id=solver_id)
            return existing
        if self.repositories.solvers.list_solvers(task.id):
            raise RuntimeError("phase-5 runtime permits exactly one durable SolverInstance")
        definition = self.definitions.require("task-supervisor")
        snapshot = task.model_snapshot or ModelSnapshot(
            model=model_name or "legacy-single-solver-adapter",
            capability_fingerprint="0" * 64,
            verification_id="legacy-single-solver-adapter",
            verified_at=now,
            capabilities={},
            max_output_tokens=4_096,
            timeout_seconds=60,
            temperature=0.2,
            reasoning_mode="auto",
        )
        allowed_capabilities = tuple(sorted({
            *(
                item["name"]
                for item in build_default_registry().snapshot()["capabilities"]
                if task.mode in item["modes"]
            ),
            "input_materialize",
            *(
                f"mcp:{item.server_id}:{item.method}"
                for item in task.mcp_capabilities.tools
            ),
        }))[:128]
        policy_payload = {
            "profile": "phase5-single-solver-compatibility",
            "allowed_tool_groups": list(definition.allowed_tool_groups),
            "allowed_capabilities": list(allowed_capabilities),
            "execution_policy_fingerprint": hashlib.sha256(
                json.dumps(
                    task.execution_policy.model_dump(mode="json") if task.execution_policy else {},
                    sort_keys=True,
                ).encode()
            ).hexdigest(),
        }
        tool_policy = ToolPolicySnapshot(
            profile=policy_payload["profile"],
            # Phase 5A keeps the one-Solver checkpoint behavior through an
            # explicit compatibility profile. Ordinary Supervisors do not
            # receive this extra execution group.
            allowed_tool_groups=tuple(dict.fromkeys((
                *definition.allowed_tool_groups, "execution",
            ))),
            allowed_capabilities=allowed_capabilities,
            content_sha256=hashlib.sha256(
                json.dumps(policy_payload, sort_keys=True).encode()
            ).hexdigest(),
        )
        skill_snapshot = (
            legacy_skill_bundle_to_solver(
                task.skill_bundle_snapshot,
                task_id=task.id,
                solver_id=solver_id,
                solver_definition_id=definition.id,
                created_at=now,
            )
            if task.skill_bundle_snapshot is not None else None
        )
        solver = SolverFactory().create(
            instance_id=solver_id,
            task=task,
            definition=definition,
            intent=None,
            model_snapshot=snapshot,
            skill_snapshot=skill_snapshot,
            tool_policy_snapshot=tool_policy,
            parent_solver_id=None,
            created_at=now,
        )
        with self.repositories.transaction():
            self.repositories.solvers.save_definition_snapshot(
                task.id, definition, created_at=now
            )
            self.repositories.solvers.add_solver(solver)
            if skill_snapshot is not None:
                self.repositories.solvers.save_solver_skill_snapshot(skill_snapshot)
            self._ensure_plan(task=task, solver_id=solver_id, now=now)
            self.repositories.events.append_agent_event(
                task.id,
                "SOLVER_INSTANCE_CREATED",
                {
                    "solver_id": solver.id,
                    "definition_id": definition.id,
                    "orchestration_role": solver.orchestration_role,
                    "single_solver_adapter": True,
                },
                solver_id=solver.id,
            )
        return solver

    def _ensure_plan(
        self, *, task: TGATask, solver_id: str, now: str | None = None
    ) -> GlobalPlan:
        existing = self.repositories.plans.get_global_plan(task.id)
        if existing is not None:
            return existing
        created_at = now or utc_now()
        intent = Intent(
            id=f"intent_initial_{task.id}",
            task_id=task.id,
            kind="general",
            title="Complete the task objective",
            objective=task.goal,
            status="running",
            assigned_solver_id=solver_id,
            budget={"turns": 48},
            created_at=created_at,
            updated_at=created_at,
            provenance={"source": "phase5_single_solver_adapter"},
        )
        plan = GlobalPlan(
            id=f"global_plan_{task.id}",
            task_id=task.id,
            version=1,
            status="active",
            intents=[intent],
            created_by_solver_id=solver_id,
            created_at=created_at,
            updated_at=created_at,
            provenance={"single_solver_adapter": True},
        )
        local = LocalPlan(
            id=f"local_plan_{solver_id}",
            task_id=task.id,
            solver_id=solver_id,
            intent_id=intent.id,
            version=1,
            status="active",
            steps=[LocalPlanStep(
                id=f"local_step_{solver_id}_1",
                solver_id=solver_id,
                intent_id=intent.id,
                description="Work toward the authoritative task objective and collect evidence.",
                order=0,
                provenance={"source": "phase5_single_solver_adapter"},
            )],
            created_at=created_at,
            updated_at=created_at,
            provenance={"single_solver_adapter": True},
        )
        self.repositories.plans.save_global_plan(plan)
        self.repositories.plans.save_local_plan(local)
        return plan


__all__ = ["SingleSolverProvisioner"]
