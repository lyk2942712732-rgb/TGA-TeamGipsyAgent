"""Serializable domain records stored on the blackboard."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:10]}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CTFPhase(str, Enum):
    RECON = "information_gathering"
    ANALYSIS = "vulnerability_analysis"
    EXPLOITATION = "exploitation"
    FLAG_REVIEW = "flag_review"
    COMPLETED = "completed"


class AgentStatus(str, Enum):
    RUNNING = "running"
    AWAITING_USER_SUBMISSION = "awaiting_user_submission"
    COMPLETED = "completed"
    FAILED = "failed"


class PathStatus(str, Enum):
    ACTIVE = "active"
    BLOCKED = "blocked"
    SUCCEEDED = "succeeded"
    ABANDONED = "abandoned"


class HypothesisStatus(str, Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class SubmissionStatus(str, Enum):
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"


class CoreRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class ChallengeContext(CoreRecord):
    challenge_id: str = ""
    title: str = ""
    task: str
    target: str = ""


class Fact(CoreRecord):
    id: str = Field(default_factory=lambda: _new_id("fact"))
    content: str
    source_tool: str
    source_call_id: str
    evidence: str
    created_at: str = Field(default_factory=_now)


class FailedAttempt(CoreRecord):
    id: str = Field(default_factory=lambda: _new_id("attempt"))
    tool_name: str
    normalized_args: dict[str, Any]
    call_fingerprint: str
    result_fingerprint: str
    result_summary: str
    error_type: str = "tool_error"
    repeat_count: int = Field(default=1, ge=1)
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)


class AttackPath(CoreRecord):
    id: str = Field(default_factory=lambda: _new_id("path"))
    name: str
    goal: str = ""
    next_step: str = ""
    status: PathStatus = PathStatus.ACTIVE
    blocked_reason: str = ""
    created_at: str = Field(default_factory=_now)


class Hypothesis(CoreRecord):
    id: str = Field(default_factory=lambda: _new_id("hyp"))
    content: str
    verification_action: str = ""
    status: HypothesisStatus = HypothesisStatus.PENDING
    created_at: str = Field(default_factory=_now)


class FlagCandidate(CoreRecord):
    id: str = Field(default_factory=lambda: _new_id("flag"))
    value: str
    source_tool: str
    source_call_id: str
    tool_evidence: str
    llm_claimed: bool = False
    evidence_verified: bool = False
    user_submission_status: SubmissionStatus = SubmissionStatus.PENDING
    created_at: str = Field(default_factory=_now)
