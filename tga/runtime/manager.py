"""Session lifecycle manager for the v2 orchestration control plane."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from tga.contracts import ActionResult, ActionSpec, ArtifactRecord, Finding, SessionRecord, SolverRecord, SubagentOutput, SubagentRequest, TGAError, TGATask
from tga.core.evidence_gate import finding_ok
from tga.evidence.store import EvidenceStore, utc_now
from tga.runtime.board import BoardStore
from tga.runtime.challenge_state import ChallengeStateMachine
from tga.runtime.completion import CompletionGate
from tga.runtime.events import EventStore
from tga.runtime.observer import BoardObserver, Observer, ObserverPatch, ObserverSidecar, build_observer_context
from tga.runtime.prompts import build_solver_context
from tga.runtime.session import AgentSession
from tga.runtime.solver import MainSolver, Solver, build_runtime_solver
from tga.runtime.solver_pool import SolverPool
from tga.runtime.subagents import validate_output_ownership
from tga.skills.registry import SkillRegistry


MAX_SESSION_TURNS = 48
MAX_ACTIONS_PER_SOLVER = 32
MAX_CONSECUTIVE_EMPTY_PLANS = 2
MAX_SEMANTIC_RETRIES_PER_HYPOTHESIS = 3
MAX_ACTIVE_SOLVERS_PER_TASK = 3
MAX_REENTRY_CYCLES = 2


@dataclass(frozen=True)
class RuntimeLimits:
    max_turns: int = MAX_SESSION_TURNS
    max_actions_per_solver: int = MAX_ACTIONS_PER_SOLVER
    max_empty_plans: int = MAX_CONSECUTIVE_EMPTY_PLANS
    max_semantic_retries: int = MAX_SEMANTIC_RETRIES_PER_HYPOTHESIS
    max_active_solvers: int = MAX_ACTIVE_SOLVERS_PER_TASK
    max_reentry_cycles: int = MAX_REENTRY_CYCLES

    @classmethod
    def from_environment(cls) -> "RuntimeLimits":
        def bounded(name: str, default: int) -> int:
            try:
                return max(1, min(int(os.environ.get(name, str(default))), default))
            except ValueError:
                return default
        return cls(
            max_turns=bounded("TGA_MAX_SESSION_TURNS", MAX_SESSION_TURNS),
            max_actions_per_solver=bounded("TGA_MAX_ACTIONS_PER_SOLVER", MAX_ACTIONS_PER_SOLVER),
            max_empty_plans=bounded("TGA_MAX_CONSECUTIVE_EMPTY_PLANS", MAX_CONSECUTIVE_EMPTY_PLANS),
            max_semantic_retries=bounded("TGA_MAX_SEMANTIC_RETRIES_PER_HYPOTHESIS", MAX_SEMANTIC_RETRIES_PER_HYPOTHESIS),
            max_active_solvers=bounded("TGA_MAX_ACTIVE_SOLVERS_PER_TASK", MAX_ACTIVE_SOLVERS_PER_TASK),
            max_reentry_cycles=bounded("TGA_MAX_REENTRY_CYCLES", MAX_REENTRY_CYCLES),
        )


class ActionExecutor(Protocol):
    """Developer B's only execution boundary; it never owns session state."""

    def execute(self, *, task: TGATask, action: ActionSpec, workspace: Path) -> ActionResult: ...


