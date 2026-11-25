"""Typed Pydantic views and mutation helpers for task and question metadata dictionaries.

Eliminates scattered raw-string metadata access and prevents key-presence vs None drift
(e.g., `'swarm' in metadata` vs `metadata.get('swarm') is None`).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .domain import RunState, TaskRun


class AOEAttemptRecord(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    agent_id: str
    phase: Literal["solo", "joint", "swarm", "reinforcement"]
    claimed_at: float | None = None
    started_at: float | None = None
    deadline: float | None = None
    outcome: Literal["solved", "timeout", "interrupted", "error"] | None = None
    finished_at: float | None = None
    cli_session_id: str | None = None
    superseded: bool = False
    first_attempt_id: str | None = None
    success_credit: bool | None = None
    completion_hold_action: str | None = None
    manual_extension_seconds: int = 0
    manual_extension_count: int = 0
    pauses: list[dict[str, Any]] = Field(default_factory=list)


class AOESwarmState(BaseModel):
    model_config = ConfigDict(extra="allow")
    entered_at: float
    started_at: float | None = None
    deadline: float | None = None


class AOERescueState(BaseModel):
    model_config = ConfigDict(extra="allow")
    state: Literal["seeking", "reinforced", "closed"]
    requested_at: float
    threshold_at: float


class AOEReinforcementState(BaseModel):
    model_config = ConfigDict(extra="allow")
    agent_id: str
    joined_at: float
    first_attempt_id: str


class AOEQuestionMeta(BaseModel):
    model_config = ConfigDict(extra="allow")
    source_key: str = ""
    description: str = ""
    difficulty: str | float | None = None
    category: str = ""
    tags: list[str] = Field(default_factory=list)
    connection_info: str = ""
    submission_instructions: str = ""
    flag_format: str = ""
    collection_notes: str = ""
    pre_solved: bool = False
    remote_attachments: list[dict[str, Any]] = Field(default_factory=list)
    status: str = "queued"
    registered_at: float | None = None
    solved_at: float | None = None
    registration_complete: bool = False
    scout_ready: bool = True
    scout_phase: str = "completed"
    produce_report: bool = True
    aoe_auto_submit_flag: bool = True
    knowledge_source: str = "online"
    ordinal: int = 0
    flag: str | None = None
    solver_agent_id: str | None = None
    finish_reason: str | None = None
    swarm: AOESwarmState | None = None
    rescue: AOERescueState | None = None
    reinforcement: AOEReinforcementState | None = None
    attempts: list[AOEAttemptRecord] = Field(default_factory=list)

    @classmethod
    def from_task(cls, question: TaskRun) -> AOEQuestionMeta:
        return cls.model_validate(question.metadata)

    def active_attempts(self) -> list[AOEAttemptRecord]:
        return [attempt for attempt in self.attempts if attempt.outcome is None]

    def report_state(self, task_state: RunState) -> str:
        if self.pre_solved:
            return "pre_solved"
        if self.finish_reason == "platform_solved":
            return "platform_solved"
        if not self.produce_report:
            return "disabled"
        if task_state == RunState.COMPLETED:
            return "completed"
        if self.status in {"solved", "abandoned"} and task_state == RunState.FAILED:
            return "failed"
        if task_state in {RunState.FINALIZING, RunState.REPORTING}:
            return "generating"
        return "pending"


class AOEParentMeta(BaseModel):
    model_config = ConfigDict(extra="allow")
    aoe_phase: str = "collecting"
    aoe_scout_mode: str = "collection_only"
    aoe_auto_submit_flag: bool = True
    aoe_attempt_seconds: int = 1200
    aoe_attempt_budgets: dict[str, int] = Field(default_factory=dict)
    collection_complete: bool = False
    collection_status: str = "collecting"
    collection_warnings: list[str] = Field(default_factory=list)
    scout_complete: bool = False
    knowledge_source: str = "online"
    produce_report: bool = True
    restart_count: int = 0

    @classmethod
    def from_task(cls, parent: TaskRun) -> AOEParentMeta:
        return cls.model_validate(parent.metadata)

    def budgets(self) -> dict[str, int]:
        return {
            stage: self.aoe_attempt_budgets.get(stage, self.aoe_attempt_seconds)
            for stage in ("first", "second", "joint")
        }


ROUND_TRANSIENT_KEYS = ("swarm", "rescue", "reinforcement", "finish_reason")


def clear_question_round_state(
    metadata: dict[str, Any],
    *,
    supersede_all: bool = False,
    supersede_interrupted: bool = False,
) -> dict[str, Any]:
    """Return a clean copy of question metadata ready to re-enter the scheduling queue."""
    cleaned = dict(metadata)
    for key in ROUND_TRANSIENT_KEYS:
        cleaned.pop(key, None)
    attempts: list[dict[str, Any]] = []
    for attempt in cleaned.get("attempts", []):
        if supersede_all:
            attempts.append({**attempt, "outcome": attempt.get("outcome") or "interrupted", "superseded": True})
        elif supersede_interrupted and (attempt.get("outcome") is None or attempt.get("outcome") == "interrupted"):
            attempts.append({**attempt, "outcome": "interrupted", "superseded": True})
        else:
            attempts.append(attempt)
    cleaned["attempts"] = attempts
    cleaned["status"] = "queued"
    if cleaned.get("registration_complete", True):
        cleaned["scout_ready"] = True
        cleaned["scout_phase"] = "completed"
    return cleaned


__all__ = [
    "AOEAttemptRecord",
    "AOEParentMeta",
    "AOEQuestionMeta",
    "AOEReinforcementState",
    "AOERescueState",
    "AOESwarmState",
    "ROUND_TRANSIENT_KEYS",
    "clear_question_round_state",
]
