"""Serial Supervisor–Worker orchestration for one schema-v6 Task."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Callable

from tga.domain.planning import Intent, IntentDependency
from tga.domain.solver import (
    ReportResult,
    ReviewResult,
    WorkerCoverage,
    WorkerResult,
)
from tga.evidence.database import utc_now
from tga.infrastructure.persistence.errors import PersistenceConflict
from tga.infrastructure.persistence.errors import IntentClaimConflict, PlanVersionConflict
from tga.infrastructure.solver_definitions.registry import SolverDefinitionRegistry
from tga.infrastructure.team_templates.registry import TeamTemplateRegistry
from tga.runtime.orchestration.intent_dispatcher import IntentDispatcher
from tga.runtime.orchestration.result_merger import ResultMerger
from tga.runtime.orchestration.solver_selector import SolverSelector
from tga.runtime.orchestration.team_runtime import TeamRuntime


MAX_SOLVER_RUN_ATTEMPTS = 3


class TaskOrchestrator:
    """The sole task-level owner of team identity, dispatch, merge and completion."""

    def __init__(
        self,
        *,
        task,
        repositories,
        definitions: SolverDefinitionRegistry | None = None,
        templates: TeamTemplateRegistry | None = None,
        runner_lease=None,
    ) -> None:
        self.task = task
        self.repositories = repositories
        self.definitions = definitions or SolverDefinitionRegistry.builtin()
        self.templates = templates or TeamTemplateRegistry.builtin(
            definitions=self.definitions
        )
        self.template = self.templates.require(task.mode)
        self.runner_lease = runner_lease
        self.team = TeamRuntime(
            task=task,
            repositories=repositories,
            definitions=self.definitions,
            template=self.template,
        )
        self.dispatcher = IntentDispatcher()
        self.selector = SolverSelector(
            definitions=self.definitions, template=self.template
        )
        self.merger = ResultMerger(task=task, repositories=repositories)

    def bootstrap(self):
        return self.team.bootstrap()

    def state(self):
        state = self.repositories.orchestration.get_state(self.task.id)
        return state or self.bootstrap()

    def pause(self, *, reason: str = "paused"):
        state = self.state()
        if state.status != "running":
            raise PersistenceConflict(
                f"TaskOrchestrator cannot pause from {state.status}"
            )
        for solver in self.repositories.solvers.list_solvers(self.task.id):
            if str(solver.status) in {"queued", "running", "ready"}:
                self.repositories.solvers.update_solver_status(solver.id, "paused")
        replacement = self._set_state("paused")
        self.repositories.events.append_agent_event(
            self.task.id,
            "TASK_ORCHESTRATOR_PAUSED",
            {"reason": reason},
            solver_id=state.supervisor_solver_id,
        )
        return replacement

    def resume(self):
        state = self.state()
        if state.status not in {"paused", "blocked", "awaiting_input"}:
            raise PersistenceConflict(
                f"TaskOrchestrator cannot resume from {state.status}"
            )
        for solver in self.repositories.solvers.list_solvers(self.task.id):
            if str(solver.status) == "paused":
                target = "queued" if solver.orchestration_role != "supervisor" else "ready"
                self.repositories.solvers.update_solver_status(solver.id, target)
        replacement = self._set_state("running")
        self.repositories.events.append_agent_event(
            self.task.id,
            "TASK_ORCHESTRATOR_RESUMED",
            {},
            solver_id=state.supervisor_solver_id,
        )
        return replacement

    def block(self, *, reason: str):
        state = self.state()
        if state.status in {"completed", "failed", "cancelled"}:
            raise PersistenceConflict(
                f"TaskOrchestrator cannot block from {state.status}"
            )
        for solver in self.repositories.solvers.list_solvers(self.task.id):
            if str(solver.status) in {"queued", "running", "ready"}:
                self.repositories.solvers.update_solver_status(solver.id, "paused")
        replacement = self._set_state("blocked")
        self.repositories.events.append_agent_event(
            self.task.id,
            "TASK_ORCHESTRATOR_BLOCKED",
            {"reason": reason},
            solver_id=state.supervisor_solver_id,
        )
        return replacement

    def cancel(self, *, reason: str = "cancelled"):
        """Cancel all nonterminal team work and make the task runtime terminal."""
        state = self.state()
        if state.status == "cancelled":
            return state
        if state.status in {"completed", "failed"}:
            raise PersistenceConflict(
                f"TaskOrchestrator cannot cancel from {state.status}"
            )
        now = utc_now()
        with self.repositories.transaction():
            plan = self.repositories.plans.get_global_plan(self.task.id)
            if plan is not None:
                terminal_intents = {"completed", "failed", "cancelled"}
                replacement_plan = plan.model_copy(update={
                    "version": plan.version + 1,
                    "status": "abandoned",
                    "updated_at": now,
                    "intents": [
                        item if item.status in terminal_intents else item.model_copy(
                            update={"status": "cancelled", "updated_at": now}
                        )
                        for item in plan.intents
                    ],
                })
                self.repositories.plans.compare_and_swap_global_plan(
                    replacement_plan,
                    expected_version=plan.version,
                    preserve_intent_lifecycle=False,
                )
                self.repositories.events.append_agent_event(
                    self.task.id,
                    "PLAN_UPDATED",
                    {
                        "operation": "task_cancelled",
                        "old_version": plan.version,
                        "new_version": plan.version + 1,
                    },
                    solver_id=state.supervisor_solver_id,
                )
            for assignment in self.repositories.orchestration.list_assignments(
                self.task.id
            ):
                if assignment.status in {"proposed", "accepted"}:
                    self.repositories.orchestration.cancel_assignment(
                        assignment.id, finished_at=now
                    )
            for solver in self.repositories.solvers.list_solvers(self.task.id):
                if str(solver.status) not in {"completed", "failed", "cancelled"}:
                    self.repositories.solvers.update_solver_status(
                        solver.id, "cancelled"
                    )
            cancelled_runs = self.repositories.orchestration.cancel_task_solver_runs(
                self.task.id
            )
            for run in cancelled_runs:
                self.repositories.events.append_agent_event(
                    self.task.id,
                    "SOLVER_RUN_CANCELLED",
                    {"run_id": run.id, "reason": reason},
                    solver_id=run.solver_id,
                    intent_id=run.intent_id,
                )
            self.repositories.solvers.revoke_task_leases(self.task.id)
            replacement = self._set_state("cancelled")
            self.repositories.events.append_agent_event(
                self.task.id,
                "TASK_ORCHESTRATOR_CANCELLED",
                {"reason": reason},
                solver_id=state.supervisor_solver_id,
            )
        return replacement

    def create_intent(
        self,
        *,
        supervisor_solver_id: str,
        kind: str,
        title: str,
        objective: str,
        dependencies: tuple[str, ...] = (),
        allowed_resource_ids: tuple[str, ...] = (),
        relevant_knowledge_ids: tuple[str, ...] = (),
        relevant_evidence_claim_ids: tuple[str, ...] = (),
        priority: int = 0,
    ) -> Intent:
        self._require_role(supervisor_solver_id, "supervisor", label="Supervisor")
        now = utc_now()
        digest = hashlib.sha256(
            f"{self.task.id}\0{kind}\0{title}\0{objective}".encode()
        ).hexdigest()[:20]
        intent = Intent(
            id=f"intent_{digest}",
            task_id=self.task.id,
            kind=kind,
            title=title,
            objective=objective,
            dependencies=[IntentDependency(intent_id=item) for item in dependencies],
            status="pending",
            budget={"turns": 16},
            priority=priority,
            created_at=now,
            updated_at=now,
            provenance={
                "created_by_solver_id": supervisor_solver_id,
                "allowed_resource_ids": list(allowed_resource_ids),
                "relevant_knowledge_ids": list(relevant_knowledge_ids),
                "relevant_evidence_claim_ids": list(relevant_evidence_claim_ids),
            },
        )
        old_version = 0
        for attempt in range(4):
            plan = self.repositories.plans.get_global_plan(self.task.id)
            if plan is None:
                raise RuntimeError("GlobalPlan is missing")
            duplicate = next((
                item for item in plan.intents
                if item.id == intent.id or (
                    item.kind == kind
                    and item.title == title
                    and item.objective == objective
                )
            ), None)
            if duplicate is not None:
                return duplicate
            maximum = min(
                1_024,
                int(self.task.execution_budget.get("max_total_intents", 128)),
            )
            if len(plan.intents) >= maximum:
                raise PersistenceConflict("Task max_total_intents limit exceeded")
            known = {item.id for item in plan.intents}
            if not set(dependencies).issubset(known):
                raise ValueError("Intent dependency is not present in GlobalPlan")
            replacement = plan.model_copy(update={
                "version": plan.version + 1,
                "updated_at": utc_now(),
                "intents": [*plan.intents, intent],
            })
            try:
                self.repositories.plans.compare_and_swap_global_plan(
                    replacement, expected_version=plan.version
                )
                old_version = plan.version
                break
            except PlanVersionConflict:
                if attempt == 3:
                    raise
        self.repositories.events.append_agent_event(
            self.task.id,
            "PLAN_UPDATED",
            {
                "operation": "intent_added",
                "intent_id": intent.id,
                "old_version": old_version,
                "new_version": old_version + 1,
            },
            solver_id=supervisor_solver_id,
        )
        self.repositories.events.append_agent_event(
            self.task.id,
            "INTENT_CREATED",
            {"intent_id": intent.id, "kind": kind, "priority": priority},
            solver_id=supervisor_solver_id,
        )
        return intent

    def dispatch_next(self):
        assignments = self.dispatch_ready(limit=1)
        return assignments[0] if assignments else None

    def dispatch_ready(self, *, limit: int | None = None) -> tuple:
        self.recover()
        state = self.state()
        if state.status not in {"running", "awaiting_input"}:
            return ()
        solvers = self.repositories.solvers.list_solvers(self.task.id)
        available = state.max_active_workers - self.dispatcher.active_worker_count(solvers)
        if limit is not None:
            available = min(available, max(0, limit))
        if available <= 0:
            return ()
        plan = self.repositories.plans.get_global_plan(self.task.id)
        runnable = self.dispatcher.runnable(plan) if plan else []
        if not runnable:
            return ()
        if state.status == "awaiting_input":
            self._set_state("running")
        assignments = []
        for intent in runnable[:available]:
            definition = self.selector.select(intent)
            try:
                assignments.append(
                    self.team.create_worker(intent=intent, definition=definition)
                )
            except IntentClaimConflict:
                # A different orchestrator process won the atomic claim.
                continue
        return tuple(assignments)

    def submit_worker_result(self, result: WorkerResult, *, lease=None) -> str:
        self._require_role(result.solver_id, "worker", label="Worker")
        if self.state().status in {"cancelled", "completed", "failed"}:
            raise PersistenceConflict(
                f"worker result rejected after task {self.state().status}"
            )
        if lease is not None and not self.repositories.solvers.validate_lease(lease):
            raise PersistenceConflict("Solver runner lease is no longer valid")
        result_id = self.repositories.solvers.save_worker_result(result)
        self.repositories.events.append_agent_event(
            self.task.id,
            "WORKER_RESULT_SUBMITTED",
            {
                "worker_result_id": result_id,
                "intent_id": result.intent_id,
                "status": str(result.status),
            },
            solver_id=result.solver_id,
            intent_id=result.intent_id,
        )
        return self.merger.merge(result_id, result)

    def retry_intent(self, *, supervisor_solver_id: str, intent_id: str):
        self._require_role(supervisor_solver_id, "supervisor", label="Supervisor")
        plan = self.repositories.plans.get_global_plan(self.task.id)
        if plan is None:
            raise RuntimeError("GlobalPlan is missing")
        old_version = plan.version
        intent = self.repositories.plans.reset_intent_for_retry(intent_id)
        attempts = [
            item.attempt
            for item in self.repositories.orchestration.list_assignments(self.task.id)
            if item.intent_id == intent_id
        ]
        attempt = max(attempts, default=0) + 1
        assignment = self.team.create_worker(
            intent=intent,
            definition=self.selector.select(intent),
            attempt=attempt,
        )
        self.repositories.events.append_agent_event(
            self.task.id,
            "PLAN_UPDATED",
            {
                "operation": "intent_retry",
                "intent_id": intent_id,
                "attempt": attempt,
                "old_version": old_version,
                "new_version": old_version + 1,
            },
            solver_id=supervisor_solver_id,
        )
        return assignment

    def recover(self):
        state = self.state()
        expired_runs = self.repositories.orchestration.expire_solver_runs()
        for run in expired_runs:
            self.repositories.events.append_agent_event(
                self.task.id,
                "SOLVER_RUN_EXPIRED",
                {
                    "run_id": run.id,
                    "attempt": run.attempt,
                    "error_code": run.error_code,
                },
                solver_id=run.solver_id,
                intent_id=run.intent_id,
            )
            solver = self.repositories.solvers.get_solver(run.solver_id)
            if solver is not None and str(solver.status) not in {
                "completed", "failed", "cancelled",
            }:
                self.repositories.solvers.update_solver_status(run.solver_id, "failed")
            if run.assignment_id:
                assignment = self.repositories.orchestration.get_assignment(
                    run.assignment_id
                )
                if assignment is not None and assignment.status in {"proposed", "accepted"}:
                    self.repositories.orchestration.cancel_assignment(
                        assignment.id, finished_at=run.finished_at or utc_now()
                    )
            if run.intent_id:
                plan = self.repositories.plans.get_global_plan(self.task.id)
                intent = next(
                    (item for item in plan.intents if item.id == run.intent_id),
                    None,
                ) if plan else None
                if intent is not None and intent.status not in {
                    "completed", "failed", "cancelled",
                }:
                    self.repositories.plans.update_intent_status(
                        intent.id, "failed", expected_status=intent.status
                    )
                if (
                    state.status == "running"
                    and run.attempt < MAX_SOLVER_RUN_ATTEMPTS
                    and state.supervisor_solver_id
                ):
                    self.retry_intent(
                        supervisor_solver_id=state.supervisor_solver_id,
                        intent_id=run.intent_id,
                    )
        for result_id, result in self.repositories.solvers.list_worker_result_records(
            self.task.id
        ):
            if not self.repositories.orchestration.is_worker_result_merged(result_id):
                self.merger.merge(result_id, result)
        return self.state()

    def request_review(self, *, supervisor_solver_id: str):
        self._require_role(supervisor_solver_id, "supervisor", label="Supervisor")
        return self.team.ensure_role_solver("reviewer")

    def record_review(self, result: ReviewResult) -> str:
        self._require_role(result.solver_id, "reviewer", label="Reviewer")
        known_results = {
            result_id
            for result_id, _ in self.repositories.solvers.list_worker_result_records(
                self.task.id
            )
        }
        if not set(result.worker_result_ids).issubset(known_results):
            raise ValueError("ReviewResult references an unknown WorkerResult")
        now = utc_now()
        with self.repositories.transaction():
            for claim_id in result.confirmed_evidence_claim_ids:
                self.repositories.evidence.review_evidence_claim(
                    claim_id,
                    status="confirmed",
                    reviewer_solver_id=result.solver_id,
                    reviewed_at=now,
                )
                self.repositories.events.append_agent_event(
                    self.task.id,
                    "EVIDENCE_CLAIM_REVIEWED",
                    {"evidence_claim_id": claim_id, "status": "confirmed"},
                    solver_id=result.solver_id,
                )
            for knowledge_id in result.confirmed_knowledge_ids:
                self.repositories.knowledge.review_knowledge(
                    knowledge_id,
                    status="verified",
                    reviewer_solver_id=result.solver_id,
                    reviewed_at=now,
                )
                self.repositories.events.append_agent_event(
                    self.task.id,
                    "KNOWLEDGE_PROMOTED",
                    {
                        "knowledge_id": knowledge_id,
                        "status": "verified",
                        "reviewer_solver_id": result.solver_id,
                    },
                    solver_id=result.solver_id,
                )
            for finding_id in result.confirmed_finding_ids:
                finding = self.repositories.evidence.get_finding(finding_id)
                if finding is None:
                    raise KeyError(f"finding not found: {finding_id}")
                confirmed = type(finding).model_validate({
                    **finding.model_dump(mode="json"),
                    "status": "confirmed",
                    "reviewed_at": now,
                    "provenance": {
                        **finding.provenance,
                        "reviewed_by_solver_id": result.solver_id,
                    },
                })
                self.repositories.evidence.save_finding(confirmed)
            for rejected_id in result.rejected_ids:
                claim = self.repositories.evidence.get_evidence_claim(rejected_id)
                if claim is not None:
                    self.repositories.evidence.review_evidence_claim(
                        rejected_id,
                        status="rejected",
                        reviewer_solver_id=result.solver_id,
                        reviewed_at=now,
                    )
                    self.repositories.events.append_agent_event(
                        self.task.id,
                        "EVIDENCE_CLAIM_REVIEWED",
                        {"evidence_claim_id": rejected_id, "status": "rejected"},
                        solver_id=result.solver_id,
                    )
                    continue
                knowledge = self.repositories.knowledge.get_knowledge(rejected_id)
                if knowledge is not None:
                    self.repositories.knowledge.review_knowledge(
                        rejected_id,
                        status="rejected",
                        reviewer_solver_id=result.solver_id,
                        reviewed_at=now,
                    )
                    continue
                finding = self.repositories.evidence.get_finding(rejected_id)
                if finding is None:
                    raise KeyError(f"review target not found: {rejected_id}")
                self.repositories.evidence.save_finding(finding.model_copy(update={
                    "status": "rejected",
                    "reviewed_at": now,
                    "provenance": {
                        **finding.provenance,
                        "reviewed_by_solver_id": result.solver_id,
                    },
                }))
        result_id = self.repositories.orchestration.save_review_result(result)
        solver = self.repositories.solvers.get_solver(result.solver_id)
        if solver is not None and str(solver.status) != "completed":
            self.repositories.solvers.update_solver_status(result.solver_id, "completed")
        state = self.state()
        self.repositories.orchestration.save_state(state.model_copy(update={
            "review_result_ids": tuple(dict.fromkeys((*state.review_result_ids, result_id))),
            "updated_at": now,
        }))
        self.repositories.events.append_agent_event(
            self.task.id,
            "REVIEW_RESULT_RECORDED",
            {
                "review_result_id": result_id,
                "status": result.status,
                "contradictions": list(result.contradictions),
            },
            solver_id=result.solver_id,
        )
        return result_id

    def request_report(self, *, supervisor_solver_id: str):
        self._require_role(supervisor_solver_id, "supervisor", label="Supervisor")
        return self.team.ensure_role_solver("reporter")

    def record_report(self, result: ReportResult) -> str:
        self._require_role(result.solver_id, "reporter", label="Reporter")
        state = self.state()
        if not set(result.review_result_ids).issubset(state.review_result_ids):
            raise ValueError("ReportResult references an unknown ReviewResult")
        result_id = self.repositories.orchestration.save_report_result(result)
        solver = self.repositories.solvers.get_solver(result.solver_id)
        target_status = "completed" if result.status == "completed" else str(result.status)
        if solver is not None and str(solver.status) != target_status:
            self.repositories.solvers.update_solver_status(result.solver_id, target_status)
        self.repositories.orchestration.save_state(state.model_copy(update={
            "report_result_ids": tuple(dict.fromkeys((*state.report_result_ids, result_id))),
            "updated_at": utc_now(),
        }))
        self.repositories.events.append_agent_event(
            self.task.id,
            "REPORT_RESULT_RECORDED",
            {"report_result_id": result_id, "status": result.status},
            solver_id=result.solver_id,
        )
        return result_id

    def confirm_finding(self, *, solver_id: str, finding_id: str) -> dict[str, Any]:
        solver = self.repositories.solvers.get_solver(solver_id)
        if solver is None or solver.orchestration_role == "reporter":
            raise PermissionError("Reporter cannot confirm Finding")
        self._require_role(solver_id, "supervisor", label="Supervisor")
        finding = self.repositories.evidence.get_finding(finding_id)
        if finding is None:
            return {"accepted": False, "reason": "finding_not_found"}
        return {"accepted": False, "reason": "review_service_required"}

    def propose_task_completion(
        self,
        *,
        solver_id: str,
        proposal: dict[str, Any],
        validator: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> dict[str, Any]:
        self._require_role(solver_id, "supervisor", label="Supervisor")
        plan = self.repositories.plans.get_global_plan(self.task.id)
        active = [
            item.id for item in (plan.intents if plan else [])
            if item.status in {"pending", "ready", "assigned", "running", "reviewing"}
        ]
        if active:
            return {
                "accepted": False,
                "code": "ACTIVE_INTENTS_REMAIN",
                "active_intent_ids": active,
            }
        result = validator(proposal)
        accepted = bool(result.get("accepted"))
        now = utc_now()
        proposal_id = "completion_" + hashlib.sha256(
            json.dumps(proposal, ensure_ascii=False, sort_keys=True, default=str).encode()
        ).hexdigest()[:24]
        if accepted and plan is not None and plan.status != "completed":
            self.repositories.plans.compare_and_swap_global_plan(
                plan.model_copy(update={
                    "version": plan.version + 1,
                    "status": "completed",
                    "updated_at": now,
                }),
                expected_version=plan.version,
            )
            self.repositories.events.append_agent_event(
                self.task.id,
                "PLAN_UPDATED",
                {
                    "operation": "plan_completed",
                    "old_version": plan.version,
                    "new_version": plan.version + 1,
                },
                solver_id=solver_id,
            )
        if accepted:
            supervisor = self.repositories.solvers.get_solver(solver_id)
            if supervisor is not None and str(supervisor.status) != "completed":
                self.repositories.solvers.update_solver_status(solver_id, "completed")
        state = self.state()
        replacement = state.model_copy(update={
            "status": "completed" if accepted else state.status,
            "completion_proposal": dict(proposal),
            "updated_at": now,
        })
        self.repositories.orchestration.save_state(replacement)
        self.repositories.events.append_agent_event(
            self.task.id,
            "TASK_COMPLETION_PROPOSED",
            {
                "proposal_id": proposal_id,
                "accepted": accepted,
                "validation": result,
            },
            solver_id=solver_id,
        )
        if accepted:
            self.repositories.events.append_agent_event(
                self.task.id,
                "TASK_COMPLETION_ACCEPTED",
                {"proposal_id": proposal_id, "validation": result},
                solver_id=solver_id,
            )
        return result

    def run_serial(
        self,
        *,
        worker_runtime: Callable[[Any], WorkerResult],
        reviewer_runtime: Callable[[Any, tuple[str, ...]], ReviewResult] | None = None,
        reporter_runtime: Callable[[Any, tuple[str, ...]], ReportResult] | None = None,
        completion_proposal: dict[str, Any] | None = None,
        completion_validator: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ):
        state = self.bootstrap()
        self.recover()
        while True:
            assignment = self.dispatch_next()
            if assignment is None:
                break
            self.repositories.solvers.update_solver_status(
                assignment.solver_id, "running"
            )
            result = worker_runtime(assignment)
            self.submit_worker_result(result)
        state = self.state()
        worker_ids = state.merged_worker_result_ids
        if reviewer_runtime is not None and worker_ids:
            reviewer = self.request_review(
                supervisor_solver_id=state.supervisor_solver_id or ""
            )
            self.repositories.solvers.update_solver_status(reviewer.id, "running")
            try:
                self.record_review(reviewer_runtime(reviewer, worker_ids))
            except Exception:
                self.repositories.solvers.update_solver_status(reviewer.id, "failed")
                raise
        state = self.state()
        if reporter_runtime is not None and state.review_result_ids:
            reporter = self.request_report(
                supervisor_solver_id=state.supervisor_solver_id or ""
            )
            self.repositories.solvers.update_solver_status(reporter.id, "running")
            try:
                self.record_report(reporter_runtime(reporter, state.review_result_ids))
            except Exception:
                self.repositories.solvers.update_solver_status(reporter.id, "failed")
                raise
        if completion_proposal is not None and completion_validator is not None:
            self.propose_task_completion(
                solver_id=self.state().supervisor_solver_id or "",
                proposal=completion_proposal,
                validator=completion_validator,
            )
        final = self.state()
        plan = self.repositories.plans.get_global_plan(self.task.id)
        if (
            final.status == "running"
            and plan is not None
            and not self.dispatcher.runnable(plan)
            and any(item.status in {"blocked", "failed"} for item in plan.intents)
        ):
            final = self._set_state("awaiting_input")
        return final

    def gateway_control_handlers(self, solver_id: str) -> dict[str, Callable]:
        """Role-scoped control handlers called only by ToolGovernanceGateway."""
        solver = self.repositories.solvers.get_solver(solver_id)
        if solver is None or solver.task_id != self.task.id:
            raise KeyError(f"solver not found for control tools: {solver_id}")
        common = {"inspect_task_state": lambda _args: self._inspect_task_state()}
        if solver.orchestration_role == "supervisor":
            return {
                **common,
                "create_intent": lambda args: self._gateway_create_intent(solver_id, args),
                "update_global_plan": lambda args: self._gateway_update_global_plan(solver_id, args),
                "spawn_solver": lambda _args: self._gateway_spawn_solver(),
                "inspect_worker_result": lambda args: self._gateway_inspect_worker_result(args),
                "request_review": lambda _args: self._gateway_request_role(solver_id, "reviewer"),
                "request_report": lambda _args: self._gateway_request_role(solver_id, "reporter"),
                "confirm_finding": lambda args: self._gateway_confirm_finding(
                    solver_id, args
                ),
            }
        if solver.orchestration_role == "worker":
            return {
                "update_local_plan": lambda args: self._gateway_update_local_plan(solver_id, args),
                "propose_knowledge": lambda args: self._gateway_propose_knowledge(solver_id, args),
                "submit_worker_result": lambda args: self._gateway_submit_worker_result(solver_id, args),
            }
        if solver.orchestration_role == "reviewer":
            return {
                "review_evidence": lambda args: self._gateway_record_review(solver_id, args),
                "review_finding": lambda args: self._gateway_record_review(solver_id, args),
                "request_more_evidence": lambda args: self._gateway_record_review(
                    solver_id, {**args, "status": "needs_more_evidence"}
                ),
            }
        return {
            "report.write": lambda args: self._gateway_record_report(solver_id, args),
        }

    def gateway_resource_handlers(self, solver_id: str) -> dict[str, Callable]:
        solver = self.repositories.solvers.get_solver(solver_id)
        if solver is None or solver.task_id != self.task.id:
            raise KeyError(f"solver not found for resource tools: {solver_id}")
        if solver.orchestration_role == "reviewer":
            return {
                "evidence.inspect": lambda args: self._read_evidence(args, confirmed_only=False),
                "knowledge.inspect": lambda args: self._read_knowledge(args, confirmed_only=False),
            }
        if solver.orchestration_role == "reporter":
            return {
                "confirmed_evidence.read": lambda args: self._read_evidence(args, confirmed_only=True),
                "confirmed_knowledge.read": lambda args: self._read_knowledge(args, confirmed_only=True),
                "confirmed_findings.read": lambda args: self._read_findings(args),
            }
        return {}

    def _inspect_task_state(self) -> dict[str, Any]:
        plan = self.repositories.plans.get_global_plan(self.task.id)
        state = self.state()
        return {
            "ok": True,
            "summary": "Current durable orchestration state.",
            "orchestrator": state.model_dump(mode="json"),
            "intents": [item.model_dump(mode="json") for item in (plan.intents if plan else [])],
            "solvers": [
                {
                    "id": item.id,
                    "definition_id": item.definition_id,
                    "role": item.orchestration_role,
                    "status": str(item.status),
                    "assigned_intent_id": item.assigned_intent_id,
                }
                for item in self.repositories.solvers.list_solvers(self.task.id)
            ],
        }

    def _read_evidence(self, args: dict[str, Any], *, confirmed_only: bool) -> dict[str, Any]:
        requested = str(args.get("evidence_claim_id") or "")
        values = [
            item for item in self.repositories.evidence.list_evidence_claims(self.task.id)
            if (not requested or item.id == requested)
            and (not confirmed_only or item.status == "confirmed")
        ]
        return {
            "ok": True,
            "evidence_claims": [item.model_dump(mode="json") for item in values[:256]],
        }

    def _read_knowledge(self, args: dict[str, Any], *, confirmed_only: bool) -> dict[str, Any]:
        requested = str(args.get("knowledge_id") or "")
        values = [
            item for item in self.repositories.knowledge.list_knowledge(self.task.id)
            if (not requested or item.id == requested)
            and (not confirmed_only or item.status == "verified")
        ]
        return {
            "ok": True,
            "knowledge": [item.model_dump(mode="json") for item in values[:256]],
        }

    def _read_findings(self, args: dict[str, Any]) -> dict[str, Any]:
        requested = str(args.get("finding_id") or "")
        values = [
            item for item in self.repositories.evidence.list_findings(self.task.id)
            if item.status == "confirmed" and (not requested or item.id == requested)
        ]
        return {
            "ok": True,
            "findings": [item.model_dump(mode="json") for item in values[:256]],
        }

    def _gateway_create_intent(self, solver_id: str, args: dict[str, Any]) -> dict[str, Any]:
        intent = self.create_intent(
            supervisor_solver_id=solver_id,
            kind=str(args["kind"]),
            title=str(args["title"]),
            objective=str(args["objective"]),
            allowed_resource_ids=tuple(args.get("allowed_resource_ids") or ()),
            relevant_knowledge_ids=tuple(args.get("relevant_knowledge_ids") or ()),
            relevant_evidence_claim_ids=tuple(args.get("relevant_evidence_claim_ids") or ()),
            priority=int(args.get("priority") or 0),
        )
        return {"ok": True, "intent": intent.model_dump(mode="json")}

    def _gateway_update_global_plan(self, solver_id: str, args: dict[str, Any]) -> dict[str, Any]:
        action = str(args.get("action") or "")
        if action != "create_intent":
            return {
                "ok": False,
                "status": "blocked",
                "error": {
                    "code": "GLOBAL_PLAN_OPERATION_UNSUPPORTED",
                    "message": "Use create_intent or an explicit supported GlobalPlan operation.",
                    "retryable": False,
                },
            }
        return self._gateway_create_intent(solver_id, args)

    def _gateway_spawn_solver(self) -> dict[str, Any]:
        assignment = self.dispatch_next()
        return {
            "ok": assignment is not None,
            "status": "queued" if assignment else "blocked",
            "terminal": assignment is not None,
            "assignment": assignment.model_dump(mode="json") if assignment else None,
        }

    def _gateway_inspect_worker_result(self, args: dict[str, Any]) -> dict[str, Any]:
        requested = str(args.get("worker_result_id") or "")
        records = self.repositories.solvers.list_worker_result_records(self.task.id)
        values = [
            {"id": result_id, **result.model_dump(mode="json")}
            for result_id, result in records
            if not requested or requested == result_id
        ]
        return {"ok": True, "worker_results": values}

    def _gateway_request_role(self, supervisor_id: str, role: str) -> dict[str, Any]:
        solver = (
            self.request_review(supervisor_solver_id=supervisor_id)
            if role == "reviewer"
            else self.request_report(supervisor_solver_id=supervisor_id)
        )
        return {
            "ok": True, "terminal": True,
            "solver_id": solver.id, "status": str(solver.status),
        }

    def _gateway_confirm_finding(self, solver_id: str, args: dict[str, Any]) -> dict[str, Any]:
        result = self.confirm_finding(
            solver_id=solver_id, finding_id=str(args.get("finding_id") or "")
        )
        return {"ok": bool(result.get("accepted")), **result}

    def _gateway_update_local_plan(self, solver_id: str, args: dict[str, Any]) -> dict[str, Any]:
        solver = self._require_role(solver_id, "worker", label="Worker")
        local = self.repositories.plans.get_local_plan(
            solver_id, solver.assigned_intent_id or ""
        )
        if local is None:
            return {"ok": False, "status": "blocked", "reason": "local_plan_missing"}
        self.repositories.events.append_agent_event(
            self.task.id,
            "LOCAL_PLAN_UPDATE_PROPOSED",
            {"local_plan_id": local.id, "proposal": dict(args)},
            solver_id=solver_id,
        )
        return {"ok": True, "local_plan_id": local.id, "proposal_recorded": True}

    def _gateway_propose_knowledge(self, solver_id: str, args: dict[str, Any]) -> dict[str, Any]:
        self._require_role(solver_id, "worker", label="Worker")
        self.repositories.events.append_agent_event(
            self.task.id,
            "KNOWLEDGE_PROPOSED",
            {"proposal": dict(args), "status": "candidate"},
            solver_id=solver_id,
        )
        return {"ok": True, "status": "candidate", "promoted": False}

    def _gateway_submit_worker_result(self, solver_id: str, args: dict[str, Any]) -> dict[str, Any]:
        solver = self._require_role(solver_id, "worker", label="Worker")
        intent_id = solver.assigned_intent_id or ""
        coverage = args.get("coverage") or {}
        result = WorkerResult(
            task_id=self.task.id,
            solver_id=solver_id,
            intent_id=intent_id,
            status=args["status"],
            summary=str(args["summary"]),
            artifact_ids=tuple(args.get("artifact_ids") or ()),
            candidate_evidence_claim_ids=tuple(
                args.get("candidate_evidence_claim_ids") or ()
            ),
            candidate_knowledge_ids=tuple(args.get("candidate_knowledge_ids") or ()),
            finding_ids=tuple(args.get("finding_ids") or ()),
            coverage=WorkerCoverage(
                completed=tuple(coverage.get("completed") or ()),
                not_covered=tuple(coverage.get("not_covered") or ()),
            ),
            limitations=tuple(args.get("limitations") or ()),
        )
        result_id = self.submit_worker_result(result, lease=self.runner_lease)
        return {
            "ok": True,
            "terminal": True,
            "status": "completed",
            "summary": result.summary,
            "worker_result_id": result_id,
        }

    def _gateway_record_review(self, solver_id: str, args: dict[str, Any]) -> dict[str, Any]:
        state = self.state()
        result_id = self.record_review(ReviewResult(
            task_id=self.task.id,
            solver_id=solver_id,
            worker_result_ids=state.merged_worker_result_ids,
            status=args.get("status") or "needs_more_evidence",
            confirmed_evidence_claim_ids=tuple(args.get("confirmed_evidence_claim_ids") or ()),
            confirmed_knowledge_ids=tuple(args.get("confirmed_knowledge_ids") or ()),
            confirmed_finding_ids=tuple(args.get("confirmed_finding_ids") or ()),
            rejected_ids=tuple(args.get("rejected_ids") or ()),
            contradictions=tuple(args.get("contradictions") or ()),
        ))
        return {
            "ok": True, "terminal": True, "status": "completed",
            "review_result_id": result_id,
        }

    def _gateway_record_report(self, solver_id: str, args: dict[str, Any]) -> dict[str, Any]:
        state = self.state()
        result_id = self.record_report(ReportResult(
            task_id=self.task.id,
            solver_id=solver_id,
            review_result_ids=state.review_result_ids,
            status="completed",
            summary=str(args["summary"]),
            report_artifact_id=args.get("report_artifact_id"),
            limitations=tuple(args.get("limitations") or ()),
        ))
        return {
            "ok": True, "terminal": True, "status": "completed",
            "report_result_id": result_id,
        }

    def _require_role(self, solver_id: str, role: str, *, label: str):
        solver = self.repositories.solvers.get_solver(solver_id)
        if solver is None or solver.task_id != self.task.id or solver.orchestration_role != role:
            raise PermissionError(f"{label} authority is required")
        return solver

    def _set_state(self, status: str):
        state = self.state()
        replacement = state.model_copy(update={"status": status, "updated_at": utc_now()})
        return self.repositories.orchestration.save_state(replacement)


__all__ = ["TaskOrchestrator"]
