"""Lifecycle coordination for runtime sessions.

The coordinator is the only write boundary for session lifecycle changes.
It validates transitions, persists state and events inside one transaction,
and releases runtime resources when a session reaches a terminal state.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from tga.contracts import CandidateFindingRecord, ChallengeContract, SessionRecord, TGATask
from tga.evidence.store import EvidenceStore, utc_now


SESSION_TRANSITIONS: dict[str, set[str]] = {
    "created": {"running", "paused", "completed", "blocked", "cancelled", "failed"},
    "running": {"paused", "awaiting_approval", "completed", "blocked", "cancelled", "failed"},
    "paused": {"running", "cancelled", "failed", "blocked"},
    "awaiting_approval": {"running", "cancelled", "failed", "blocked"},
    "blocked": {"running", "cancelled", "failed"},
    "completed": set(),
    "cancelled": set(),
    "failed": set(),
}


class SessionTransitionError(ValueError):
    code = "SESSION_TRANSITION_INVALID"

    def __init__(self, from_status: str, to_status: str):
        super().__init__(f"invalid session transition: {from_status} -> {to_status}")
        self.from_status = from_status
        self.to_status = to_status


@dataclass(frozen=True)
class SessionOutcome:
    status: str
    stop_reason: str = ""
    turn_count: int = 0
    summary: str = ""
    evidence_artifact_ids: list[str] | None = None
    error: dict[str, Any] | None = None
    details: dict[str, Any] | None = None


class SessionCoordinator:
    def __init__(self, store: EvidenceStore):
        self.store = store

    def create(self, session: SessionRecord) -> SessionRecord:
        with self.store.transaction():
            return self.store.create_session(session)

    def ensure_session(
        self,
        *,
        task: TGATask,
        max_turns: int,
        supervisor_solver_id: str,
    ) -> SessionRecord:
        """Create or recover the Task lifecycle aggregate for the formal Supervisor."""
        with self.store.transaction():
            session = self.store.get_session(task.id)
            created = session is None
            if session is None:
                session = self.store.create_session(SessionRecord(
                    task_id=task.id,
                    schema_version=task.schema_version,
                    max_turns=max_turns,
                    active_solver_id=supervisor_solver_id,
                    workspace_path="workspace",
                    mcp_catalog_version=task.mcp_capabilities.catalog_version,
                ))
            elif session.max_turns > max_turns:
                session = self.store.update_session(task.id, max_turns=max_turns)
            elif session.active_solver_id is None:
                session = self.store.update_session(
                    task.id, active_solver_id=supervisor_solver_id
                )

            if task.mode == "ctf" and self.store.get_challenge(task.id) is None:
                self.store.upsert_challenge(ChallengeContract(
                    task_id=task.id,
                    entry_url=task.task_entry_url,
                    allowed_origins=list(task.execution_policy.network.seed_origins if task.execution_policy else []),
                    status="unknown",
                    flag_format=task.flag_format,
                ))

            if created:
                self.store.append_agent_event(
                    task.id,
                    "SESSION_CREATED",
                    {"status": session.status, "max_turns": session.max_turns},
                    solver_id=supervisor_solver_id,
                )
            return session

    def update_session(self, *, task_id: str, **changes: Any) -> SessionRecord:
        if not changes:
            session = self.store.get_session(task_id)
            if session is None:
                raise KeyError(f"session not found: {task_id}")
            return session
        with self.store.transaction():
            session = self.store.get_session(task_id)
            if session is None:
                raise KeyError(f"session not found: {task_id}")
            if "status" in changes:
                status = str(changes["status"])
                if status != session.status and status not in SESSION_TRANSITIONS.get(session.status, set()):
                    raise SessionTransitionError(session.status, status)
            if "turn_count" in changes:
                changes["turn_count"] = max(0, int(changes["turn_count"]))
            if "max_turns" in changes:
                changes["max_turns"] = max(1, int(changes["max_turns"]))
            return self.store.update_session(task_id, **changes)

    def advance_turn(self, *, task_id: str, delta: int = 1) -> SessionRecord:
        with self.store.transaction():
            session = self.store.get_session(task_id)
            if session is None:
                raise KeyError(f"session not found: {task_id}")
            if delta == 0:
                return session
            return self.store.update_session(task_id, turn_count=session.turn_count + delta)

    def reserve_turn(self, *, task_id: str) -> SessionRecord | None:
        with self.store.transaction():
            return self.store.reserve_turn(task_id)

    def start(self, *, task_id: str, solver_id: str | None = None) -> SessionRecord:
        session = self.store.get_session(task_id)
        if session is None:
            raise KeyError(f"session not found: {task_id}")
        if session.status == "running":
            effective_solver_id = solver_id or session.active_solver_id
            with self.store.transaction():
                session = self.store.update_session(
                    task_id,
                    active_solver_id=effective_solver_id,
                    started_at=session.started_at or utc_now(),
                    finished_at=None,
                    stop_reason="",
                )
                return session
        return self._transition(task_id=task_id, status="running", reason="session_started", solver_id=solver_id)

    def pause(self, *, task_id: str, reason: str = "user_paused") -> SessionRecord:
        return self._transition(task_id=task_id, status="paused", reason=reason)

    def resume(self, *, task_id: str, reason: str = "user_resumed") -> SessionRecord:
        return self._transition(task_id=task_id, status="running", reason=reason)

    def await_approval(self, *, task_id: str, action_id: str) -> SessionRecord:
        return self._transition(
            task_id=task_id,
            status="awaiting_approval",
            reason=f"action_approval_required:{action_id}",
        )

    def cancel(self, *, task_id: str, reason: str = "user_cancelled") -> SessionRecord:
        return self._transition(task_id=task_id, status="cancelled", reason=reason, finished=True)

    def complete(self, *, task_id: str, summary: str, evidence_artifact_ids: list[str], turn_count: int, reason: str = "finish_accepted", solver_id: str | None = None, details: dict[str, Any] | None = None) -> SessionRecord:
        return self._transition(task_id=task_id, status="completed", reason=reason, solver_id=solver_id, finished=True, summary=summary, evidence_artifact_ids=evidence_artifact_ids, turn_count=turn_count, details=details)

    def block(self, *, task_id: str, reason: str, turn_count: int | None = None, solver_id: str | None = None, error: dict[str, Any] | None = None) -> SessionRecord:
        return self._transition(task_id=task_id, status="blocked", reason=reason, turn_count=turn_count, solver_id=solver_id, error=error)

    def fail(self, *, task_id: str, reason: str, error: dict[str, Any] | None = None, turn_count: int | None = None, solver_id: str | None = None) -> SessionRecord:
        return self._transition(task_id=task_id, status="failed", reason=reason, error=error, turn_count=turn_count, solver_id=solver_id, finished=True)

    def outcome(self, *, status: str, stop_reason: str = "", turn_count: int = 0, summary: str = "", evidence_artifact_ids: list[str] | None = None, error: dict[str, Any] | None = None) -> SessionOutcome:
        return SessionOutcome(status=status, stop_reason=stop_reason, turn_count=turn_count, summary=summary, evidence_artifact_ids=evidence_artifact_ids or [], error=error)

    def release_resources(
        self,
        *,
        task_id: str,
        solver_id: str | None,
        status: str,
        handlers: Any,
        executor: Any,
        mcp_manager: Any,
        close_shared_mcp: bool = True,
    ) -> None:
        """Release runtime-owned resources through the lifecycle boundary."""
        handlers.close()
        if status not in {"completed", "cancelled", "failed"}:
            return
        close_sessions = getattr(executor, "close_http_sessions", None)
        if callable(close_sessions):
            destroyed = close_sessions(task_id=task_id, solver_id=solver_id)
            with self.store.transaction():
                self.store.append_agent_event(
                    task_id,
                    "HTTP_SESSION_STATUS",
                    {"profile": "destroyed", "destroyed_origins": destroyed},
                    solver_id=solver_id,
                )
        if close_shared_mcp:
            mcp_manager.close()

    def stop(
        self,
        *,
        task_id: str,
        status: str,
        reason: str,
        solver_id: str | None = None,
        turn_count: int | None = None,
        summary: str = "",
        evidence_artifact_ids: list[str] | None = None,
        error: dict[str, Any] | None = None,
        details: dict[str, Any] | None = None,
    ) -> SessionRecord:
        if status == "completed":
            session = self.store.get_session(task_id)
            return self.complete(
                task_id=task_id,
                summary=summary or reason,
                evidence_artifact_ids=evidence_artifact_ids or [],
                turn_count=turn_count if turn_count is not None else session.turn_count if session else 0,
                reason=reason,
                solver_id=solver_id,
                details=details,
            )
        if status == "blocked":
            return self.block(task_id=task_id, reason=reason, turn_count=turn_count, solver_id=solver_id, error=error)
        if status == "failed":
            return self.fail(task_id=task_id, reason=reason, error=error, turn_count=turn_count, solver_id=solver_id)
        if status == "cancelled":
            return self.cancel(task_id=task_id, reason=reason)
        raise ValueError(f"unsupported terminal status: {status}")

    def apply(self, *, task_id: str, solver_id: str, outcome: SessionOutcome) -> SessionRecord:
        """Persist a runner outcome through the single lifecycle boundary."""
        return self.stop(
            task_id=task_id,
            status=outcome.status,
            reason=outcome.stop_reason,
            solver_id=solver_id,
            turn_count=outcome.turn_count,
            summary=outcome.summary,
            evidence_artifact_ids=outcome.evidence_artifact_ids,
            error=outcome.error,
            details=outcome.details,
        )

    def _transition(self, *, task_id: str, status: str, reason: str, solver_id: str | None = None, finished: bool = False, summary: str = "", evidence_artifact_ids: list[str] | None = None, error: dict[str, Any] | None = None, turn_count: int | None = None, details: dict[str, Any] | None = None) -> SessionRecord:
        with self.store.transaction():
            session = self.store.get_session(task_id)
            if session is None:
                raise KeyError(f"session not found: {task_id}")
            allowed = SESSION_TRANSITIONS.get(session.status, set())
            if status not in allowed:
                raise SessionTransitionError(session.status, status)
            update: dict[str, Any] = {"status": status, "stop_reason": reason}
            if turn_count is not None:
                update["turn_count"] = turn_count
            if finished or status in {"completed", "cancelled", "failed"}:
                update["finished_at"] = utc_now()
            if status == "running":
                update.setdefault("finished_at", None)
                if session.started_at is None:
                    update.setdefault("started_at", utc_now())
            if solver_id is not None:
                update["active_solver_id"] = solver_id
            session = self.store.update_session(task_id, **update)
            effective_solver_id = solver_id or session.active_solver_id
            challenge = self.store.get_challenge(task_id)
            if status == "running" and challenge is not None and challenge.status in {"unknown", "blocked"}:
                previous = challenge.status
                challenge = challenge.model_copy(
                    update={"status": "active", "status_reason": reason, "solved_at": None}
                )
                self.store.upsert_challenge(challenge)
                self.store.append_agent_event(
                    task_id,
                    "CHALLENGE_STATUS_CHANGED",
                    {"from": previous, "status": "active", "reason": reason},
                    solver_id=effective_solver_id,
                )
            if status == "running" and reason == "session_started":
                self.store.append_agent_event(
                    task_id,
                    "SESSION_STARTED",
                    {"max_turns": session.max_turns, "status": status},
                    solver_id=effective_solver_id,
                )
                self.store.append_agent_event(
                    task_id,
                    "AGENT_STARTED",
                    {"role": "main"},
                    solver_id=effective_solver_id,
                )
            if status == "completed":
                completion = {"reason": reason, "summary": summary, "evidence_artifact_ids": evidence_artifact_ids or [], **(details or {})}
                for claim in completion.get("claims") or []:
                    if not isinstance(claim, dict) or claim.get("kind") not in {"finding", "vulnerability"}:
                        continue
                    statement = str(claim.get("statement") or "").strip()
                    claim_evidence = [str(item) for item in claim.get("evidence_artifact_ids") or []]
                    evidence_id = next((
                        item for item in claim_evidence
                        if (artifact := self.store.get_artifact(item)) is not None and artifact.task_id == task_id
                    ), "")
                    if not statement:
                        continue
                    digest = hashlib.sha256(f"{task_id}\0{claim.get('kind')}\0{statement}".encode("utf-8")).hexdigest()[:16]
                    finding = CandidateFindingRecord(
                        id=f"finding_{digest}",
                        task_id=task_id,
                        title=statement[:255],
                        target=(self.store.get_task(task_id).default_action_target() if self.store.get_task(task_id) else task_id),
                        severity="medium" if claim.get("kind") == "vulnerability" else "info",
                        evidence_artifact_id=evidence_id or None,
                        evidence_excerpt=statement[:500],
                    )
                    self.store.add_candidate_finding(finding)
                    self.store.append_agent_event(
                        task_id,
                        "FINDING_CANDIDATE",
                        {
                            "finding_id": finding.id,
                            "title": finding.title,
                            "target": finding.target,
                            "severity": finding.severity,
                            "status": "candidate",
                            "evidence_artifact_id": evidence_id or None,
                        },
                        solver_id=solver_id,
                    )
                structured = completion.get("structured_result") if isinstance(completion.get("structured_result"), dict) else {}
                flag = str(structured.get("flag") or "").strip()
                proof_id = str(structured.get("proof_artifact_id") or "").strip()
                if flag and proof_id:
                    proof = self.store.get_artifact(proof_id)
                    if proof is None or proof.task_id != task_id:
                        raise ValueError("completion proof must be a task-owned Artifact")
                    self.store.add_flag(task_id, flag, proof_id)
                    self.store.append_agent_event(task_id, "FLAG_CONFIRMED", {"value": flag, "evidence_artifact_id": proof_id, "verification": structured.get("verification") or "completion_validator"}, solver_id=solver_id)
                    if challenge is not None:
                        challenge = challenge.model_copy(update={"status": "solved", "status_reason": "completion_validator", "completion_proof_artifact_id": proof_id, "solved_at": utc_now()})
                        self.store.upsert_challenge(challenge)
                        self.store.append_agent_event(task_id, "CHALLENGE_STATUS_CHANGED", {"status": "solved", "reason": "completion_validator", "completion_proof_artifact_id": proof_id}, solver_id=solver_id)
                self.store.append_agent_event(task_id, "FINISH_ACCEPTED", completion, solver_id=solver_id)
                self.store.append_agent_event(task_id, "AGENT_FINISHED", completion, solver_id=solver_id)
            if not (status == "running" and reason == "session_started"):
                self.store.append_agent_event(task_id, "SESSION_STOPPED" if status in {"completed", "cancelled", "failed", "blocked"} else "SESSION_CONTROLLED", {"action": status, "reason": reason, "status": status, "error": error}, solver_id=solver_id)
            return session
