"""Canonical structured memory for one CTF challenge."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import Field

from core.state.models import (
    AgentStatus,
    AttackPath,
    ChallengeContext,
    CoreRecord,
    CTFPhase,
    Fact,
    FailedAttempt,
    FlagCandidate,
    Hypothesis,
    HypothesisStatus,
    PathStatus,
    SubmissionStatus,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Blackboard(CoreRecord):
    """Source of truth injected into every model decision."""

    challenge: ChallengeContext
    phase: CTFPhase = CTFPhase.RECON

    confirmed_facts: list[Fact] = Field(default_factory=list)
    failed_attempts: list[FailedAttempt] = Field(default_factory=list)
    current_path: AttackPath | None = None
    hypotheses: list[Hypothesis] = Field(default_factory=list)

    flag_candidates: list[FlagCandidate] = Field(default_factory=list)
    awaiting_user_submission: bool = False

    step_count: int = 0
    no_progress_count: int = 0
    reflection_count: int = 0
    reflection_guidance: str = ""
    last_failure_signature: str = ""
    failure_streak: int = 0
    status: AgentStatus = AgentStatus.RUNNING

    @classmethod
    def create(
        cls,
        task: str,
        *,
        challenge_id: str = "",
        title: str = "",
        target: str = "",
    ) -> "Blackboard":
        return cls(
            challenge=ChallengeContext(
                challenge_id=challenge_id,
                title=title,
                task=task,
                target=target,
            )
        )

    def copy_for_update(self) -> "Blackboard":
        return self.model_copy(deep=True)

    def add_fact(
        self,
        *,
        content: str,
        source_tool: str,
        source_call_id: str,
        evidence: str,
    ) -> bool:
        key = (content.strip(), source_tool, source_call_id)
        if any((f.content.strip(), f.source_tool, f.source_call_id) == key for f in self.confirmed_facts):
            return False
        self.confirmed_facts.append(
            Fact(
                content=content.strip(),
                source_tool=source_tool,
                source_call_id=source_call_id,
                evidence=evidence,
            )
        )
        return True

    def set_path(self, name: str, goal: str = "", next_step: str = "") -> None:
        name = name.strip()
        if not name:
            return
        if self.current_path and self.current_path.name.casefold() == name.casefold():
            self.current_path.status = PathStatus.ACTIVE
            if goal:
                self.current_path.goal = goal.strip()
            if next_step:
                self.current_path.next_step = next_step.strip()
            return
        self.current_path = AttackPath(name=name, goal=goal.strip(), next_step=next_step.strip())

    def add_hypothesis(self, content: str, verification_action: str = "") -> Hypothesis | None:
        normalized = content.strip()
        if not normalized:
            return None
        for hypothesis in self.hypotheses:
            if hypothesis.content.casefold() == normalized.casefold():
                if verification_action:
                    hypothesis.verification_action = verification_action.strip()
                return hypothesis
        hypothesis = Hypothesis(
            content=normalized,
            verification_action=verification_action.strip(),
        )
        self.hypotheses.append(hypothesis)
        return hypothesis

    def update_hypothesis(self, hypothesis_id: str, status: HypothesisStatus) -> bool:
        for hypothesis in self.hypotheses:
            if hypothesis.id == hypothesis_id:
                hypothesis.status = status
                return True
        return False

    def record_failure(
        self,
        *,
        tool_name: str,
        normalized_args: dict[str, Any],
        call_fingerprint: str,
        result_fingerprint: str,
        result_summary: str,
        error_type: str,
    ) -> FailedAttempt:
        signature = f"{call_fingerprint}:{result_fingerprint}"
        if signature == self.last_failure_signature and self.failed_attempts:
            attempt = self.failed_attempts[-1]
            attempt.repeat_count += 1
            attempt.updated_at = _now()
            self.failure_streak += 1
        else:
            attempt = FailedAttempt(
                tool_name=tool_name,
                normalized_args=normalized_args,
                call_fingerprint=call_fingerprint,
                result_fingerprint=result_fingerprint,
                result_summary=result_summary,
                error_type=error_type,
            )
            self.failed_attempts.append(attempt)
            self.last_failure_signature = signature
            self.failure_streak = 1
        self.no_progress_count += 1
        return attempt

    def clear_failure_streak(self) -> None:
        self.last_failure_signature = ""
        self.failure_streak = 0
        self.no_progress_count = 0
        self.reflection_guidance = ""

    def block_current_path(self, reason: str) -> None:
        if not self.current_path:
            return
        self.current_path.status = PathStatus.BLOCKED
        self.current_path.blocked_reason = reason.strip()

    def add_tool_flag(
        self,
        *,
        value: str,
        source_tool: str,
        source_call_id: str,
        tool_evidence: str,
    ) -> FlagCandidate:
        normalized = value.strip()
        for candidate in reversed(self.flag_candidates):
            if (
                candidate.value == normalized
                and candidate.user_submission_status == SubmissionStatus.FAILED
            ):
                return candidate
        for candidate in self.flag_candidates:
            if candidate.value == normalized and candidate.source_call_id == source_call_id:
                return candidate
        candidate = FlagCandidate(
            value=normalized,
            source_tool=source_tool,
            source_call_id=source_call_id,
            tool_evidence=tool_evidence,
        )
        self.flag_candidates.append(candidate)
        return candidate

    def verify_llm_flag(self, value: str) -> FlagCandidate | None:
        normalized = value.strip()
        for candidate in reversed(self.flag_candidates):
            if (
                candidate.value == normalized
                and candidate.user_submission_status != SubmissionStatus.FAILED
            ):
                candidate.llm_claimed = True
                candidate.evidence_verified = True
                self.awaiting_user_submission = True
                self.phase = CTFPhase.FLAG_REVIEW
                self.status = AgentStatus.AWAITING_USER_SUBMISSION
                return candidate
        return None

    def pending_user_flag(self) -> FlagCandidate | None:
        for candidate in reversed(self.flag_candidates):
            if (
                candidate.evidence_verified
                and candidate.user_submission_status == SubmissionStatus.PENDING
            ):
                return candidate
        return None

    def mark_user_submission(self, success: bool) -> FlagCandidate | None:
        candidate = self.pending_user_flag()
        if not candidate:
            return None
        self.awaiting_user_submission = False
        if success:
            candidate.user_submission_status = SubmissionStatus.SUCCESS
            self.phase = CTFPhase.COMPLETED
            self.status = AgentStatus.COMPLETED
            if self.current_path:
                self.current_path.status = PathStatus.SUCCEEDED
        else:
            candidate.user_submission_status = SubmissionStatus.FAILED
            self.phase = CTFPhase.EXPLOITATION
            self.status = AgentStatus.RUNNING
            self.no_progress_count += 1
        return candidate
