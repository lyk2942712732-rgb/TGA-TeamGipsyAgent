"""Idempotently merge structured WorkerResult into orchestration state."""

from __future__ import annotations

from tga.evidence.database import utc_now
from tga.infrastructure.persistence.errors import PersistenceConflict


class ResultMerger:
    def __init__(self, *, task, repositories) -> None:
        self.task = task
        self.repositories = repositories

    def merge(self, result_id: str, result) -> str:
        with self.repositories.transaction():
            return self._merge_locked(result_id, result)

    def _merge_locked(self, result_id: str, result) -> str:
        if self.repositories.orchestration.is_worker_result_merged(result_id):
            return result_id
        state = self.repositories.orchestration.get_state(self.task.id)
        if state is None or not state.supervisor_solver_id:
            raise RuntimeError("TaskOrchestrator must be bootstrapped before merging results")
        if state.status in {"completed", "failed", "cancelled"}:
            raise PersistenceConflict(
                f"WorkerResult cannot merge after task {state.status}"
            )
        assignment = self.repositories.orchestration.get_assignment_for_solver(
            result.solver_id
        )
        if assignment is None:
            raise ValueError("WorkerResult has no durable SolverAssignment")
        if (
            result.task_id != self.task.id
            or result.intent_id != assignment.intent_id
            or result.solver_id != assignment.solver_id
        ):
            raise ValueError("WorkerResult ownership does not match SolverAssignment")
        solver = self.repositories.solvers.get_solver(result.solver_id)
        if solver is None or solver.orchestration_role != "worker":
            raise PermissionError("only a Worker Solver may submit WorkerResult")
        for artifact_id in result.artifact_ids:
            artifact = self.repositories.evidence.get_artifact(artifact_id)
            if artifact is None or artifact.task_id != self.task.id:
                raise ValueError(f"WorkerResult Artifact is not task-owned: {artifact_id}")
        for claim_id in result.candidate_evidence_claim_ids:
            claim = self.repositories.evidence.get_evidence_claim(claim_id)
            if claim is None or claim.task_id != self.task.id or claim.status != "candidate":
                raise ValueError(f"WorkerResult EvidenceClaim is not a task candidate: {claim_id}")
        knowledge = {
            item.id: item for item in self.repositories.knowledge.list_knowledge(self.task.id)
        }
        for knowledge_id in result.candidate_knowledge_ids:
            item = knowledge.get(knowledge_id)
            if item is None or item.status != "candidate":
                raise ValueError(f"WorkerResult Knowledge is not a task candidate: {knowledge_id}")
        findings = {
            item.id: item for item in self.repositories.evidence.list_findings(self.task.id)
        }
        for finding_id in result.finding_ids:
            item = findings.get(finding_id)
            if item is None or item.status != "candidate":
                raise ValueError(f"WorkerResult Finding is not a task candidate: {finding_id}")

        intent_status = {
            "succeeded": "completed",
            "partial": "completed",
            "blocked": "blocked",
            "failed": "failed",
            "cancelled": "cancelled",
        }[str(result.status)]
        solver_status = {
            "succeeded": "completed",
            "partial": "completed",
            "blocked": "blocked",
            "failed": "failed",
            "cancelled": "cancelled",
        }[str(result.status)]
        current_plan = self.repositories.plans.get_global_plan(self.task.id)
        current_intent = next(
            item for item in current_plan.intents if item.id == result.intent_id
        )
        if current_intent.status != intent_status:
            self.repositories.plans.update_intent_status(
                result.intent_id,
                intent_status,
                expected_status=current_intent.status,
            )
            self.repositories.events.append_agent_event(
                self.task.id,
                "PLAN_UPDATED",
                {
                    "operation": "intent_status_changed",
                    "intent_id": result.intent_id,
                    "old_version": current_plan.version,
                    "new_version": current_plan.version + 1,
                },
                solver_id=state.supervisor_solver_id,
                intent_id=result.intent_id,
            )
        current_solver = self.repositories.solvers.get_solver(result.solver_id)
        if str(current_solver.status) != solver_status:
            self.repositories.solvers.update_solver_status(result.solver_id, solver_status)
        now = utc_now()
        self.repositories.orchestration.complete_assignment(
            assignment.id, finished_at=now
        )
        self.repositories.orchestration.mark_worker_result_merged(
            result_id,
            task_id=self.task.id,
            intent_id=result.intent_id,
            supervisor_solver_id=state.supervisor_solver_id,
            merged_at=now,
        )
        updated = state.model_copy(update={
            "merged_worker_result_ids": tuple(dict.fromkeys((
                *state.merged_worker_result_ids, result_id,
            ))),
            "updated_at": now,
        })
        self.repositories.orchestration.save_state(updated)
        self.repositories.events.append_agent_event(
            self.task.id,
            "WORKER_RESULT_MERGED",
            {
                "worker_result_id": result_id,
                "intent_id": result.intent_id,
                "worker_status": str(result.status),
                "intent_status": intent_status,
                "candidate_knowledge_ids": list(result.candidate_knowledge_ids),
                "candidate_evidence_claim_ids": list(result.candidate_evidence_claim_ids),
            },
            solver_id=state.supervisor_solver_id,
            intent_id=result.intent_id,
        )
        self.repositories.events.append_agent_event(
            self.task.id,
            "INTENT_COMPLETED",
            {"intent_id": result.intent_id, "status": intent_status},
            solver_id=result.solver_id,
            intent_id=result.intent_id,
        )
        solver_event = {
            "completed": "SOLVER_COMPLETED",
            "failed": "SOLVER_FAILED",
            "blocked": "SOLVER_FAILED",
            "cancelled": "SOLVER_FAILED",
        }[solver_status]
        self.repositories.events.append_agent_event(
            self.task.id,
            solver_event,
            {"solver_id": result.solver_id, "status": solver_status},
            solver_id=result.solver_id,
            intent_id=result.intent_id,
        )
        return result_id


__all__ = ["ResultMerger"]
