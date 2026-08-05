"""Durable team provisioning without starting any Solver runner."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from tga.application.services.skill_candidate_activation_service import (
    SkillCandidateActivationService,
)
from tga.application.services.skill_selection_service import (
    SolverSkillSelectionRequest,
    SolverSkillSelectionService,
)
from tga.application.services.solver_factory import SolverFactory
from tga.application.capabilities import CapabilityAssignmentService
from tga.deployment.paths import run_root as resolve_run_root
from tga.domain.planning import GlobalPlan, Intent, LocalPlan, LocalPlanStep
from tga.domain.retrieval import RetrievalPolicy
from tga.domain.skills import SkillActivation
from tga.domain.solver import (
    SolverAssignment,
    SolverRun,
    TeamRuntimeState,
    CapabilityBindingSnapshot,
)
from tga.domain.task.models import ModelSnapshot
from tga.domain.kali import SandboxProfileSnapshot
from tga.evidence.database import utc_now
from tga.infrastructure.persistence.errors import PersistenceConflict
from tga.infrastructure.persistence import PersistenceBundle
from tga.infrastructure.skills.catalog import FileSkillCatalog
from tga.runtime.retrieval import RetrievalService


INITIAL_INTENT_KIND = {
    "ctf": "challenge_classification",
    "penetration_test": "surface_mapping",
    "incident_response": "evidence_triage",
    "vulnerability_research": "architecture_analysis",
    "reverse_engineering": "binary_triage",
}


class TeamRuntime:
    def __init__(self, *, task, repositories, definitions, template) -> None:
        self.task = task
        self.repositories = repositories
        self.definitions = definitions
        self.template = template
        self.factory = SolverFactory()
        self.skills = SolverSkillSelectionService(FileSkillCatalog.builtin())
        self.assignments = CapabilityAssignmentService(definitions=definitions)

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
                    "allowed_resource_ids": [
                        item.resource_id for item in self.task.session_input.files
                    ],
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
            capability_binding_snapshot=solver.capability_binding_snapshot,
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
            max_turns=solver.budget.max_turns,
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
        binding = self._capability_binding(definition)
        available_capabilities = binding.host_capability_ids + (
            binding.kali.capabilities if binding.kali is not None else ()
        )
        visible_manifest = self.assignments.manifest(
            task_id=self.task.id,
            solver_id=solver_id,
            definition=definition,
            intent_id=intent.id if intent else None,
            execution_policy=self.task.execution_policy,
            capability_snapshot=binding,
        )
        tool_policy_allowed_capabilities = tuple(
            item.id for item in visible_manifest.host_capabilities
        ) + (
            visible_manifest.kali.capabilities
            if visible_manifest.kali is not None
            else ()
        )
        approved = ()
        candidates = ()
        decision = None
        index_snapshot_ids: tuple[str, ...] = ()
        skill_corpus = self._open_skill_corpus()
        if skill_corpus is not None:
            skill_policy = self._skill_retrieval_policy()
            try:
                gateway = RetrievalService(skill_corpus.retrieval)
                query = " ".join((
                    definition.id,
                    *definition.specialties,
                    *definition.default_skill_tags,
                    intent.kind if intent else "",
                    intent.objective if intent else self.task.goal,
                ))
                pack = gateway.retrieve_skill_candidates(
                    task_id=self.task.id,
                    solver_id=solver_id,
                    intent_id=intent.id if intent else None,
                    query=query,
                    policy=skill_policy,
                    workspace_id=self.task.workspace_id,
                )
                if pack is not None:
                    activation = SkillCandidateActivationService(
                        repository=skill_corpus.retrieval,
                        assignment_service=self.assignments,
                    ).activate(
                        pack=pack,
                        task_id=self.task.id,
                        solver_id=solver_id,
                        mode=self.task.mode,
                        definition=definition,
                        intent=intent,
                        available_capabilities=available_capabilities,
                        tool_policy_allowed_capabilities=tool_policy_allowed_capabilities,
                        policy=skill_policy,
                        workspace_id=self.task.workspace_id,
                        created_at=now,
                        reserved_skill_names=tuple(definition.required_skill_names),
                        max_skills=max(0, 3 - len(definition.required_skill_names)),
                    )
                    approved = activation.approved
                    candidates = activation.candidates
                    decision = activation.decision
                    index_snapshot_ids = decision.index_snapshot_ids
            finally:
                skill_corpus.close()
        skill = self.skills.select_solver_skills(SolverSkillSelectionRequest(
            task_id=self.task.id,
            solver_id=solver_id,
            mode=self.task.mode,
            mode_config=self.task.mode_config.model_dump(mode="json"),
            definition=definition,
            intent=intent,
            available_capabilities=available_capabilities,
            tool_policy_allowed_capabilities=tool_policy_allowed_capabilities,
            created_at=now,
        ), approved_candidates=approved,
            selection_decision_id=decision.id if decision else None,
            skill_index_snapshot_ids=index_snapshot_ids,
        )
        solver = self.factory.create(
            instance_id=solver_id,
            task=self.task,
            definition=definition,
            intent=intent,
            model_snapshot=self._model_snapshot(now),
            skill_snapshot=skill,
            capability_binding_snapshot=binding,
            parent_solver_id=parent_solver_id,
            created_at=now,
        )
        with self.repositories.transaction():
            self.repositories.solvers.save_definition_snapshot(
                self.task.id, definition, created_at=now
            )
            self.repositories.solvers.add_solver(solver)
            if decision is not None:
                self.repositories.solvers.save_skill_selection_decision(decision)
                self._append_skill_decision_events(decision, candidates)
            self.repositories.solvers.save_solver_skill_snapshot(skill)
            self.repositories.events.append_agent_event(
                self.task.id,
                "SKILL_SNAPSHOT_FROZEN",
                {
                    "solver_id": solver.id,
                    "selection_decision_id": skill.selection_decision_id,
                    "index_snapshot_ids": list(skill.skill_index_snapshot_ids),
                    "skills": [
                        {
                            "name": item.name,
                            "version": item.version,
                            "content_sha256": item.content_sha256,
                            "document_id": item.document_id,
                            "revision_id": item.revision_id,
                        }
                        for item in skill.skills
                    ],
                },
                solver_id=solver.id,
                intent_id=solver.assigned_intent_id,
            )
            for item in skill.skills:
                activation = SkillActivation(
                    id="skillact_" + hashlib.sha256(
                        f"{solver.id}\0{item.name}\0{item.content_sha256}".encode()
                    ).hexdigest()[:32],
                    task_id=self.task.id,
                    solver_id=solver.id,
                    skill_name=item.name,
                    skill_content_sha256=item.content_sha256,
                    source="solver_specialized",
                    reason="; ".join(item.selection_reasons),
                    activated_at=now,
                    document_id=item.document_id,
                    revision_id=item.revision_id,
                    retrieval_run_id=item.retrieval_run_id,
                    index_snapshot_id=item.index_snapshot_id,
                    selection_decision_id=skill.selection_decision_id,
                )
                self.repositories.solvers.save_skill_activation(activation)
                self.repositories.events.append_agent_event(
                    self.task.id,
                    "SKILL_ACTIVATED",
                    activation.model_dump(mode="json"),
                    solver_id=solver.id,
                    intent_id=solver.assigned_intent_id,
                )
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

    def _append_skill_decision_events(self, decision, candidate_values) -> None:
        self.repositories.events.append_agent_event(
            self.task.id,
            "SKILL_RETRIEVAL_COMPLETED",
            {
                "retrieval_run_ids": list(decision.retrieval_run_ids),
                "index_snapshot_ids": list(decision.index_snapshot_ids),
                "candidate_ids": list(decision.candidate_ids),
            },
            solver_id=decision.solver_id,
            intent_id=decision.intent_id,
        )
        candidates = {item.id: item for item in candidate_values}
        for rejection in decision.rejected_candidates:
            candidate = candidates.get(rejection.candidate_id)
            payload = {
                "decision_id": decision.id,
                "candidate_id": rejection.candidate_id,
                "code": rejection.code,
                "reason": rejection.reason,
            }
            if candidate is not None:
                payload.update({
                    "retrieval_run_id": candidate.retrieval_run_id,
                    "index_snapshot_id": candidate.index_snapshot_id,
                    "document_id": candidate.document_id,
                    "revision_id": candidate.revision_id,
                    "content_sha256": candidate.content_sha256,
                })
            self.repositories.events.append_agent_event(
                self.task.id,
                "SKILL_CANDIDATE_REJECTED",
                payload,
                solver_id=decision.solver_id,
                intent_id=decision.intent_id,
            )
        self.repositories.events.append_agent_event(
            self.task.id,
            "SKILL_SELECTION_DECIDED",
            decision.model_dump(mode="json"),
            solver_id=decision.solver_id,
            intent_id=decision.intent_id,
        )

    def _open_skill_corpus(self):
        configured = os.environ.get("TGA_SKILL_CORPUS_DB")
        path = (
            Path(configured)
            if configured
            else resolve_run_root() / "_skill-corpus" / "evidence.db"
        )
        if not configured and not path.is_file():
            return None
        return PersistenceBundle.open(path)

    def _skill_retrieval_policy(self) -> RetrievalPolicy:
        scopes = ["solver", "task"]
        if self.task.workspace_id:
            scopes.append("workspace")
        scopes.append("global")
        return RetrievalPolicy(
            allowed_source_kinds=(
                "documentation", "code_repository", "knowledge_base",
                "uploaded_file", "web_reference",
            ),
            allowed_trust_levels=("authoritative", "trusted"),
            allowed_owner_scopes=tuple(scopes),
            task_artifact_access=False,
            cross_solver_access=False,
            max_results=16,
            max_context_tokens=24_000,
        )

    def _capability_binding(self, definition) -> CapabilityBindingSnapshot:
        host_entries = self.assignments.resolve_host(definition)
        host_capabilities = tuple(item.id for item in host_entries)
        kali_runtime = self.assignments.resolve_kali(definition)
        kali_profile = None
        if definition.kali is not None:
            kali_profile = SandboxProfileSnapshot.model_validate(
                self.assignments.kali_profiles.config.profile(
                    definition.kali.profile_id
                ).model_dump(mode="json")
            )
        sandbox_config_digest = self.assignments.kali_profiles.config.digest
        payload = {
            "definition": definition.id,
            "host_capability_profile_id": definition.host_capability_profile_id,
            "host_capabilities": [item.model_dump(mode="json") for item in host_entries],
            "kali": (
                kali_runtime.model_dump(mode="json") if kali_runtime is not None else None
            ),
            "kali_profile": (
                kali_profile.model_dump(mode="json") if kali_profile is not None else None
            ),
            "sandbox_config_digest": sandbox_config_digest,
        }
        return CapabilityBindingSnapshot(
            host_capability_profile_id=definition.host_capability_profile_id,
            host_capability_ids=host_capabilities,
            host_capabilities=host_entries,
            kali=definition.kali,
            kali_runtime=kali_runtime,
            kali_profile=kali_profile,
            sandbox_config_digest=sandbox_config_digest,
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