class Manager:
    def __init__(
        self, *, store: EvidenceStore | None = None, run_root: str | Path | None = None,
        executor: ActionExecutor | None = None, solver: Solver | None = None, observer: Observer | None = None,
        skills: SkillRegistry | None = None,
    ):
        self.store = store
        self.run_root = Path(run_root or os.environ.get("TGA_RUN_ROOT", "runs"))
        self.executor = executor
        # Explicit test/custom solvers remain stable.  The application default
        # is resolved at each run so updating LLM settings does not require a
        # backend restart or leave a previously created Manager on the
        # deterministic fallback forever.
        self._explicit_solver = solver is not None
        self.solver = solver or build_runtime_solver()
        self.observer = observer or BoardObserver()
        self.skills = skills or SkillRegistry()
        self.limits = RuntimeLimits.from_environment()

    def run_session(self, task_id: str) -> dict:
        store, should_close = self._store_for(task_id)
        try:
            snapshot = store.task_snapshot(task_id)
            if not snapshot.get("task"):
                raise KeyError(f"task not found: {task_id}")
            if not self._explicit_solver:
                self.solver = build_runtime_solver()
            task = TGATask.model_validate(snapshot["task"])
            # The application-level manager is shared by API requests.  A
            # default executor must therefore be constructed per task so its
            # ArtifactStore cannot accidentally write into a previous task's
            # workspace.
            executor = self.executor or self._default_executor(task)
            return self._run(task=task, store=store, executor=executor)
        finally:
            if should_close:
                store.close()

    def start_session(self, *, task_id: str, initial_hint: str | None = None) -> dict:
        """Create the v2 session record before the API schedules its runner.

        Creation and execution are deliberately separate: the caller can
        return to the Runtime page immediately, while the manager loop runs in
        the background. The v2 task endpoint calls this before returning
        control to the Runtime UI.
        """
        store, should_close = self._store_for(task_id)
        try:
            snapshot = store.task_snapshot(task_id)
            if not snapshot.get("task"):
                raise KeyError(f"task not found: {task_id}")
            session = AgentSession(store=store, run_root=self.run_root, task_id=task_id).ensure(max_turns=self.limits.max_turns)
            task = TGATask.model_validate(snapshot["task"])
            ChallengeStateMachine(store).ensure(task)
            if session.status in {"completed", "cancelled", "failed"}:
                return {"accepted": False, "status": session.status, "reason": "terminal_session"}
            if session.status not in {"created", "running"}:
                return {"accepted": False, "status": session.status, "reason": "session_not_startable"}
            if initial_hint and initial_hint.strip():
                self._record_user_hint(store=store, task_id=task_id, content=initial_hint)
            self._checkpoint(store, task_id)
            return {"accepted": True, "status": store.get_session(task_id).status}
        finally:
            if should_close:
                store.close()

    def control_session(self, *, task_id: str, action: str, action_id: str | None = None) -> dict:
        store, should_close = self._store_for(task_id)
        try:
            session = store.get_session(task_id)
            if session is None:
                raise KeyError(f"session not found: {task_id}")
            events = EventStore(store)
            if action == "pause":
                session = store.update_session(task_id, status="paused", stop_reason="user_paused")
                for solver in store.list_solvers(task_id):
                    if solver.status in {"starting", "running"}:
                        store.update_solver(solver.id, status="waiting")
                        store.update_subagent_status(solver.id, "waiting")
            elif action == "resume":
                if session.status not in {"paused", "blocked"}:
                    return {"status": session.status, "accepted": False, "reason": "session_not_paused"}
                session = store.update_session(task_id, status="running", stop_reason="")
                challenge = store.get_challenge(task_id)
                if challenge and challenge.status == "blocked":
                    ChallengeStateMachine(store).transition(task_id, "active", reason="user_resumed")
                SolverPool(store=store, run_root=self.run_root, max_active=self.limits.max_active_solvers).resume_all(task_id)
            elif action == "cancel":
                session = store.update_session(task_id, status="cancelled", finished_at=utc_now(), stop_reason="user_cancelled")
                SolverPool(store=store, run_root=self.run_root, max_active=self.limits.max_active_solvers).stop_all(
                    task_id, status="cancelled", reason="user_cancelled"
                )
            elif action == "approve_action" and action_id:
                store.update_action_status(action_id, "approved")
            else:
                return {"accepted": False, "reason": "invalid_control_action"}
            events.append(task_id, "SESSION_CONTROLLED", {"action": action, "action_id": action_id, "status": session.status})
            self._checkpoint(store, task_id)
            return {"accepted": True, "status": session.status}
        finally:
            if should_close:
                store.close()

    def add_hint(self, *, task_id: str, content: str) -> dict:
        store, should_close = self._store_for(task_id)
        try:
            entry = self._record_user_hint(store=store, task_id=task_id, content=content)
            self._checkpoint(store, task_id)
            return {"accepted": True, "memory_id": entry.id}
        finally:
            if should_close:
                store.close()

    def spawn_subagent(self, *, task_id: str, request: dict | SubagentRequest) -> dict:
        """Start a bounded child Solver without sharing its parent's chat context."""
        store, should_close = self._store_for(task_id)
        try:
            parsed = request if isinstance(request, SubagentRequest) else SubagentRequest.model_validate(request)
            if parsed.task_id != task_id:
                raise ValueError("subagent request task ownership mismatch")
            session = store.get_session(task_id)
            if session is None or session.status != "running":
                raise ValueError("subagents require a running session")
            for hypothesis_id in parsed.hypothesis_ids:
                hypothesis = store.get_hypothesis(hypothesis_id)
                if hypothesis is None or hypothesis.task_id != task_id:
                    raise ValueError(f"unknown subagent hypothesis: {hypothesis_id}")
            for artifact_id in parsed.input_artifact_ids:
                artifact = store.get_artifact(artifact_id)
                if artifact is None or artifact.task_id != task_id:
                    raise ValueError(f"unknown subagent input artifact: {artifact_id}")
            pool = SolverPool(store=store, run_root=self.run_root, max_active=self.limits.max_active_solvers)
            child = pool.start(parsed, model_name=self.solver.model_name)
            self._checkpoint(store, task_id)
            return {"accepted": True, "solver_id": child.id, "request_id": parsed.id, "status": child.status}
        finally:
            if should_close:
                store.close()

    def accept_subagent_output(self, *, task_id: str, output: dict | SubagentOutput) -> dict:
        """Validate ownership, merge a structured hand-off, then run flag gates."""
        store, should_close = self._store_for(task_id)
        try:
            parsed = output if isinstance(output, SubagentOutput) else SubagentOutput.model_validate(output)
            record = next(
                (item for item in store.list_subagents(task_id) if item["request"]["id"] == parsed.request_id),
                None,
            )
            if record is None:
                raise KeyError(f"subagent request not found: {parsed.request_id}")
            request = SubagentRequest.model_validate(record["request"])
            validate_output_ownership(store=store, request=request, output=parsed)
            pool = SolverPool(store=store, run_root=self.run_root, max_active=self.limits.max_active_solvers)
            pool.finish(request, parsed)
            task_payload = store.task_snapshot(task_id).get("task")
            task = TGATask.model_validate(task_payload)
            artifacts = [store.get_artifact(item) for item in parsed.artifact_ids]
            gate = CompletionGate(store, artifact_text=self._artifact_text)
            solved = False
            for candidate in parsed.candidate_flags:
                decision = gate.evaluate(
                    task=task,
                    candidate=candidate,
                    artifacts=[item for item in artifacts if item is not None],
                    solver_id=parsed.solver_id,
                )
                solved = solved or decision.solved
                if solved:
                    break
            self._record_board_snapshot(store, task_id, solver_id=parsed.solver_id, cause="subagent_output")
            if solved:
                self._stop(store, task_id, parsed.solver_id, "completed", "confirmed_flag")
            else:
                self._maybe_spawn_reentry(store=store, pool=pool, request=request, output=parsed)
                try:
                    patch = self.observer.review(build_observer_context(store.task_snapshot(task_id)))
                    patch = patch if isinstance(patch, ObserverPatch) else ObserverPatch.model_validate(patch)
                    BoardObserver.apply(board=BoardStore(store), task_id=task_id, patch=patch)
                    EventStore(store).append(task_id, "OBSERVER_REVIEWED", {"steer_message": patch.steer_message}, solver_id=parsed.solver_id)
                except Exception as exc:
                    EventStore(store).append(task_id, "OBSERVER_FAILED", {"reason": str(exc)[:280]}, solver_id=parsed.solver_id)
            self._checkpoint(store, task_id)
            return {"accepted": True, "status": parsed.status, "solved": solved}
        finally:
            if should_close:
                store.close()

    def _maybe_spawn_reentry(
        self, *, store: EvidenceStore, pool: SolverPool, request: SubagentRequest, output: SubagentOutput,
    ) -> None:
        if request.role != "targeted" or not output.coverage_gaps or not request.hypothesis_ids:
            return
        hypotheses = [store.get_hypothesis(item) for item in request.hypothesis_ids]
        if not hypotheses or any(item is None or item.status != "inconclusive" for item in hypotheses):
            return
        existing = [
            item for item in store.list_subagents(request.task_id)
            if item["request"].get("role") == "recon"
            and str(item["request"].get("objective") or "").startswith("Re-entry recon cycle")
        ]
        if len(existing) >= self.limits.max_reentry_cycles:
            return
        main = next((item for item in store.list_solvers(request.task_id) if item.role == "main"), None)
        if main is None:
            return
        reentry = SubagentRequest(
            id=f"subreq_{uuid4().hex[:12]}",
            task_id=request.task_id,
            parent_solver_id=main.id,
            role="recon",
            objective=f"Re-entry recon cycle {len(existing) + 1}: close targeted coverage gaps without repeating prior actions.",
            hypothesis_ids=request.hypothesis_ids,
            input_artifact_ids=output.artifact_ids,
            skill_names=request.skill_names,
            max_actions=min(4, self.limits.max_actions_per_solver),
        )
        try:
            pool.start(reentry, model_name=self.solver.model_name)
        except ValueError as exc:
            EventStore(store).append(
                request.task_id,
                "SUBAGENT_REENTRY_SKIPPED",
                {"reason": str(exc)[:280], "parent_request_id": request.id},
                solver_id=output.solver_id,
            )

    @staticmethod
    def _record_user_hint(*, store: EvidenceStore, task_id: str, content: str):
        text = content.strip()
        if not text:
            raise ValueError("hint must not be empty")
        if len(text) > 800:
            raise ValueError("hint exceeds 800 characters")
        entry = BoardStore(store).add_memory(task_id=task_id, kind="hint", content=text, source="user")
        events = EventStore(store)
        events.append(task_id, "USER_HINT", {"memory_id": entry.id, "content": text})
        events.append(task_id, "MEMORY_UPSERTED", {"memory_id": entry.id, "kind": "hint", "source": "user"})
        Manager._record_board_snapshot(store, task_id, cause="user_hint")
        return entry

    def _run(self, *, task: TGATask, store: EvidenceStore, executor: ActionExecutor) -> dict:
        board = BoardStore(store)
        events = EventStore(store)
        durable = AgentSession(store=store, run_root=self.run_root, task_id=task.id)
        session = durable.ensure(max_turns=self.limits.max_turns)
        if session.status in {"completed", "cancelled", "failed", "paused"}:
            return store.task_snapshot(task.id)
        challenge_state = ChallengeStateMachine(store)
        challenge = challenge_state.ensure(task)
        if challenge.status == "solved":
            store.update_session(task.id, status="completed", finished_at=challenge.solved_at or utc_now(), stop_reason="confirmed_flag")
            return store.task_snapshot(task.id)
        if challenge.status != "active":
            challenge_state.activate(task, reason="session_started")
        pool = SolverPool(store=store, run_root=self.run_root, max_active=self.limits.max_active_solvers)
        if store.list_solvers(task.id):
            pool.recover(task.id)
            pool.resume_all(task.id)
        if not store.list_solvers(task.id):
            if self._active_solver_count(store, task.id) >= self.limits.max_active_solvers:
                self._stop_without_solver(store, task.id, "blocked", "active_solver_budget_exhausted")
                return store.task_snapshot(task.id)
            solver_id = f"solver_{uuid4().hex[:12]}"
            store.add_solver(SolverRecord(id=solver_id, task_id=task.id, status="running", model_name=self.solver.model_name, started_at=utc_now()))
            session = store.update_session(task.id, status="running", active_solver_id=solver_id, started_at=utc_now())
            events.append(task.id, "SESSION_STARTED", {"max_turns": session.max_turns}, solver_id=solver_id)
            events.append(task.id, "SOLVER_STARTED", {"role": "main", "model_name": self.solver.model_name}, solver_id=solver_id)
        # Existing/recovered sessions can already have active Solver records.
        # Reaching the configured cap is itself a stop condition; waiting for
        # one more would allow a fourth Solver and leave no safe selection.
        active_records = [item for item in store.list_solvers(task.id) if item.status in {"starting", "running", "waiting"}]
        main_records = [item for item in active_records if item.role == "main"]
        if len(main_records) != 1 or len([item for item in active_records if item.role != "main"]) > self.limits.max_active_solvers:
            active_id = store.get_session(task.id).active_solver_id
            if active_id:
                self._stop(store, task.id, active_id, "blocked", "active_solver_budget_exhausted")
            else:
                self._stop_without_solver(store, task.id, "blocked", "active_solver_budget_exhausted")
            return store.task_snapshot(task.id)
        solver = next(item for item in store.list_solvers(task.id) if item.id == store.get_session(task.id).active_solver_id)
        if not store.list_hypotheses(task.id):
            try:
                drafts = self.solver.initial_hypotheses(task=task, solver_id=solver.id)
            except Exception as exc:
                events.append(task.id, "SOLVER_FAILED", {"phase": "initial_hypotheses", "reason": str(exc)[:500]}, solver_id=solver.id)
                self._stop(store, task.id, solver.id, "failed", "solver_initialization_failed")
                return store.task_snapshot(task.id)
            if not 1 <= len(drafts) <= 5:
                self._stop(store, task.id, solver.id, "failed", "invalid_initial_hypothesis_count")
                return store.task_snapshot(task.id)
            for draft in drafts:
                hypothesis = board.create_hypothesis(task_id=task.id, draft=draft, owner_solver_id=solver.id)
                events.append(task.id, "HYPOTHESIS_CREATED", {"hypothesis_id": hypothesis.id, "statement": hypothesis.statement, "attack_class": hypothesis.attack_class}, solver_id=solver.id)
            self._record_board_snapshot(store, task.id, solver_id=solver.id, cause="initial_hypotheses")

        if not self._explicit_solver:
            self._ensure_automatic_subagents(task=task, store=store, pool=pool, main_solver=solver)

        empty_plans = 0
        sidecar = ObserverSidecar(self.observer)
        try:
          while True:
            self._drain_observer(sidecar, task.id, store, board, solver.id)
            session = store.get_session(task.id)
            if session is None or session.status != "running":
                break
            if session.turn_count >= session.max_turns:
                self._stop(store, task.id, solver.id, "blocked", "session_turn_budget_exhausted")
                break
            if not self._explicit_solver:
                selected = self._next_role_assignment(store, task.id)
                if selected is not None:
                    solver, hypothesis = selected
                else:
                    hypothesis = None
            else:
                hypothesis = self._next_hypothesis(store, task.id)
            if hypothesis is None:
                self._stop(store, task.id, solver.id, "blocked", "no_active_hypothesis")
                break
            loaded_skills = self.skills.for_turn(mode=task.mode, attack_class=hypothesis.attack_class)
            events.append(
                task.id,
                "SKILLS_LOADED",
                {
                    "hypothesis_id": hypothesis.id,
                    "skills": [
                        {"name": skill.name, "version": skill.version, "source": skill.source}
                        for skill in loaded_skills
                    ],
                },
                solver_id=solver.id,
            )
            solver_context = build_solver_context(
                task=task, snapshot=store.task_snapshot(task.id), skills=loaded_skills,
                role=solver.role, solver_id=solver.id,
            )
            try:
                proposed = self.solver.propose_action(task=task, solver_id=solver.id, hypothesis=hypothesis, snapshot=solver_context)
            except Exception as exc:
                events.append(task.id, "SOLVER_FAILED", {"phase": "propose_action", "reason": str(exc)[:500]}, solver_id=solver.id)
                self._stop(store, task.id, solver.id, "failed", "solver_planning_failed")
                break
            if proposed is None:
                empty_plans += 1
                events.append(
                    task.id,
                    "PLAN_EMPTY",
                    {
                        "hypothesis_id": hypothesis.id,
                        "count": empty_plans,
                        "reason": str(getattr(self.solver, "last_plan_reason", "no executable action was proposed"))[:500],
                    },
                    solver_id=solver.id,
                )
                if empty_plans >= self.limits.max_empty_plans:
                    sidecar.request(build_observer_context(store.task_snapshot(task.id)))
                    self._drain_observer(sidecar, task.id, store, board, solver.id, wait=True)
                    self._stop(store, task.id, solver.id, "blocked", "consecutive_empty_plans")
                    break
                continue
            empty_plans = 0
            try:
                self._validate_action(task, solver.id, hypothesis.id, proposed)
            except (TypeError, ValueError) as exc:
                events.append(task.id, "SOLVER_FAILED", {"phase": "action_contract", "reason": str(exc)[:500]}, solver_id=solver.id)
                self._stop(store, task.id, solver.id, "failed", "invalid_solver_action")
                break
            if self._semantic_retry_count(store, task.id, hypothesis.id, proposed) >= self.limits.max_semantic_retries:
                board.transition_hypothesis(hypothesis.id, status="inconclusive", last_result="semantic action retry budget exhausted")
                events.append(task.id, "HYPOTHESIS_STALLED", {"hypothesis_id": hypothesis.id, "reason": "semantic_retry_budget_exhausted"}, solver_id=solver.id)
                self._record_board_snapshot(store, task.id, solver_id=solver.id, cause="hypothesis_stalled")
                sidecar.request(build_observer_context(store.task_snapshot(task.id)))
                continue
            if self._actions_for_solver(store, task.id, solver.id) >= self._solver_action_limit(store, task.id, solver.id):
                self._stop(store, task.id, solver.id, "blocked", "solver_action_budget_exhausted")
                break
            store.add_action(proposed)
            events.append(task.id, "ACTION_PROPOSED", {"action_id": proposed.id, "capability": proposed.capability, "target": proposed.target, "hypothesis_id": hypothesis.id}, solver_id=solver.id)
            board.transition_hypothesis(hypothesis.id, status="testing")
            store.update_action_status(proposed.id, "approved")
            events.append(task.id, "ACTION_APPROVED", {"action_id": proposed.id}, solver_id=solver.id)
            store.update_action_status(proposed.id, "running")
            store.update_solver(solver.id, status="waiting")
            events.append(task.id, "ACTION_STARTED", {"action_id": proposed.id}, solver_id=solver.id)
            workspace = self.run_root / task.id / "solvers" / solver.id
            workspace.mkdir(parents=True, exist_ok=True)
            try:
                result = executor.execute(task=task, action=proposed, workspace=workspace)
            except Exception as exc:  # Executor failures are lifecycle failures, never uncaught API crashes.
                result = ActionResult(
                    action_id=proposed.id, task_id=task.id, solver_id=solver.id, status="failed",
                    summary="controlled executor raised an unexpected error", error=TGAError(code="EXECUTOR_FAILED", message=str(exc)[:500]),
                )
                store.add_action_result(result)
                store.update_action_status(proposed.id, "failed")
                store.update_solver(solver.id, status="failed", finished_at=utc_now())
                events.append(task.id, "ACTION_FINISHED", {"action_id": proposed.id, "status": "failed", "summary": result.summary, "artifact_ids": []}, solver_id=solver.id)
                self._stop(store, task.id, solver.id, "failed", "executor_failed")
                break
            try:
                self._validate_result(proposed, result)
            except (TypeError, ValueError) as exc:
                normalized = ActionResult(
                    action_id=proposed.id, task_id=task.id, solver_id=solver.id, status="failed",
                    summary="controlled executor returned an invalid result contract",
                    error=TGAError(code="INVALID_EXECUTOR_RESULT", message=str(exc)[:500]),
                )
                store.add_action_result(normalized)
                store.update_action_status(proposed.id, "failed")
                events.append(task.id, "ACTION_FINISHED", {"action_id": proposed.id, "status": "failed", "summary": normalized.summary, "artifact_ids": []}, solver_id=solver.id)
                self._stop(store, task.id, solver.id, "failed", "invalid_executor_result")
                break
            store.add_action_result(result)
            store.update_action_status(proposed.id, result.status)
            store.update_solver(solver.id, status="running")
            session = store.update_session(task.id, turn_count=session.turn_count + 1)
            events.append(task.id, "ACTION_FINISHED", {"action_id": proposed.id, "status": result.status, "summary": result.summary, "artifact_ids": result.artifact_ids}, solver_id=solver.id)
            artifacts_ok = self._apply_result(task, store, board, solver.id, hypothesis.id, result)
            if artifacts_ok:
                interpretation = self._interpret_result(hypothesis=hypothesis, result=result)
                if interpretation.status:
                    updated = board.transition_hypothesis(
                        hypothesis.id, status=interpretation.status, last_result=interpretation.last_result,
                        evidence_artifact_ids=result.artifact_ids, proposed_by_solver=interpretation.decisive,
                    )
                    events.append(task.id, "HYPOTHESIS_UPDATED", {"hypothesis_id": updated.id, "status": updated.status, "last_result": updated.last_result}, solver_id=solver.id)
                    self._record_board_snapshot(store, task.id, solver_id=solver.id, cause="hypothesis_updated")
            if store.task_snapshot(task.id)["flags"]:
                self._stop(store, task.id, solver.id, "completed", "confirmed_flag")
                break
            if session.turn_count and session.turn_count % 6 == 0:
                sidecar.request(build_observer_context(store.task_snapshot(task.id)))
            durable.checkpoint()
        finally:
            self._drain_observer(sidecar, task.id, store, board, solver.id, wait=True)
            sidecar.close()
            durable.checkpoint()
        return store.task_snapshot(task.id)

    def _apply_result(self, task: TGATask, store: EvidenceStore, board: BoardStore, solver_id: str, hypothesis_id: str, result: ActionResult) -> bool:
        events = EventStore(store)
        artifacts = [self._resolve_artifact(store, task.id, item) for item in result.artifact_ids]
        known = [item for item in artifacts if item is not None]
        if len(known) != len(result.artifact_ids):
            events.append(task.id, "RESULT_REJECTED", {"action_id": result.action_id, "reason": "unpersisted_artifact_reference"}, solver_id=solver_id)
            for flag in result.candidate_flags:
                events.append(task.id, "GATE_REJECTED", {"kind": "flag", "value": flag, "reason": "unpersisted_artifact_reference"}, solver_id=solver_id)
            for finding in result.candidate_findings:
                events.append(task.id, "GATE_REJECTED", {"kind": "finding", "finding_id": finding.id, "reason": "unpersisted_artifact_reference"}, solver_id=solver_id)
            return False
        if result.facts and known:
            try:
                memory = board.add_memory(task_id=task.id, kind="fact", content="\n".join(result.facts)[:800], source=f"solver:{solver_id}", artifact_ids=[item.id for item in known])
                events.append(task.id, "MEMORY_UPSERTED", {"memory_id": memory.id, "kind": memory.kind}, solver_id=solver_id)
            except ValueError:
                pass
        if result.status in {"failed", "blocked"} and known:
            try:
                memory = board.add_memory(task_id=task.id, kind="failure_boundary", content=result.summary[:800], source=f"solver:{solver_id}", artifact_ids=[item.id for item in known])
                events.append(task.id, "MEMORY_UPSERTED", {"memory_id": memory.id, "kind": memory.kind}, solver_id=solver_id)
            except ValueError:
                pass
        artifact_texts = {artifact.id: self._artifact_text(task.id, artifact) for artifact in known}
        completion_gate = CompletionGate(store, artifact_text=self._artifact_text)
        for flag in result.candidate_flags:
            completion_gate.evaluate(task=task, candidate=flag, artifacts=known, solver_id=solver_id)
        for finding in result.candidate_findings:
            store.add_candidate_finding(finding)
            artifact_text = artifact_texts.get(finding.evidence_artifact_id or "")
            if finding_ok(finding, task=task, artifact_text=artifact_text):
                store.confirm_finding(finding.id, finding.evidence_artifact_id or "")
                events.append(task.id, "FINDING_CONFIRMED", {"finding_id": finding.id, "evidence_artifact_id": finding.evidence_artifact_id}, solver_id=solver_id)
            else:
                events.append(task.id, "GATE_REJECTED", {"kind": "finding", "finding_id": finding.id, "reason": "finding_evidence_gate_failed"}, solver_id=solver_id)
        self._record_board_snapshot(store, task.id, solver_id=solver_id, cause="action_result")
        return True

    def _drain_observer(
        self, sidecar: ObserverSidecar, task_id: str, store: EvidenceStore, board: BoardStore,
        solver_id: str, *, wait: bool = False,
    ) -> None:
        try:
            patch = sidecar.drain(wait=wait)
            if patch is None:
                return
            BoardObserver.apply(board=board, task_id=task_id, patch=patch)
            EventStore(store).append(task_id, "OBSERVER_REVIEWED", {"steer_message": patch.steer_message}, solver_id=solver_id)
            self._record_board_snapshot(store, task_id, solver_id=solver_id, cause="observer_review")
        except Exception as exc:  # observer never terminates a solver
            EventStore(store).append(task_id, "OBSERVER_FAILED", {"reason": str(exc)[:280]}, solver_id=solver_id)

    def _interpret_result(self, *, hypothesis, result: ActionResult):
        interpret = getattr(self.solver, "interpret_result", None)
        if not callable(interpret):
            from tga.runtime.solver import SolverInterpretation
            return SolverInterpretation(last_result=result.summary)
        return interpret(hypothesis=hypothesis, result=result)

    @staticmethod
    def _next_hypothesis(store: EvidenceStore, task_id: str):
        candidates = [item for item in store.list_hypotheses(task_id, active_only=True) if item.status in {"pending", "testing"}]
        # Continue a line while it is actively being tested; otherwise an
        # unrelated unplanned candidate can starve retry accounting.
        return sorted(candidates, key=lambda item: (item.status != "testing", -item.confidence, item.created_at))[0] if candidates else None

    def _ensure_automatic_subagents(
        self, *, task: TGATask, store: EvidenceStore, pool: SolverPool, main_solver: SolverRecord,
    ) -> None:
        """Create non-overlapping role routes from the current durable board."""
        if store.list_subagents(task.id):
            return
        hypotheses = [item for item in store.list_hypotheses(task.id, active_only=True) if item.status in {"pending", "testing", "inconclusive"}]
        recon_ids = [item.id for item in hypotheses if item.attack_class.casefold() == "recon"]
        targeted_ids = [item.id for item in hypotheses if item.attack_class.casefold() != "recon"]
        role_specs = [
            (
                "recon",
                "Map the authorized landing surface, assets, forms, APIs and coverage gaps without duplicating targeted tests.",
                recon_ids,
            ),
            (
                "targeted",
                "Validate one high-confidence non-recon hypothesis at a time with minimal evidence-producing actions.",
                targeted_ids,
            ),
            (
                "research",
                "Convert observed technologies, errors and protocols into executable next tests; do not execute broad scans.",
                targeted_ids,
            ),
        ]
        for role, objective, hypothesis_ids in role_specs:
            # Targeted/research are useful only after recon or task input has
            # produced a concrete candidate.  Recon still starts on an empty
            # board so the system can make that progress itself.
            if role != "recon" and not hypothesis_ids:
                continue
            request = SubagentRequest(
                id=f"subreq_{uuid4().hex[:12]}",
                task_id=task.id,
                parent_solver_id=main_solver.id,
                role=role,
                objective=objective,
                hypothesis_ids=hypothesis_ids,
                input_artifact_ids=[],
                skill_names=[],
                max_actions=min(8, self.limits.max_actions_per_solver),
            )
            pool.start(request, model_name=self.solver.model_name)

    @staticmethod
    def _next_role_assignment(store: EvidenceStore, task_id: str):
        active = [item for item in store.list_solvers(task_id) if item.role != "main" and item.status in {"running", "waiting"}]
        hypotheses = [item for item in store.list_hypotheses(task_id, active_only=True) if item.status in {"pending", "testing"}]
        actions = store.list_actions(task_id)
        action_counts = {solver.id: sum(1 for item in actions if item.get("solver_id") == solver.id) for solver in active}
        for solver in sorted(active, key=lambda item: (action_counts[item.id], item.started_at or "", item.id)):
            if solver.role == "recon":
                compatible = [item for item in hypotheses if item.attack_class.casefold() == "recon"]
            else:
                compatible = [item for item in hypotheses if item.attack_class.casefold() != "recon"]
            if compatible:
                return solver, sorted(compatible, key=lambda item: (item.status != "testing", -item.confidence, item.created_at))[0]
        return None

    def _solver_action_limit(self, store: EvidenceStore, task_id: str, solver_id: str) -> int:
        record = next((item for item in store.list_subagents(task_id) if item["solver_id"] == solver_id), None)
        if record is None:
            return self.limits.max_actions_per_solver
        return min(self.limits.max_actions_per_solver, int(record["request"].get("max_actions") or self.limits.max_actions_per_solver))

    @staticmethod
    def _validate_action(task: TGATask, solver_id: str, hypothesis_id: str, action: ActionSpec) -> None:
        if action.task_id != task.id or action.solver_id != solver_id or action.hypothesis_id != hypothesis_id:
            raise ValueError("action ownership does not match active task, solver, and hypothesis")
        if action.risk == "destructive":
            raise ValueError("destructive actions are not permitted by the runtime")

    @staticmethod
    def _validate_result(action: ActionSpec, result: ActionResult) -> None:
        if (result.action_id, result.task_id, result.solver_id) != (action.id, action.task_id, action.solver_id):
            raise ValueError("executor result does not match action ownership")

    @staticmethod
    def _semantic_retry_count(store: EvidenceStore, task_id: str, hypothesis_id: str, action: ActionSpec) -> int:
        fingerprint = json.dumps([action.capability, action.target, action.arguments], sort_keys=True, ensure_ascii=False)
        return sum(
            1 for item in store.list_actions(task_id)
            if item.get("hypothesis_id") == hypothesis_id
            and json.dumps([item.get("capability"), item.get("target"), item.get("arguments")], sort_keys=True, ensure_ascii=False) == fingerprint
        )

    @staticmethod
    def _actions_for_solver(store: EvidenceStore, task_id: str, solver_id: str) -> int:
        return sum(1 for item in store.list_actions(task_id) if item.get("solver_id") == solver_id)

    @staticmethod
    def _active_solver_count(store: EvidenceStore, task_id: str) -> int:
        return sum(1 for item in store.list_solvers(task_id) if item.status in {"starting", "running", "waiting"})

    def _artifact_text(self, task_id: str, artifact) -> str:
        root = (self.run_root / task_id / "artifacts").resolve()
        try:
            path = (root / artifact.path).resolve()
            path.relative_to(root)
            return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
        except (OSError, ValueError):
            return ""

    def _resolve_artifact(self, store: EvidenceStore, task_id: str, artifact_id: str) -> ArtifactRecord | None:
        existing = store.get_artifact(artifact_id)
        if existing is not None:
            return existing
        root = (self.run_root / task_id / "artifacts").resolve()
        matches = list(root.glob(f"{artifact_id}.*")) if root.exists() else []
        if len(matches) != 1:
            return None
        path = matches[0]
        try:
            path.resolve().relative_to(root)
            payload = path.read_bytes()
        except (OSError, ValueError):
            return None
        import hashlib
        from datetime import UTC, datetime

        artifact = ArtifactRecord(
            id=artifact_id, task_id=task_id, kind="file", path=path.name,
            sha256=hashlib.sha256(payload).hexdigest(), tool="runtime.executor",
            created_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        )
        store.add_artifact(artifact)
        return artifact

    def _stop(self, store: EvidenceStore, task_id: str, solver_id: str, status: str, reason: str) -> None:
        store.update_session(task_id, status=status, finished_at=utc_now(), stop_reason=reason)
        challenge = store.get_challenge(task_id)
        if challenge and challenge.status == "active" and status in {"blocked", "failed", "cancelled"}:
            ChallengeStateMachine(store).transition(task_id, "blocked", reason=reason, solver_id=solver_id)
        SolverPool(store=store, run_root=self.run_root, max_active=self.limits.max_active_solvers).stop_all(
            task_id, status=status, reason=reason
        )
        EventStore(store).append(task_id, "SESSION_STOPPED", {"status": status, "reason": reason}, solver_id=solver_id)
        if status == "completed":
            from tga.reporting.markdown_report import render_markdown_report

            report = self.run_root / task_id / "reports" / "report.md"
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text(render_markdown_report(store.task_snapshot(task_id)), encoding="utf-8")

    def _stop_without_solver(self, store: EvidenceStore, task_id: str, status: str, reason: str) -> None:
        store.update_session(task_id, status=status, finished_at=utc_now(), stop_reason=reason)
        challenge = store.get_challenge(task_id)
        if challenge and challenge.status == "active" and status in {"blocked", "failed", "cancelled"}:
            ChallengeStateMachine(store).transition(task_id, "blocked", reason=reason)
        EventStore(store).append(task_id, "SESSION_STOPPED", {"status": status, "reason": reason})

    def _store_for(self, task_id: str) -> tuple[EvidenceStore, bool]:
        if self.store is not None:
            return self.store, False
        return EvidenceStore(self.run_root / task_id / "evidence.db"), True

    def _default_executor(self, task: TGATask) -> ActionExecutor:
        """Wire B's controlled adapter without giving the manager tool access."""
        from tga.capabilities.runtime import ControlledActionExecutor
        from tga.evidence.artifacts import ArtifactStore
        from tga.tools.bootstrap import build_tool_runner_from_env

        artifact_store = ArtifactStore(self.run_root / task.id / "artifacts")
        return ControlledActionExecutor(
            artifact_store=artifact_store,
            tool_runner=build_tool_runner_from_env(artifact_store),
        )

    def _checkpoint(self, store: EvidenceStore, task_id: str) -> None:
        AgentSession(store=store, run_root=self.run_root, task_id=task_id).checkpoint()

    @staticmethod
    def _record_board_snapshot(
        store: EvidenceStore, task_id: str, *, cause: str, solver_id: str | None = None,
    ) -> None:
        """Persist the board state at a concrete event sequence for replay.

        The board is deliberately bounded (hypotheses + at most 20 active
        memory entries), so a compact event payload is safe and lets replay
        render the actual historical state rather than today's state.
        """
        EventStore(store).append(
            task_id,
            "BOARD_SNAPSHOT",
            {
                "cause": cause,
                "board": {
                    "hypotheses": [item.model_dump(mode="json") for item in store.list_hypotheses(task_id)],
                    "memory": [item.model_dump(mode="json") for item in store.list_memory(task_id)],
                },
            },
            solver_id=solver_id,
        )



_manager: Manager | None = None


def get_manager() -> Manager:
    """Application entry point used by the v2 control adapter."""
    global _manager
    if _manager is None:
        _manager = Manager()
    return _manager
