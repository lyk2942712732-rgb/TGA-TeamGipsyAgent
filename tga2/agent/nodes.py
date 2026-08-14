"""Business-aware LangGraph nodes and deterministic Runtime policy guards."""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from langgraph.types import interrupt

from tga2.agent.graph import RuntimeDeps, TGAState
from tga2.agent.middleware import SAFE_DEFAULT_TOOLS, worker_middleware
from tga2.agent.schemas import (
    EvidencePacket,
    ReviewDraft,
    ReviewPacket,
    SituationPacket,
    SupervisorDecision,
    WorkerDraft,
)
from tga2.agent.tools import ToolRegistry
from tga2.core.models import (
    AgentEvent,
    EvidenceClaim,
    EvidenceLocator,
    Finding,
    Intent,
    IntentStatus,
    Plan,
    SolverRole,
    SolverRun,
    TaskStatus,
    utc_now,
)
from tga2.core.report import render_markdown


class TaskCancelledError(RuntimeError):
    """Raised at graph node boundaries after an operator cancels a task."""


class BudgetExceededError(RuntimeError):
    """Raised before work starts when a hard task budget is exhausted."""


class GraphNodes:
    def __init__(self, dependencies: RuntimeDeps) -> None:
        self.deps = dependencies

    def load_task(self, state: TGAState) -> TGAState:
        task = self._task(state)
        self.deps.store.set_task_status(task.id, TaskStatus.RUNNING)
        self.deps.store.append_event(
            AgentEvent(
                task_id=task.id, type="TASK_STARTED", payload={"mode": task.mode}
            )
        )
        return {
            "objective": task.spec.objective,
            "status": "running",
            "intent_index": 0,
            "attempt": 1,
            "model_calls": 0,
            "plan_version": 0,
        }

    def preflight(self, state: TGAState) -> TGAState:
        task = self._task(state)
        runtime = self.deps.configuration.runtime
        role_status = {
            role: self.deps.configuration.role_model_status(role)
            for role in ("supervisor", "worker", "reviewer", "reporter")
        }
        unavailable = [
            role for role, value in role_status.items() if not value["ready"]
        ]
        if unavailable:
            raise RuntimeError(
                f"configured role models are unavailable: {', '.join(unavailable)}"
            )
        self.deps.store.append_event(
            AgentEvent(
                task_id=task.id,
                type="PREFLIGHT_COMPLETED",
                payload={
                    "roles": role_status,
                    "max_intents": runtime.budget.task.max_intents,
                    "max_model_calls": runtime.budget.task.max_model_calls,
                    "max_tool_calls": runtime.budget.task.max_tool_calls,
                },
            )
        )
        return {}

    def initial_plan(self, state: TGAState) -> TGAState:
        task = self._task(state)
        self._ensure_task_time(task.id)
        self._ensure_model_budget(state, "supervisor")
        self._solver_activity(
            task.id, "supervisor", "running", "正在拆解任务并生成初始 Plan"
        )
        draft = self.deps.agents.plan(task)
        calls = self._take_model_calls("supervisor")
        maximum = self.deps.configuration.runtime.budget.task.max_intents
        draft_intents = draft.intents[:maximum]
        intents = self._intents_from_drafts(task.id, draft_intents)
        if not intents:
            raise ValueError("supervisor produced an empty plan")
        plan = Plan(task_id=task.id, version=1, summary=draft.summary, intents=intents)
        self.deps.store.save_plan(plan)
        self.deps.store.save_solver_run(
            SolverRun(
                task_id=task.id,
                solver_id="supervisor",
                role=SolverRole.SUPERVISOR,
                status="completed",
                summary=draft.summary,
            )
        )
        self.deps.store.append_event(
            AgentEvent(
                task_id=task.id,
                type="PLAN_CREATED",
                solver_id="supervisor",
                payload={
                    "version": 1,
                    "summary": draft.summary,
                    "intent_ids": [item.id for item in intents],
                    "model_calls": calls,
                },
            )
        )
        self._solver_activity(
            task.id, "supervisor", "waiting", "初始 Plan 已生成，等待 Worker 执行"
        )
        for intent in intents:
            self.deps.store.append_event(
                AgentEvent(
                    task_id=task.id,
                    type="INTENT_CREATED",
                    solver_id="supervisor",
                    intent_id=intent.id,
                    payload={
                        "title": intent.title,
                        "objective": intent.objective,
                        "dependencies": list(intent.dependencies),
                        "success_criteria": list(intent.success_criteria),
                        "expected_evidence": list(intent.expected_evidence),
                        "stop_conditions": list(intent.stop_conditions),
                        "allowed_tools": list(intent.allowed_tools),
                        "status": intent.status.value,
                    },
                )
            )
        initial_intent = min(
            (item for item in intents if not item.dependencies),
            key=lambda item: (-item.priority, item.created_at, item.id),
        )
        return {
            "intent_ids": [item.id for item in intents],
            "intent_index": list(intents).index(initial_intent),
            "current_intent_id": initial_intent.id,
            "attempt": 1,
            "model_calls": state.get("model_calls", 0) + calls,
            "plan_version": 1,
        }

    def worker(self, state: TGAState) -> TGAState:
        task = self._task(state)
        intent = self._intent(state)
        self._ensure_task_time(task.id)
        self._ensure_model_budget(state, "worker")
        attempt = state.get("attempt", 1)
        if attempt > self.deps.configuration.runtime.budget.intent.max_attempts:
            raise BudgetExceededError(f"intent attempt budget exhausted: {attempt}")
        started = intent.model_copy(
            update={"status": IntentStatus.RUNNING, "updated_at": utc_now()}
        )
        self.deps.store.update_intent(started)
        existing_events = self.deps.store.list_events(task.id, limit=1000)
        if not any(
            item.type == "INTENT_STARTED" and item.intent_id == intent.id
            for item in existing_events
        ):
            self.deps.store.append_event(
                AgentEvent(
                    task_id=task.id,
                    type="INTENT_STARTED",
                    solver_id="worker",
                    intent_id=intent.id,
                    payload={"title": intent.title, "objective": intent.objective},
                )
            )
        if not any(
            item.type == "WORKER_ATTEMPT_STARTED"
            and item.intent_id == intent.id
            and item.payload.get("attempt") == attempt
            for item in existing_events
        ):
            self.deps.store.append_event(
                AgentEvent(
                    task_id=task.id,
                    type="WORKER_ATTEMPT_STARTED",
                    solver_id="worker",
                    intent_id=intent.id,
                    payload={"attempt": attempt},
                )
            )
        self._solver_activity(
            task.id,
            "worker",
            "running",
            f"正在执行 Intent：{intent.title}（第 {attempt} 次尝试）",
            intent.id,
        )
        tools = self._worker_tools(task.id, intent.id)
        role_tools = set(self.deps.configuration.runtime.roles["worker"].tools)
        interventions = self._interventions(task.id, intent.id)
        feedback = state.get("review_feedback", "")
        if interventions:
            feedback = "\n\n".join(
                item
                for item in (
                    feedback,
                    "User interventions:\n"
                    + "\n".join(
                        f"- [{item.get('kind', 'hint')}] {item.get('content', '')}"
                        for item in interventions[-10:]
                    ),
                )
                if item
            )
        intent_payload = {
            **intent.model_dump(mode="json"),
            "attempt": attempt,
            "retry_context": self._retry_context(task.id, intent.id),
            "handoff_context": self._handoff_context(task.id, intent.id),
        }
        draft = self.deps.agents.work(
            task,
            intent_payload,
            tools,
            feedback,
            worker_middleware(
                task=task,
                store=self.deps.store,
                workspace=self.deps.workspace,
                intent_id=intent.id,
                tools=tools,
                sandbox_image=self.deps.sandbox_image
                if "run_command" in role_tools
                else None,
                configuration=self.deps.configuration,
                skill_root=str(self.deps.skills.root) if self.deps.skills else None,
                attempt_tool_limit=self.deps.configuration.runtime.budget.roles.worker.tool_calls_per_attempt,
            ),
        )
        calls = self._take_model_calls("worker")
        claim_ids = self._persist_claims(task.id, intent.id, draft)
        self.deps.store.save_solver_run(
            SolverRun(
                task_id=task.id,
                solver_id="worker",
                role=SolverRole.WORKER,
                intent_id=intent.id,
                status="completed",
                summary=draft.summary,
                tool_calls=len(
                    [
                        action
                        for action in self.deps.store.list_actions(task.id)
                        if action.intent_id == intent.id
                    ]
                ),
            )
        )
        self.deps.store.append_event(
            AgentEvent(
                task_id=task.id,
                type="WORKER_ATTEMPT_COMPLETED",
                solver_id="worker",
                intent_id=intent.id,
                payload={
                    "attempt": attempt,
                    "summary": draft.summary[:1000],
                    "claim_ids": claim_ids,
                    "completion_status": draft.completion_status,
                    "criterion_assessments": [
                        item.model_dump(mode="json")
                        for item in draft.criterion_assessments
                    ],
                    "limitations": draft.limitations,
                    "model_calls": calls,
                },
            )
        )
        self._solver_activity(
            task.id, "worker", "waiting", "调查结果已提交 Reviewer", intent.id
        )
        return {
            "worker_draft": draft.model_dump(mode="json"),
            "claim_ids": claim_ids,
            "model_calls": state.get("model_calls", 0) + calls,
        }

    def reviewer(self, state: TGAState) -> TGAState:
        task = self._task(state)
        intent = self._intent(state)
        self._ensure_task_time(task.id)
        self._ensure_model_budget(state, "reviewer")
        self._solver_activity(
            task.id, "reviewer", "running", "正在审查 Worker 的证据包", intent.id
        )
        packet = self._review_packet(task, intent, state)
        proposed_review = self.deps.agents.review(task, packet)
        calls = self._take_model_calls("reviewer")
        review = self._guard_review(intent, state, proposed_review)
        eligible_claim_ids = {str(item.claim.get("id")) for item in packet.evidence}
        confirmed = set(review.confirmed_claim_ids).intersection(eligible_claim_ids)
        rejected = (
            set(review.rejected_claim_ids).intersection(eligible_claim_ids) - confirmed
        )
        for claim_id in eligible_claim_ids:
            claim = self.deps.store.get_claim(claim_id)
            if claim is None:
                continue
            status = (
                "confirmed"
                if claim_id in confirmed
                else "rejected"
                if claim_id in rejected
                else claim.status
            )
            self.deps.store.save_claim(
                claim.model_copy(
                    update={
                        "status": status,
                        "reviewed_by": "reviewer",
                        "reviewed_at": utc_now(),
                    }
                )
            )
        self._persist_findings(task.id, review, confirmed)
        self.deps.store.save_solver_run(
            SolverRun(
                task_id=task.id,
                solver_id="reviewer",
                role=SolverRole.REVIEWER,
                intent_id=intent.id,
                status="completed",
                summary=review.feedback,
            )
        )
        if review.passed:
            self.deps.store.update_intent(
                intent.model_copy(
                    update={"status": IntentStatus.COMPLETED, "updated_at": utc_now()}
                )
            )
            self.deps.store.append_event(
                AgentEvent(
                    task_id=task.id,
                    type="INTENT_COMPLETED",
                    solver_id="reviewer",
                    intent_id=intent.id,
                    payload={
                        "attempt": state.get("attempt", 1),
                        "confirmed_claim_ids": sorted(confirmed),
                    },
                )
            )
        self.deps.store.append_event(
            AgentEvent(
                task_id=task.id,
                type="REVIEW_COMPLETED",
                solver_id="reviewer",
                intent_id=intent.id,
                payload={
                    "attempt": state.get("attempt", 1),
                    "verdict": review.verdict,
                    "reason_codes": review.reason_codes,
                    "feedback": review.feedback,
                    "confirmed_claim_ids": sorted(confirmed),
                    "rejected_claim_ids": sorted(rejected),
                    "criterion_results": [
                        item.model_dump(mode="json")
                        for item in review.criterion_results
                    ],
                    "model_calls": calls,
                },
            )
        )
        self._solver_activity(
            task.id,
            "reviewer",
            "waiting",
            f"审查完成：{review.verdict}",
            intent.id,
        )
        return {
            "review_result": review.model_dump(mode="json"),
            "review_feedback": review.feedback,
            "model_calls": state.get("model_calls", 0) + calls,
        }

    def supervisor_checkpoint(self, state: TGAState) -> TGAState:
        task = self._task(state)
        self._ensure_task_time(task.id)
        self._ensure_model_budget(state, "supervisor")
        self._solver_activity(
            task.id,
            "supervisor",
            "running",
            "正在根据 Worker 结果与 Reviewer 意见决定下一步",
            state.get("current_intent_id"),
        )
        packet = self._situation_packet(task, state)
        proposed = self.deps.agents.decide(task, packet)
        calls = self._take_model_calls("supervisor")
        decision = self._guard_decision(task.id, state, proposed)
        self.deps.store.append_event(
            AgentEvent(
                task_id=task.id,
                type="SUPERVISOR_DECIDED",
                solver_id="supervisor",
                intent_id=state.get("current_intent_id"),
                payload={
                    **decision.model_dump(mode="json"),
                    "attempt": state.get("attempt", 1),
                    "proposed_action": proposed.action,
                    "model_calls": calls,
                },
            )
        )
        self._solver_activity(
            task.id,
            "supervisor",
            "waiting",
            f"检查点决策：{decision.action}",
            state.get("current_intent_id"),
        )
        return {
            "supervisor_decision": decision.model_dump(mode="json"),
            "model_calls": state.get("model_calls", 0) + calls,
        }

    def advance(self, state: TGAState) -> TGAState:
        intents = self.deps.store.list_intents(state["task_id"])
        self._block_failed_dependents(state["task_id"], intents)
        intents = self.deps.store.list_intents(state["task_id"])
        pending = [item for item in intents if item.status == IntentStatus.PENDING]
        if not pending:
            return {
                "advance_action": "report",
                "completed_with_limitations": True,
            }
        completed_ids = {
            item.id for item in intents if item.status == IntentStatus.COMPLETED
        }
        known_ids = {item.id for item in intents}
        runnable = [
            item
            for item in pending
            if set(item.dependencies).issubset(completed_ids)
            and set(item.dependencies).issubset(known_ids)
        ]
        if not runnable:
            for item in pending:
                self._mark_dependency_blocked(
                    item,
                    "Intent has unresolved, missing, or cyclic dependencies.",
                )
            return {
                "advance_action": "report",
                "completed_with_limitations": True,
            }
        next_intent = min(
            runnable, key=lambda item: (-item.priority, item.created_at, item.id)
        )
        ids = [item.id for item in intents]
        return {
            "advance_action": "worker",
            "intent_ids": ids,
            "intent_index": ids.index(next_intent.id),
            "current_intent_id": next_intent.id,
            "attempt": 1,
            "review_feedback": "",
            "claim_ids": [],
            "review_result": {},
            "worker_draft": {},
        }

    def retry(self, state: TGAState) -> TGAState:
        task_id = state["task_id"]
        attempt = state.get("attempt", 1) + 1
        feedback = (state.get("supervisor_decision") or {}).get(
            "feedback"
        ) or state.get("review_feedback", "")
        self.deps.store.append_event(
            AgentEvent(
                task_id=task_id,
                type="INTENT_RETRY_REQUESTED",
                solver_id="supervisor",
                intent_id=state.get("current_intent_id"),
                payload={"attempt": attempt, "feedback": feedback},
            )
        )
        return {"attempt": attempt, "review_feedback": feedback, "claim_ids": []}

    def revise_plan(self, state: TGAState) -> TGAState:
        task = self._task(state)
        decision = SupervisorDecision.model_validate(state["supervisor_decision"])
        existing = self.deps.store.list_intents(task.id)
        additions = self._intents_from_drafts(
            task.id, decision.new_intents, existing=tuple(existing)
        )
        current = self._intent(state)
        if current.status != IntentStatus.COMPLETED:
            self.deps.store.update_intent(
                current.model_copy(
                    update={"status": IntentStatus.BLOCKED, "updated_at": utc_now()}
                )
            )
        version = state.get("plan_version", 1) + 1
        plan = Plan(
            task_id=task.id,
            version=version,
            summary=decision.reason,
            intents=(*existing, *additions),
        )
        self.deps.store.save_plan(plan)
        self.deps.store.append_event(
            AgentEvent(
                task_id=task.id,
                type="PLAN_REVISED",
                solver_id="supervisor",
                payload={
                    "version": version,
                    "reason": decision.reason,
                    "added_intent_ids": [item.id for item in additions],
                },
            )
        )
        return {
            "intent_ids": [item.id for item in plan.intents],
            "plan_version": version,
        }

    def request_user_input(self, state: TGAState) -> TGAState:
        task = self._task(state)
        decision = SupervisorDecision.model_validate(state["supervisor_decision"])
        question = decision.user_question or decision.reason
        self.deps.store.set_task_status(task.id, TaskStatus.AWAITING_USER_INPUT)
        self.deps.store.append_event(
            AgentEvent(
                task_id=task.id,
                type="USER_INPUT_REQUIRED",
                solver_id="supervisor",
                intent_id=state.get("current_intent_id"),
                payload={"question": question, "reason": decision.reason},
            )
        )
        self._solver_activity(
            task.id,
            "supervisor",
            "awaiting_user_input",
            question,
            state.get("current_intent_id"),
        )
        return {"user_question": question}

    def wait_for_user(self, state: TGAState) -> TGAState:
        response = interrupt(
            {
                "kind": "user_input",
                "question": state.get(
                    "user_question", "Additional information is required."
                ),
                "intent_id": state.get("current_intent_id"),
            }
        )
        content = str(
            response.get("content", "") if isinstance(response, dict) else response
        ).strip()
        if not content:
            raise ValueError("user response cannot be blank")
        self.deps.store.set_task_status(state["task_id"], TaskStatus.RUNNING)
        self.deps.store.append_event(
            AgentEvent(
                task_id=state["task_id"],
                type="USER_INPUT_RECEIVED",
                solver_id="supervisor",
                intent_id=state.get("current_intent_id"),
                payload={"content": content[:2000]},
            )
        )
        self._solver_activity(
            state["task_id"],
            "supervisor",
            "running",
            "已收到用户回答，正在重新决策",
            state.get("current_intent_id"),
        )
        return {"user_response": content}

    def block_intent(self, state: TGAState) -> TGAState:
        task = self._task(state)
        intent = self._intent(state)
        reason = (state.get("supervisor_decision") or {}).get(
            "reason"
        ) or "Intent could not be completed."
        self.deps.store.update_intent(
            intent.model_copy(
                update={"status": IntentStatus.BLOCKED, "updated_at": utc_now()}
            )
        )
        self.deps.store.append_event(
            AgentEvent(
                task_id=task.id,
                type="INTENT_BLOCKED",
                solver_id="supervisor",
                intent_id=intent.id,
                payload={"reason": reason, "attempts": state.get("attempt", 1)},
            )
        )
        self._block_failed_dependents(
            task.id, self.deps.store.list_intents(task.id)
        )
        return {"completed_with_limitations": True}

    def reporter(self, state: TGAState) -> TGAState:
        task = self._task(state)
        self._ensure_task_time(task.id)
        self._ensure_model_budget(state, "reporter")
        self._solver_activity(task.id, "reporter", "running", "正在生成最终证据报告")
        snapshot = self.deps.store.snapshot(task.id)
        report_input = {
            **snapshot,
            "findings": [
                item for item in snapshot["findings"] if item["status"] == "confirmed"
            ],
            "evidence_claims": [
                item
                for item in snapshot["evidence_claims"]
                if item["status"] == "confirmed"
            ],
        }
        draft = self.deps.agents.report(task, report_input)
        calls = self._take_model_calls("reporter")
        markdown = render_markdown(report_input, draft)
        path = self.deps.workspace.reports / "report.md"
        path.write_text(markdown, encoding="utf-8")
        relative = path.relative_to(self.deps.workspace.root).as_posix()
        self.deps.store.save_report(task.id, markdown, relative)
        self.deps.store.save_solver_run(
            SolverRun(
                task_id=task.id,
                solver_id="reporter",
                role=SolverRole.REPORTER,
                status="completed",
                summary=draft.executive_summary,
            )
        )
        self.deps.store.append_event(
            AgentEvent(
                task_id=task.id,
                type="REPORT_GENERATED",
                solver_id="reporter",
                payload={"path": relative, "model_calls": calls},
            )
        )
        self._solver_activity(task.id, "reporter", "completed", "最终报告已生成")
        return {
            "report_path": relative,
            "model_calls": state.get("model_calls", 0) + calls,
        }

    def complete(self, state: TGAState) -> TGAState:
        task = self._task(state)
        intents = self.deps.store.list_intents(task.id)
        completed = [item for item in intents if item.status == IntentStatus.COMPLETED]
        confirmed_findings = [
            item
            for item in self.deps.store.list_findings(task.id)
            if item.status == "confirmed"
        ]
        if not completed and not confirmed_findings:
            message = "completion rejected: no completed intent or confirmed finding is available"
            self.deps.store.set_task_status(task.id, TaskStatus.FAILED)
            self.deps.store.append_event(
                AgentEvent(
                    task_id=task.id,
                    type="TASK_FAILED",
                    payload={
                        "error_type": "TaskCompletionRejectedError",
                        "message": message,
                        "report_path": state.get("report_path"),
                        "model_calls": state.get("model_calls", 0),
                    },
                )
            )
            return {"status": TaskStatus.FAILED.value, "error": message}
        limited = state.get("completed_with_limitations", False) or bool(
            [
                item
                for item in intents
                if item.status in {IntentStatus.BLOCKED, IntentStatus.FAILED}
            ]
        )
        if not completed and confirmed_findings:
            limited = True
        status = (
            TaskStatus.COMPLETED_WITH_LIMITATIONS if limited else TaskStatus.COMPLETED
        )
        self.deps.store.set_task_status(task.id, status)
        event_type = "TASK_COMPLETED_WITH_LIMITATIONS" if limited else "TASK_COMPLETED"
        self.deps.store.append_event(
            AgentEvent(
                task_id=task.id,
                type=event_type,
                payload={
                    "report_path": state.get("report_path"),
                    "completed_intents": len(completed),
                    "model_calls": state.get("model_calls", 0),
                },
            )
        )
        return {"status": status.value}

    def _worker_tools(self, task_id: str, intent_id: str):
        tools = ToolRegistry(
            store=self.deps.store,
            workspace=self.deps.workspace,
            task_id=task_id,
            intent_id=intent_id,
            model_read_max_bytes=self.deps.configuration.runtime.files.model_read_max_bytes,
            skills=self.deps.skills,
            external_tools=self.deps.external_tools,
        ).tools()
        configured = set(self.deps.store.get_policy(task_id).tool.allowed_tools)
        role_tools = set(self.deps.configuration.runtime.roles["worker"].tools)
        return [
            tool for tool in tools if tool.name in role_tools or tool.name in configured
        ]

    def _review_packet(self, task, intent: Intent, state: TGAState) -> ReviewPacket:
        evidence: list[EvidencePacket] = []
        claim_ids = list(dict.fromkeys(state.get("claim_ids", [])))
        artifact_by_id = {
            item.id: item for item in self.deps.store.list_artifacts(task.id)
        }
        for claim in self.deps.store.list_claims(task.id):
            artifact = artifact_by_id.get(claim.artifact_id)
            if (
                claim.status == "confirmed"
                and artifact is not None
                and (
                    claim.intent_id == intent.id
                    or (claim.intent_id is None and artifact.intent_id == intent.id)
                )
                and claim.id not in claim_ids
            ):
                claim_ids.append(claim.id)
        for claim_id in claim_ids:
            claim = self.deps.store.get_claim(claim_id)
            artifact = (
                self.deps.store.get_artifact(claim.artifact_id) if claim else None
            )
            if claim is None or artifact is None or artifact.task_id != task.id:
                continue
            excerpt, locator_valid = self._evidence_excerpt(
                artifact.path, claim.locator
            )
            try:
                actual_hash = hashlib.sha256(
                    self.deps.workspace.read_artifact(artifact.path)
                ).hexdigest()
            except (OSError, ValueError):
                actual_hash = ""
            valid = locator_valid and actual_hash == artifact.sha256
            evidence.append(
                EvidencePacket(
                    claim=claim.model_dump(mode="json"),
                    artifact_sha256=artifact.sha256,
                    artifact_kind=artifact.kind,
                    source_tool=artifact.tool_name,
                    cited_excerpt=excerpt,
                    locator_valid=valid,
                )
            )
        return ReviewPacket(
            task_objective=task.spec.objective,
            current_intent=intent.model_dump(mode="json"),
            worker_result=state.get("worker_draft", {}),
            evidence=evidence,
            task_success_criteria=list(task.spec.success_criteria),
            intent_success_criteria=list(intent.success_criteria),
            expected_evidence=list(intent.expected_evidence),
            stop_conditions=list(intent.stop_conditions),
        )

    def _retry_context(self, task_id: str, intent_id: str) -> dict[str, Any]:
        """Carry audited progress into a fresh Worker attempt without chat replay."""
        artifacts = {
            item.id: item for item in self.deps.store.list_artifacts(task_id)
        }
        confirmed_evidence: list[dict[str, Any]] = []
        for claim in self.deps.store.list_claims(task_id):
            artifact = artifacts.get(claim.artifact_id)
            if (
                claim.status != "confirmed"
                or artifact is None
                or not (
                    claim.intent_id == intent_id
                    or (claim.intent_id is None and artifact.intent_id == intent_id)
                )
            ):
                continue
            excerpt, valid = self._evidence_excerpt(artifact.path, claim.locator)
            if not valid:
                continue
            confirmed_evidence.append(
                {
                    "claim_id": claim.id,
                    "artifact_id": artifact.id,
                    "source_intent_id": artifact.intent_id,
                    "statement": claim.statement,
                    "locator": claim.locator.model_dump(mode="json"),
                    "excerpt": excerpt[:1500],
                }
            )
        reviews = [
            {
                "attempt": item.payload.get("attempt"),
                "verdict": item.payload.get("verdict"),
                "feedback": item.payload.get("feedback"),
                "criterion_results": item.payload.get("criterion_results") or [],
            }
            for item in self.deps.store.list_events(task_id, limit=1000)
            if item.type == "REVIEW_COMPLETED" and item.intent_id == intent_id
        ]
        commands = []
        seen_commands: set[str] = set()
        for action in self.deps.store.list_actions(task_id):
            if action.intent_id != intent_id or action.tool_name != "run_command":
                continue
            command = str(action.arguments.get("command") or "").strip()
            if not command or command in seen_commands:
                continue
            seen_commands.add(command)
            commands.append(
                {
                    "command": command[:1000],
                    "status": action.status,
                    "artifact_ids": list(action.artifact_ids),
                }
            )
        return {
            "confirmed_evidence": confirmed_evidence[-16:],
            "previous_reviews": reviews[-3:],
            "executed_commands": commands[-24:],
            "instructions": (
                "Treat confirmed evidence as already satisfied. Do not repeat an "
                "executed command. Use read_artifact when its output needs closer "
                "inspection, and collect only evidence missing from the latest review."
            ),
        }

    def _guard_review(
        self, intent: Intent, state: TGAState, review: ReviewDraft
    ) -> ReviewDraft:
        """A Reviewer may judge evidence, but Runtime enforces checklist coverage."""

        if review.verdict != "pass" or not intent.success_criteria:
            return review
        expected = set(range(len(intent.success_criteria)))
        verified = {
            item.criterion_index
            for item in review.criterion_results
            if item.status == "verified" and item.criterion_index in expected
        }
        worker = state.get("worker_draft") or {}
        worker_completed = worker.get("completion_status") == "completed"
        worker_met = {
            item["criterion_index"]
            for item in worker.get("criterion_assessments") or []
            if item.get("status") == "met"
            and isinstance(item.get("criterion_index"), int)
            and item["criterion_index"] in expected
        }
        if verified == expected and worker_met == expected and worker_completed:
            return review
        missing = sorted(expected - verified)
        worker_missing = sorted(expected - worker_met)
        reasons = list(dict.fromkeys([*review.reason_codes, "incomplete_objective"]))
        detail = (
            f"Unverified Intent criteria: {', '.join(str(item + 1) for item in missing)}."
            if missing
            else (
                "Worker did not declare the Intent acceptance contract completed."
                if not worker_completed
                else "Worker did not mark criteria "
                f"{', '.join(str(item + 1) for item in worker_missing)} as met."
            )
        )
        return review.model_copy(
            update={
                "verdict": "retry",
                "reason_codes": reasons,
                "feedback": " ".join(
                    item for item in (review.feedback, detail) if item
                ),
            }
        )

    def _situation_packet(self, task, state: TGAState) -> SituationPacket:
        policy = self.deps.store.get_policy(task.id)
        intents = self.deps.store.list_intents(task.id)
        plan = self.deps.store.get_plan(task.id)
        events = self.deps.store.list_events(task.id, limit=1000)
        failed = [
            {
                "attempt": item.payload.get("attempt"),
                "feedback": item.payload.get("feedback"),
            }
            for item in events
            if item.type == "REVIEW_COMPLETED" and item.payload.get("verdict") != "pass"
        ][-5:]
        budget = self.deps.configuration.runtime.budget
        return SituationPacket(
            task_objective=task.spec.objective,
            authorization=policy.model_dump(mode="json"),
            plan={
                "version": plan.version if plan else state.get("plan_version", 1),
                "summary": plan.summary if plan else "",
                "intents": [item.model_dump(mode="json") for item in intents],
            },
            current_intent={
                **self._intent(state).model_dump(mode="json"),
                "attempt": state.get("attempt", 1),
            },
            task_context=self._handoff_context(
                task.id, state.get("current_intent_id", "")
            ),
            worker_result=state.get("worker_draft"),
            review_result=state.get("review_result"),
            confirmed_findings=[
                item.model_dump(mode="json")
                for item in self.deps.store.list_findings(task.id)
                if item.status == "confirmed"
            ],
            failed_attempts=failed,
            user_interventions=self._interventions(
                task.id, state.get("current_intent_id", "")
            )[-10:],
            remaining_budget={
                "task_model_calls": max(
                    0, budget.task.max_model_calls - state.get("model_calls", 0)
                ),
                "task_tool_calls": max(
                    0,
                    budget.task.max_tool_calls
                    - len(self.deps.store.list_actions(task.id)),
                ),
                "intent_attempts": max(
                    0, budget.intent.max_attempts - state.get("attempt", 1)
                ),
                "intent_attempt_limit": budget.intent.max_attempts,
                "intent_slots": max(0, budget.task.max_intents - len(intents)),
            },
            tool_health={
                "available": sorted(
                    tool.name
                    for tool in self._worker_tools(task.id, state["current_intent_id"])
                )
            },
        )

    def _guard_decision(
        self, task_id: str, state: TGAState, decision: SupervisorDecision
    ) -> SupervisorDecision:
        budget = self.deps.configuration.runtime.budget
        intents = self.deps.store.list_intents(task_id)
        pending = [item for item in intents if item.status == IntentStatus.PENDING]
        review = state.get("review_result") or {}
        attempt = state.get("attempt", 1)
        if decision.action == "retry" and attempt >= budget.intent.max_attempts:
            return decision.model_copy(
                update={"action": "fail", "reason": "Intent attempt budget exhausted."}
            )
        if decision.action == "next_intent" and not pending:
            return decision.model_copy(
                update={
                    "action": "finish" if review.get("verdict") == "pass" else "fail"
                }
            )
        if decision.action == "finish" and pending:
            return decision.model_copy(
                update={
                    "action": "next_intent",
                    "reason": "Runtime requires all planned intents to reach a terminal state before completion.",
                }
            )
        if decision.action == "finish" and review.get("verdict") != "pass":
            return decision.model_copy(
                update={
                    "action": "retry"
                    if attempt < budget.intent.max_attempts
                    else "fail",
                    "reason": "Runtime rejected finish because the current review did not pass.",
                }
            )
        if decision.action == "finish" and any(
            action.status == "awaiting_approval"
            for action in self.deps.store.list_actions(task_id)
        ):
            return decision.model_copy(
                update={
                    "action": "ask_user",
                    "reason": "A tool approval is still pending.",
                    "user_question": "Resolve the pending tool approval before completing the task.",
                }
            )
        if decision.action == "revise_plan":
            slots = budget.task.max_intents - len(intents)
            additions = decision.new_intents[: max(0, slots)]
            task = self.deps.store.get_task(task_id)
            if (
                not additions
                or task is None
                or not self._revision_is_authorized(task, additions)
            ):
                return decision.model_copy(
                    update={
                        "action": "retry"
                        if attempt < budget.intent.max_attempts
                        else "fail",
                        "reason": "Runtime rejected an empty or over-budget plan revision.",
                    }
                )
            return decision.model_copy(update={"new_intents": additions})
        if decision.action == "ask_user" and not (
            decision.user_question or decision.reason
        ):
            return decision.model_copy(
                update={"user_question": "Please provide the missing information."}
            )
        return decision

    def _revision_is_authorized(self, task, additions) -> bool:
        """A revision may add work, never a target outside user authorization."""
        policy = self.deps.store.get_policy(task.id)
        authorized_text = "\n".join(
            [
                task.spec.objective,
                *task.spec.instructions,
                *task.spec.constraints,
                *task.spec.success_criteria,
                *policy.allowed_origins,
            ]
        )
        authorized_hosts = self._hosts(authorized_text)
        proposed_hosts = self._hosts(
            "\n".join(
                "\n".join(
                    [
                        item.title,
                        item.objective,
                        *item.success_criteria,
                        *item.expected_evidence,
                    ]
                )
                for item in additions
            )
        )
        return proposed_hosts.issubset(authorized_hosts)

    def _intent_from_draft(
        self, task_id: str, item, dependencies: tuple[str, ...] = ()
    ) -> Intent:
        budget = self.deps.configuration.runtime.budget
        policy_tools = self.deps.store.get_policy(task_id).tool.allowed_tools
        allowed_tools = policy_tools or SAFE_DEFAULT_TOOLS
        return Intent(
            task_id=task_id,
            title=item.title,
            objective=item.objective,
            dependencies=dependencies,
            priority=item.priority,
            success_criteria=tuple(item.success_criteria),
            expected_evidence=tuple(item.expected_evidence),
            allowed_tools=tuple(sorted(allowed_tools)),
            stop_conditions=(
                "Stop successfully as soon as every acceptance criterion is supported by cited Artifacts.",
                (
                    "Stop this attempt when its budget reaches "
                    f"{budget.roles.worker.calls_per_attempt} model calls or "
                    f"{budget.roles.worker.tool_calls_per_attempt} tool calls."
                ),
                (
                    "Stop as blocked or needs_user when authorization, required "
                    "user-only information, or an unavailable capability prevents progress."
                ),
                "Do not repeat a successful command merely to preserve output; Runtime already creates an Artifact.",
            ),
        )

    def _intents_from_drafts(
        self, task_id: str, drafts, *, existing: tuple[Intent, ...] = ()
    ) -> tuple[Intent, ...]:
        """Resolve model-authored dependency indexes into Runtime-owned Intent IDs."""
        created = [self._intent_from_draft(task_id, item) for item in drafts]
        available = [*existing, *created]
        offset = len(existing)
        resolved: list[Intent] = []
        for position, (draft, intent) in enumerate(zip(drafts, created, strict=True)):
            current_index = offset + position
            indexes = list(dict.fromkeys(draft.dependencies))
            dependencies = tuple(
                available[index].id
                for index in indexes
                if 0 <= index < current_index
            )
            resolved.append(intent.model_copy(update={"dependencies": dependencies}))
        return tuple(resolved)

    @staticmethod
    def _hosts(value: str) -> set[str]:
        hosts: set[str] = set()
        for match in re.findall(r"https?://[^\s<>'\"]+", value, flags=re.IGNORECASE):
            host = urlparse(match.rstrip(".,);]")).hostname
            if host:
                hosts.add(host.casefold())
        return hosts

    def _ensure_model_budget(self, state: TGAState, role: str) -> None:
        budget = self.deps.configuration.runtime.budget
        role_budget = getattr(budget.roles, role)
        needed = {
            "supervisor": lambda: (
                role_budget.calls_per_decision + role_budget.parse_retries
            ),
            "worker": lambda: role_budget.calls_per_attempt,
            "reviewer": lambda: (
                role_budget.calls_per_review + role_budget.parse_retries
            ),
            "reporter": lambda: (
                role_budget.calls_per_report + role_budget.parse_retries
            ),
        }[role]()
        if state.get("model_calls", 0) + needed > budget.task.max_model_calls:
            raise BudgetExceededError("task model-call budget exhausted")

    def _ensure_task_time(self, task_id: str) -> None:
        events = self.deps.store.list_events(task_id, limit=1000)
        started = next(
            (item.created_at for item in events if item.type == "TASK_STARTED"), None
        )
        if started is None:
            return
        started_at = (
            started
            if isinstance(started, datetime)
            else datetime.fromisoformat(str(started))
        )
        elapsed = (datetime.now(UTC) - started_at).total_seconds() / 60
        if elapsed >= self.deps.configuration.runtime.budget.task.max_duration_minutes:
            raise BudgetExceededError("task duration budget exhausted")

    def _take_model_calls(self, role: str) -> int:
        return self.deps.agents.take_model_calls(role)

    def _solver_activity(
        self,
        task_id: str,
        solver_id: str,
        status: str,
        summary: str,
        intent_id: str | None = None,
    ) -> None:
        self.deps.store.append_event(
            AgentEvent(
                task_id=task_id,
                type="SOLVER_STATUS_CHANGED",
                solver_id=solver_id,
                intent_id=intent_id,
                payload={
                    "status": status,
                    "summary": summary[:1000],
                    "stage": solver_id,
                },
            )
        )

    def _interventions(self, task_id: str, intent_id: str) -> list[dict[str, Any]]:
        return [
            item.payload
            for item in self.deps.store.list_events(task_id, limit=1000)
            if item.type in {"USER_INTERVENTION", "USER_INPUT_RECEIVED"}
            and (
                item.payload.get("scope") == "task"
                or item.payload.get("target_id") in {intent_id, "worker", "supervisor"}
                or item.type == "USER_INPUT_RECEIVED"
            )
        ]

    def _handoff_context(self, task_id: str, current_intent_id: str) -> dict[str, Any]:
        """Build a bounded, auditable Task ledger for a fresh Intent or checkpoint."""
        intents = self.deps.store.list_intents(task_id)
        intent_by_id = {item.id: item for item in intents}
        prior_ids = {item.id for item in intents if item.id != current_intent_id}
        artifacts = [
            item
            for item in self.deps.store.list_artifacts(task_id)
            if item.intent_id in prior_ids
        ]
        artifact_by_id = {item.id: item for item in artifacts}
        summaries: dict[str, str] = {}
        for run in self.deps.store.list_solver_runs(task_id):
            if run.intent_id in prior_ids and run.summary:
                summaries[run.intent_id] = run.summary[:2000]

        confirmed_evidence = []
        for claim in self.deps.store.list_claims(task_id):
            artifact = artifact_by_id.get(claim.artifact_id)
            if claim.status != "confirmed" or artifact is None:
                continue
            excerpt, valid = self._evidence_excerpt(artifact.path, claim.locator)
            if not valid:
                continue
            confirmed_evidence.append(
                {
                    "claim_id": claim.id,
                    "claim_intent_id": claim.intent_id,
                    "source_intent_id": artifact.intent_id,
                    "artifact_id": artifact.id,
                    "statement": claim.statement,
                    "locator": claim.locator.model_dump(mode="json"),
                    "excerpt": excerpt[:1000],
                }
            )

        commands = []
        for action in self.deps.store.list_actions(task_id):
            if action.intent_id == current_intent_id or action.tool_name != "run_command":
                continue
            command = str(action.arguments.get("command") or "").strip()
            if not command:
                continue
            commands.append(
                {
                    "intent_id": action.intent_id,
                    "command": command[:1000],
                    "status": action.status,
                    "artifact_ids": list(action.artifact_ids),
                }
            )

        return {
            "completed_intents": [
                {
                    "intent_id": item.id,
                    "title": item.title,
                    "objective": item.objective,
                    "status": item.status.value,
                    "summary": summaries.get(item.id, ""),
                }
                for item in intents
                if item.id in prior_ids
                and item.status
                in {IntentStatus.COMPLETED, IntentStatus.BLOCKED, IntentStatus.FAILED}
            ][-16:],
            "available_artifacts": [
                {
                    "artifact_id": item.id,
                    "source_intent_id": item.intent_id,
                    "source_intent_title": (
                        intent_by_id[item.intent_id].title
                        if item.intent_id in intent_by_id
                        else None
                    ),
                    "kind": item.kind,
                    "source_tool": item.tool_name,
                    "sha256": item.sha256,
                    "media_type": item.media_type,
                }
                for item in artifacts[-64:]
            ],
            "confirmed_evidence": confirmed_evidence[-24:],
            "confirmed_findings": [
                item.model_dump(mode="json")
                for item in self.deps.store.list_findings(task_id)
                if item.status == "confirmed"
            ][-24:],
            "executed_commands": commands[-32:],
            "instructions": (
                "Reuse relevant confirmed evidence and Artifact IDs instead of "
                "repeating completed work. Use list_artifacts for discovery and "
                "read_artifact only when the indexed content needs inspection. "
                "Prior conclusions do not satisfy the current Intent automatically; "
                "cite them in a current-Intent Claim and let Reviewer verify them."
            ),
        }

    def _block_failed_dependents(self, task_id: str, intents: list[Intent]) -> None:
        """Propagate terminal dependency failures without starting invalid work."""
        known = {item.id for item in intents}
        terminal_failures = {
            item.id
            for item in intents
            if item.status in {IntentStatus.BLOCKED, IntentStatus.FAILED}
        }
        changed = True
        while changed:
            changed = False
            for position, item in enumerate(intents):
                if item.status != IntentStatus.PENDING or not item.dependencies:
                    continue
                missing = set(item.dependencies) - known
                failed = set(item.dependencies).intersection(terminal_failures)
                if not missing and not failed:
                    continue
                reason = (
                    "Intent dependency is missing: " + ", ".join(sorted(missing))
                    if missing
                    else "Intent dependency did not complete: "
                    + ", ".join(sorted(failed))
                )
                self._mark_dependency_blocked(item, reason)
                intents[position] = item.model_copy(
                    update={"status": IntentStatus.BLOCKED}
                )
                terminal_failures.add(item.id)
                changed = True

    def _mark_dependency_blocked(self, intent: Intent, reason: str) -> None:
        if intent.status != IntentStatus.PENDING:
            return
        self.deps.store.update_intent(
            intent.model_copy(
                update={"status": IntentStatus.BLOCKED, "updated_at": utc_now()}
            )
        )
        self.deps.store.append_event(
            AgentEvent(
                task_id=intent.task_id,
                type="INTENT_BLOCKED",
                solver_id="supervisor",
                intent_id=intent.id,
                payload={
                    "reason": reason,
                    "dependencies": list(intent.dependencies),
                },
            )
        )

    def _task(self, state: TGAState):
        task = self.deps.store.get_task(state["task_id"])
        if task is None:
            raise KeyError(f"task not found: {state['task_id']}")
        if task.status == TaskStatus.CANCELLED:
            raise TaskCancelledError(f"task cancelled: {task.id}")
        return task

    def _intent(self, state: TGAState) -> Intent:
        intent_id = state["current_intent_id"]
        for intent in self.deps.store.list_intents(state["task_id"]):
            if intent.id == intent_id:
                return intent
        raise KeyError(f"intent not found: {intent_id}")

    def _persist_claims(
        self, task_id: str, intent_id: str, draft: WorkerDraft
    ) -> list[str]:
        claim_ids: list[str] = []
        claimed_artifacts: set[str] = set()
        for item in draft.claims:
            artifact = self.deps.store.get_artifact(item.artifact_id)
            if artifact is None or artifact.task_id != task_id:
                continue
            locator = EvidenceLocator(
                kind="line_range"
                if item.line_start is not None and item.line_end is not None
                else "whole",
                line_start=item.line_start,
                line_end=item.line_end,
                quote=item.quote,
            )
            _, valid = self._evidence_excerpt(artifact.path, locator)
            if not valid:
                continue
            claim_ids.append(
                self._save_claim_once(
                    task_id=task_id,
                    intent_id=intent_id,
                    artifact_id=artifact.id,
                    statement=item.statement,
                    locator=locator,
                    created_by="worker",
                )
            )
            claimed_artifacts.add(artifact.id)

        intent = next(
            (
                item
                for item in self.deps.store.list_intents(task_id)
                if item.id == intent_id
            ),
            None,
        )
        for assessment in draft.criterion_assessments:
            if assessment.status != "met":
                continue
            criterion = (
                intent.success_criteria[assessment.criterion_index]
                if intent is not None
                and assessment.criterion_index < len(intent.success_criteria)
                else f"acceptance criterion {assessment.criterion_index + 1}"
            )
            statement = assessment.note.strip() or f"Evidence supports: {criterion}"
            for artifact_id in assessment.artifact_ids:
                if artifact_id in claimed_artifacts:
                    continue
                artifact = self.deps.store.get_artifact(artifact_id)
                if (
                    artifact is None
                    or artifact.task_id != task_id
                ):
                    continue
                claim_id = self._save_claim_once(
                    task_id=task_id,
                    intent_id=intent_id,
                    artifact_id=artifact.id,
                    statement=statement,
                    locator=EvidenceLocator(kind="whole"),
                    created_by="runtime_from_acceptance_assessment",
                )
                if claim_id not in claim_ids:
                    claim_ids.append(claim_id)
                claimed_artifacts.add(artifact.id)
        return list(dict.fromkeys(claim_ids))

    def _save_claim_once(
        self,
        *,
        task_id: str,
        intent_id: str,
        artifact_id: str,
        statement: str,
        locator: EvidenceLocator,
        created_by: str,
    ) -> str:
        for existing in self.deps.store.list_claims(task_id):
            if (
                existing.intent_id == intent_id
                and existing.artifact_id == artifact_id
                and existing.statement == statement
                and existing.locator == locator
                and existing.status != "rejected"
            ):
                return existing.id
        source_artifact = self.deps.store.get_artifact(artifact_id)
        claim = EvidenceClaim(
            task_id=task_id,
            intent_id=intent_id,
            artifact_id=artifact_id,
            source_intent_id=source_artifact.intent_id if source_artifact else None,
            statement=statement,
            locator=locator,
            created_by=created_by,
        )
        self.deps.store.save_claim(claim)
        self.deps.store.append_event(
            AgentEvent(
                task_id=task_id,
                type="EVIDENCE_CLAIM_CREATED",
                solver_id="worker",
                intent_id=intent_id,
                payload={
                    "evidence_claim_id": claim.id,
                    "artifact_id": artifact_id,
                    "source_intent_id": claim.source_intent_id,
                    "statement_preview": statement[:1000],
                    "locator": locator.model_dump(mode="json"),
                    "created_by": created_by,
                },
            )
        )
        return claim.id

    def _evidence_excerpt(
        self, artifact_path: str, locator: EvidenceLocator
    ) -> tuple[str, bool]:
        try:
            text = self.deps.workspace.read_artifact(artifact_path).decode(
                "utf-8", errors="replace"
            )
        except (OSError, ValueError):
            return "", False
        if locator.quote and locator.quote not in text:
            return "", False
        if locator.kind == "line_range":
            lines = text.splitlines()
            if (
                locator.line_start is None
                or locator.line_end is None
                or locator.line_end > len(lines)
            ):
                return "", False
            start = max(1, locator.line_start - 2)
            end = min(len(lines), locator.line_end + 2)
            return "\n".join(lines[start - 1 : end])[:8000], True
        if locator.quote:
            index = text.find(locator.quote)
            return text[max(0, index - 500) : index + len(locator.quote) + 500][
                :8000
            ], True
        return text[:8000], True

    def _persist_findings(
        self, task_id: str, review: ReviewDraft, confirmed: set[str]
    ) -> None:
        allowed_severity = {"info", "low", "medium", "high", "critical"}
        for item in review.findings:
            claim_ids = tuple(
                claim_id
                for claim_id in item.evidence_claim_ids
                if claim_id in confirmed
            )
            if not claim_ids:
                continue
            self.deps.store.save_finding(
                Finding(
                    task_id=task_id,
                    title=item.title,
                    description=item.description,
                    severity=item.severity
                    if item.severity in allowed_severity
                    else "info",
                    status="confirmed",
                    evidence_claim_ids=claim_ids,
                    remediation=item.remediation,
                )
            )


__all__ = ["BudgetExceededError", "GraphNodes", "TaskCancelledError"]
