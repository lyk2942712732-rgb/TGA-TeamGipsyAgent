"""Durable team provisioning without starting any Solver runner."""

from __future__ import annotations

import hashlib
import json

from tga.application.services.skill_selection_service import (
    SolverSkillSelectionRequest,
    SolverSkillSelectionService,
)
from tga.application.services.solver_factory import SolverFactory
from tga.capabilities.registry import build_default_registry
from tga.domain.planning import GlobalPlan, Intent, LocalPlan, LocalPlanStep
from tga.domain.solver import (
    SolverAssignment,
    SolverRun,
    TeamRuntimeState,
    ToolPolicySnapshot,
)
from tga.domain.task.models import ModelSnapshot
from tga.evidence.database import utc_now
from tga.infrastructure.persistence.errors import PersistenceConflict
from tga.infrastructure.skills.catalog import FileSkillCatalog


INITIAL_INTENT_KIND = {
    "ctf": "recon",
    "penetration_test": "recon",
    "incident_response": "forensics",
    "vulnerability_research": "code_audit",
    "reverse_engineering": "binary_analysis",
}


class TeamRuntime:
    def __init__(self, *, task, repositories, definitions, template) -> None:
        self.task = task
        self.repositories = repositories
        self.definitions = definitions
        self.template = template
        self.factory = SolverFactory()
        self.skills = SolverSkillSelectionService(FileSkillCatalog.builtin())
        self.registry = build_default_registry()

    def bootstrap(self) -> TeamRuntimeState:
        current = self.repositories.orchestration.get_state(self.task.id)
        if current is not None:
            if current.team_template_sha256 != self.template.content_sha256:
                raise PersistenceConflict(
                    "TeamTemplate changed after TaskOrchestrator bootstrap"
                )
            return current
        now = utc_now()
        existing_supervisors = [
            item for item in self.repositories.solvers.list_solvers(self.task.id)
            if item.orchestration_role == "supervisor"
        ]
        supervisor_id = (
            existing_supervisors[0].id
            if existing_supervisors
            else self._stable_solver_id("supervisor", self.template.supervisor_definition_id)
        )
        supervisor = self.repositories.solvers.get_solver(supervisor_id)
        if supervisor is None:
            supervisor = self._create_solver(
                solver_id=supervisor_id,
                definition=self.definitions.require(self.template.supervisor_definition_id),
                intent=None,
                parent_solver_id=None,
                now=now,
            )
        if self.repositories.plans.get_global_plan(self.task.id) is None:
            intent = Intent(
                id=f"intent_initial_{self.task.id}",
                task_id=self.task.id,
                kind=INITIAL_INTENT_KIND[self.task.mode],
                title="Initial specialist investigation",
                objective=self.task.goal,
                status="pending",
                budget={"turns": 16},
                priority=0,
                created_at=now,
                updated_at=now,
                provenance={
                    "source": "phase6_task_orchestrator",
                    "allowed_resource_ids": [item.id for item in self.task.session_input.files],
                },
            )
            self.repositories.plans.save_global_plan(GlobalPlan(
                id=f"global_plan_{self.task.id}",
                task_id=self.task.id,
                version=1,
                status="active",
                intents=[intent],
                created_by_solver_id=supervisor.id,
                created_at=now,
                updated_at=now,
                provenance={"source": "phase6_task_orchestrator"},
            ))
        max_active_workers = self._max_active_workers()
        state = TeamRuntimeState(
            task_id=self.task.id,
            team_template_sha256=self.template.content_sha256,
            supervisor_solver_id=supervisor.id,
            status="running",
            max_active_workers=max_active_workers,
            max_total_solvers=self._max_total_solvers(),
            created_at=now,
            updated_at=now,
        )
        self.repositories.orchestration.save_state(state)
        self.repositories.events.append_agent_event(
            self.task.id,
            "ORCHESTRATOR_STARTED",
            {
                "supervisor_solver_id": supervisor.id,
                "team_template_sha256": self.template.content_sha256,
                "max_active_workers": max_active_workers,
                "max_total_solvers": state.max_total_solvers,
            },
            solver_id=supervisor.id,
        )
        self.repositories.events.append_agent_event(
            self.task.id,
            "TASK_ORCHESTRATOR_STARTED",
            {
                "supervisor_solver_id": supervisor.id,
                "team_template_sha256": self.template.content_sha256,
                "max_active_workers": max_active_workers,
                "max_total_solvers": state.max_total_solvers,
            },
            solver_id=supervisor.id,
        )
        return state

    def create_worker(self, *, intent, definition, attempt: int = 1) -> SolverAssignment:
        with self.repositories.transaction():
            return self._create_worker_locked(
                intent=intent, definition=definition, attempt=attempt
            )

    def _create_worker_locked(
        self, *, intent, definition, attempt: int
    ) -> SolverAssignment:
        state = self.bootstrap()
        solver_id = self._stable_solver_id(
            "worker", definition.id, intent_id=intent.id, attempt=attempt
        )
        assignment_id = f"assignment_{solver_id}"
        existing = self.repositories.orchestration.get_assignment(assignment_id)
        if existing is not None:
            return existing
        self._enforce_solver_limit()
        now = utc_now()
        solver = self._create_solver(
            solver_id=solver_id,
            definition=definition,
            intent=intent,
            parent_solver_id=state.supervisor_solver_id,
            now=now,
        )
        claimed = self.repositories.plans.claim_pending_intent(
            intent.id,
            solver.id,
            expected_version=self.repositories.plans.get_intent_version(intent.id),
        )
        if self.repositories.plans.get_local_plan(solver.id, intent.id) is None:
            self.repositories.plans.save_local_plan(LocalPlan(
                id=f"local_plan_{solver.id}_{intent.id}",
                task_id=self.task.id,
                solver_id=solver.id,
                intent_id=intent.id,
                version=1,
                status="active",
                steps=[LocalPlanStep(
                    id=f"local_step_{solver.id}_1",
                    solver_id=solver.id,
                    intent_id=intent.id,
                    description=intent.objective,
                    order=0,
                    provenance={"source": "phase6_assignment"},
                )],
                created_at=now,
                updated_at=now,
                provenance={"source": "phase6_assignment"},
            ))
        solver = self.repositories.solvers.update_solver_status(solver.id, "queued")
        skill = self.repositories.solvers.get_solver_skill_snapshot(solver.id)
        provenance = intent.provenance or {}
        assignment = SolverAssignment(
            id=assignment_id,
            task_id=self.task.id,
            solver_id=solver.id,
            intent_id=intent.id,
            assigned_by_solver_id=state.supervisor_solver_id or "",
            intent=claimed,
            allowed_resources=tuple(provenance.get("allowed_resource_ids") or ()),
            relevant_knowledge_ids=tuple(provenance.get("relevant_knowledge_ids") or ()),
            relevant_evidence_claim_ids=tuple(
                provenance.get("relevant_evidence_claim_ids") or ()
            ),
            skill_snapshot=skill,
            tool_policy_snapshot=solver.tool_policy_snapshot,
            budget=solver.budget,
            attempt=attempt,
            status="accepted",
            assigned_at=now,
            accepted_at=now,
        )
        self.repositories.orchestration.save_assignment(assignment)
        run = SolverRun(
            id=f"run_{assignment.id}",
            task_id=self.task.id,
            solver_id=solver.id,
            assignment_id=assignment.id,
            intent_id=intent.id,
            orchestration_role="worker",
            attempt=attempt,
            state="queued",
            created_at=now,
            updated_at=now,
        )
        self.repositories.orchestration.create_solver_run(run)
        self.repositories.events.append_agent_event(
            self.task.id,
            "SOLVER_RUN_QUEUED",
            {
                "run_id": run.id,
                "assignment_id": assignment.id,
                "intent_id": intent.id,
                "attempt": attempt,
            },
            solver_id=solver.id,
            intent_id=intent.id,
        )
        self.repositories.events.append_agent_event(
            self.task.id,
            "SOLVER_ASSIGNED",
            {
                "assignment_id": assignment.id,
                "intent_id": intent.id,
                "definition_id": definition.id,
                "attempt": attempt,
            },
            solver_id=solver.id,
            intent_id=intent.id,
        )
        self.repositories.events.append_agent_event(
            self.task.id,
            "INTENT_ASSIGNED",
            {
                "intent_id": intent.id,
                "solver_id": solver.id,
                "assignment_id": assignment.id,
                "attempt": attempt,
            },
            solver_id=solver.id,
            intent_id=intent.id,
        )
        self.repositories.events.append_agent_event(
            self.task.id,
            "INTENT_CLAIMED",
            {
                "intent_id": intent.id,
                "solver_id": solver.id,
                "assignment_id": assignment.id,
            },
            solver_id=solver.id,
            intent_id=intent.id,
        )
        return assignment

    def ensure_role_solver(self, role: str):
        if role not in {"reviewer", "reporter"}:
            raise ValueError(f"unsupported team role: {role}")
        definition_id = (
            self.template.reviewer_definition_id
            if role == "reviewer" else self.template.reporter_definition_id
        )
        existing = [
            item for item in self.repositories.solvers.list_solvers(self.task.id)
            if item.orchestration_role == role and str(item.status) not in {"failed", "cancelled"}
        ]
        if existing:
            return existing[0]
        failed_count = sum(
            item.orchestration_role == role
            for item in self.repositories.solvers.list_solvers(self.task.id)
        )
        attempt = failed_count + 1
        self._enforce_solver_limit()
        solver_id = self._stable_solver_id(role, definition_id, attempt=attempt)
        state = self.bootstrap()
        solver = self._create_solver(
            solver_id=solver_id,
            definition=self.definitions.require(definition_id),
            intent=None,
            parent_solver_id=state.supervisor_solver_id,
            now=utc_now(),
        )
        return self.repositories.solvers.update_solver_status(solver.id, "queued")

    def _create_solver(
        self, *, solver_id: str, definition, intent, parent_solver_id: str | None, now: str
    ):
        policy = self._tool_policy(definition)
        skill = self.skills.select_solver_skills(SolverSkillSelectionRequest(
            task_id=self.task.id,
            solver_id=solver_id,
            mode=self.task.mode,
            mode_config=self.task.mode_config.model_dump(mode="json"),
            definition=definition,
            intent=intent,
            available_capabilities=policy.allowed_capabilities,
            tool_policy_allowed_capabilities=policy.allowed_capabilities,
            created_at=now,
        ))
        solver = self.factory.create(
            instance_id=solver_id,
            task=self.task,
            definition=definition,
            intent=intent,
            model_snapshot=self._model_snapshot(now),
            skill_snapshot=skill,
            tool_policy_snapshot=policy,
            parent_solver_id=parent_solver_id,
            created_at=now,
        )
        with self.repositories.transaction():
            self.repositories.solvers.save_definition_snapshot(
                self.task.id, definition, created_at=now
            )
            self.repositories.solvers.add_solver(solver)
            self.repositories.solvers.save_solver_skill_snapshot(skill)
            self.repositories.events.append_agent_event(
                self.task.id,
                "SOLVER_CREATED",
                {
                    "solver_id": solver.id,
                    "definition_id": solver.definition_id,
                    "orchestration_role": str(solver.orchestration_role),
                    "parent_solver_id": solver.parent_solver_id,
                    "assigned_intent_id": solver.assigned_intent_id,
                },
                solver_id=solver.id,
                intent_id=solver.assigned_intent_id,
            )
        return solver

    def _tool_policy(self, definition) -> ToolPolicySnapshot:
        capabilities = tuple(sorted(
            item["name"]
            for item in self.registry.snapshot()["capabilities"]
            if self.task.mode in item["modes"]
        ))
        payload = {
            "definition": definition.id,
            "profile": definition.tool_policy_profile,
            "groups": definition.allowed_tool_groups,
            "capabilities": capabilities,
        }
        return ToolPolicySnapshot(
            profile=definition.tool_policy_profile,
            allowed_tool_groups=definition.allowed_tool_groups,
            allowed_capabilities=capabilities,
            content_sha256=hashlib.sha256(
                json.dumps(payload, sort_keys=True).encode()
            ).hexdigest(),
        )

    def _model_snapshot(self, now: str) -> ModelSnapshot:
        return self.task.model_snapshot or ModelSnapshot(
            model="phase6-serial-orchestrator",
            capability_fingerprint="0" * 64,
            verification_id="phase6-serial-orchestrator",
            verified_at=now,
            capabilities={},
            max_output_tokens=4_096,
            timeout_seconds=60,
            temperature=0.2,
            reasoning_mode="auto",
        )

    def _max_total_solvers(self) -> int:
        requested = self.task.execution_budget.get("max_total_solvers")
        return min(self.template.max_total_solvers, int(requested)) if requested is not None else self.template.max_total_solvers

    def _max_active_workers(self) -> int:
        requested = int(self.task.execution_budget.get("max_active_workers", 1))
        # Phase-6 TeamTemplates retain their original hash so in-flight tasks
        # remain recoverable. Phase 7 makes the explicit Task budget the
        # bounded concurrency override; absence keeps serial behavior.
        return max(1, min(2, requested))

    def _enforce_solver_limit(self) -> None:
        if len(self.repositories.solvers.list_solvers(self.task.id)) >= self._max_total_solvers():
            raise PersistenceConflict("Task max_total_solvers limit exceeded")

    def _stable_solver_id(
        self, role: str, definition_id: str, *, intent_id: str = "", attempt: int = 1
    ) -> str:
        digest = hashlib.sha256(
            f"{self.task.id}\0{role}\0{definition_id}\0{intent_id}\0{attempt}".encode()
        ).hexdigest()[:20]
        return f"solver_{role}_{digest}_a{attempt}"


__all__ = ["INITIAL_INTENT_KIND", "TeamRuntime"]
