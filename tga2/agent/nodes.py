"""Business-aware nodes surrounding standard LangChain agents."""

from __future__ import annotations

from tga2.agent.graph import RuntimeDeps, TGAState
from tga2.agent.middleware import worker_middleware
from tga2.agent.schemas import ReviewDraft, WorkerDraft
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


class GraphNodes:
    def __init__(self, dependencies: RuntimeDeps) -> None:
        self.deps = dependencies

    def load_task(self, state: TGAState) -> TGAState:
        task = self._task(state)
        self.deps.store.set_task_status(task.id, TaskStatus.RUNNING)
        self.deps.store.append_event(
            AgentEvent(
                task_id=task.id,
                type="TASK_STARTED",
                payload={"mode": task.mode},
            )
        )
        return {
            "objective": task.spec.objective,
            "status": "running",
            "intent_index": 0,
            "iteration": 0,
            "max_iterations": 2,
        }

    def supervisor(self, state: TGAState) -> TGAState:
        task = self._task(state)
        draft = self.deps.agents.plan(task)
        intents = tuple(
            Intent(
                task_id=task.id,
                title=item.title,
                objective=item.objective,
                priority=item.priority,
            )
            for item in draft.intents
        )
        plan = Plan(task_id=task.id, summary=draft.summary, intents=intents)
        self.deps.store.save_plan(plan)
        run = SolverRun(
            task_id=task.id,
            solver_id="supervisor",
            role=SolverRole.SUPERVISOR,
            status="completed",
            summary=draft.summary,
        )
        self.deps.store.save_solver_run(run)
        self.deps.store.append_event(
            AgentEvent(
                task_id=task.id,
                type="PLAN_CREATED",
                solver_id="supervisor",
                payload={
                    "summary": draft.summary,
                    "intent_ids": [item.id for item in intents],
                },
            )
        )
        return {
            "intent_ids": [item.id for item in intents],
            "intent_index": 0,
            "current_intent_id": intents[0].id,
            "iteration": 0,
        }

    def worker(self, state: TGAState) -> TGAState:
        task = self._task(state)
        intent = self._intent(state)
        started = intent.model_copy(
            update={"status": IntentStatus.RUNNING, "updated_at": utc_now()}
        )
        self.deps.store.update_intent(started)
        tools = ToolRegistry(
            store=self.deps.store,
            workspace=self.deps.workspace,
            task_id=task.id,
            intent_id=intent.id,
            external_tools=self.deps.external_tools,
        ).tools()
        configured = set(self.deps.store.get_policy(task.id).tool.allowed_tools)
        role_tools = (
            set(self.deps.configuration.runtime.solver_tools.get("worker", ()))
            if self.deps.configuration
            else set()
        )
        if role_tools:
            tools = [
                tool
                for tool in tools
                if tool.name in role_tools or tool.name in configured
            ]
        draft = self.deps.agents.work(
            task,
            intent.model_dump(mode="json"),
            tools,
            state.get("review_feedback", ""),
            worker_middleware(
                task=task,
                store=self.deps.store,
                workspace=self.deps.workspace,
                intent_id=intent.id,
                tools=tools,
                sandbox_image=(
                    self.deps.sandbox_image if "run_command" in role_tools else None
                ),
            ),
        )
        claim_ids = self._persist_claims(task.id, intent.id, draft)
        run = SolverRun(
            task_id=task.id,
            solver_id="worker",
            role=SolverRole.WORKER,
            intent_id=intent.id,
            status="completed",
            summary=draft.summary,
        )
        self.deps.store.save_solver_run(run)
        self.deps.store.append_event(
            AgentEvent(
                task_id=task.id,
                type="WORKER_COMPLETED",
                solver_id="worker",
                intent_id=intent.id,
                payload={"summary": draft.summary[:1000], "claim_ids": claim_ids},
            )
        )
        return {"worker_draft": draft.model_dump(mode="json"), "claim_ids": claim_ids}

    def reviewer(self, state: TGAState) -> TGAState:
        task = self._task(state)
        intent = self._intent(state)
        claims = [
            claim
            for claim_id in state.get("claim_ids", [])
            if (claim := self.deps.store.get_claim(claim_id)) is not None
        ]
        worker = WorkerDraft.model_validate(state["worker_draft"])
        review = self.deps.agents.review(task, claims, worker)
        confirmed = set(review.confirmed_claim_ids).intersection(
            item.id for item in claims
        )
        rejected = (
            set(review.rejected_claim_ids).intersection(item.id for item in claims)
            - confirmed
        )
        for claim in claims:
            status = (
                "confirmed"
                if claim.id in confirmed
                else "rejected"
                if claim.id in rejected
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
        run = SolverRun(
            task_id=task.id,
            solver_id="reviewer",
            role=SolverRole.REVIEWER,
            intent_id=intent.id,
            status="completed",
            summary=review.feedback,
        )
        self.deps.store.save_solver_run(run)
        if review.passed:
            self.deps.store.update_intent(
                intent.model_copy(
                    update={
                        "status": IntentStatus.COMPLETED,
                        "updated_at": utc_now(),
                    }
                )
            )
        self.deps.store.append_event(
            AgentEvent(
                task_id=task.id,
                type="REVIEW_COMPLETED",
                solver_id="reviewer",
                intent_id=intent.id,
                payload={"passed": review.passed, "feedback": review.feedback},
            )
        )
        return {"review_passed": review.passed, "review_feedback": review.feedback}

    def advance(self, state: TGAState) -> TGAState:
        index = state.get("intent_index", 0) + 1
        ids = state["intent_ids"]
        return {
            "intent_index": index,
            "current_intent_id": ids[index],
            "iteration": 0,
            "review_feedback": "",
            "claim_ids": [],
        }

    def retry(self, state: TGAState) -> TGAState:
        return {"iteration": state.get("iteration", 0) + 1}

    def reporter(self, state: TGAState) -> TGAState:
        task = self._task(state)
        snapshot = self.deps.store.snapshot(task.id)
        draft = self.deps.agents.report(task, snapshot)
        markdown = render_markdown(snapshot, draft)
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
                payload={"path": relative},
            )
        )
        return {"report_path": relative}

    def complete(self, state: TGAState) -> TGAState:
        task = self._task(state)
        self.deps.store.set_task_status(task.id, TaskStatus.COMPLETED)
        self.deps.store.append_event(
            AgentEvent(
                task_id=task.id,
                type="TASK_COMPLETED",
                payload={"report_path": state.get("report_path")},
            )
        )
        return {"status": "completed"}

    def _task(self, state: TGAState):
        task = self.deps.store.get_task(state["task_id"])
        if task is None:
            raise KeyError(f"task not found: {state['task_id']}")
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
            if not self._locator_matches(artifact.path, locator):
                continue
            claim = EvidenceClaim(
                task_id=task_id,
                artifact_id=artifact.id,
                statement=item.statement,
                locator=locator,
                created_by="worker",
            )
            self.deps.store.save_claim(claim)
            claim_ids.append(claim.id)
        return claim_ids

    def _locator_matches(self, artifact_path: str, locator: EvidenceLocator) -> bool:
        try:
            text = self.deps.workspace.read_artifact(artifact_path).decode(
                "utf-8", errors="replace"
            )
        except (OSError, ValueError):
            return False
        if locator.quote and locator.quote not in text:
            return False
        if locator.kind == "line_range":
            return locator.line_end is not None and locator.line_end <= max(
                1, len(text.splitlines())
            )
        return True

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
            finding = Finding(
                task_id=task_id,
                title=item.title,
                description=item.description,
                severity=item.severity if item.severity in allowed_severity else "info",
                status="confirmed",
                evidence_claim_ids=claim_ids,
                remediation=item.remediation,
            )
            self.deps.store.save_finding(finding)


__all__ = ["GraphNodes"]
