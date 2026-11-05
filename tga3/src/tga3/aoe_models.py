"""AOE question input and deterministic scheduling rules, separate from single-task policy."""

from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .domain import RunState, TaskRun


class AOEQuestionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    source_key: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=500)
    description: str = Field(min_length=1, max_length=100000)
    difficulty: str | float | None = None
    connection_info: str = Field(default="", max_length=20000)
    submission_instructions: str = Field(default="", max_length=20000)
    attachments: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("difficulty", mode="before")
    @classmethod
    def valid_difficulty(cls, value):
        if isinstance(value, bool) or isinstance(value, (float, int)) and not math.isfinite(value):
            raise ValueError("Difficulty must be an original label or finite numeric level")
        return value


def difficulty_order(value: Any) -> float:
    """Use supplied levels only; unknown labels remain unranked in source order."""
    if isinstance(value, (float, int)) and not isinstance(value, bool):
        return float(value)
    labels = {
        "very easy": 0,
        "beginner": 0,
        "入门": 0,
        "非常简单": 0,
        "easy": 1,
        "简单": 1,
        "初级": 1,
        "简单题": 1,
        "medium": 2,
        "normal": 2,
        "中等": 2,
        "普通": 2,
        "中级": 2,
        "hard": 3,
        "困难": 3,
        "高级": 3,
        "困难题": 3,
        "very hard": 4,
        "expert": 4,
        "insane": 4,
        "极难": 4,
        "专家": 4,
    }
    return float(labels.get(str(value).strip().lower(), math.inf))


def question_order(question: TaskRun) -> tuple[float, int, str]:
    return difficulty_order(question.metadata.get("difficulty")), question.metadata.get("ordinal", 0), str(question.id)


def active_attempts(question: TaskRun) -> list[dict]:
    return [attempt for attempt in question.metadata.get("attempts", []) if attempt.get("outcome") is None]


def attempted_by(question: TaskRun) -> set[str]:
    return {attempt["agent_id"] for attempt in question.metadata.get("attempts", []) if attempt["phase"] == "solo"}


def unfinished(questions: list[TaskRun]) -> list[TaskRun]:
    return [q for q in questions if q.metadata.get("status", "queued") not in {"solved", "abandoned"}]


def scout_ready(question: TaskRun) -> bool:
    # Old AOE runs had no explicit flag and left SCOUTING only after handoff.
    return question.metadata.get("scout_ready", question.state != RunState.SCOUTING)


def schedulable_questions(questions: list[TaskRun], *, collection_complete: bool = True) -> list[TaskRun]:
    """During collection solve easy questions; afterwards retain the existing policy."""
    remaining = unfinished(questions)
    if collection_complete:
        return remaining
    if all(math.isinf(difficulty_order(q.metadata.get("difficulty"))) for q in questions):
        return remaining
    return [q for q in remaining if difficulty_order(q.metadata.get("difficulty")) <= 1]


def overview_order(question: TaskRun) -> tuple[int, float, int, str]:
    solved = question.metadata.get("status") == "solved"
    timestamp = question.metadata.get("solved_at") if solved else None
    if timestamp is None and solved:
        timestamp = max((a.get("finished_at", 0) for a in question.metadata.get("attempts", [])
                         if a.get("outcome") == "solved"), default=question.updated_at.timestamp())
    timestamp = timestamp if solved else question.metadata.get("registered_at", question.created_at.timestamp())
    return (0 if solved else 1, -timestamp, -question.metadata.get("ordinal", 0), str(question.id))


def next_solo_question(questions: list[TaskRun], agent_id: str) -> TaskRun | None:
    eligible = [
        q
        for q in unfinished(questions)
        if scout_ready(q) and not active_attempts(q) and agent_id not in attempted_by(q)
    ]
    # Untouched by either Worker always precedes a peer's previous question, regardless of difficulty.
    return min(eligible, key=lambda q: (bool(attempted_by(q)), *question_order(q))) if eligible else None


def joint_ready(questions: list[TaskRun], worker_ids: tuple[str, ...]) -> bool:
    remaining = unfinished(questions)
    return bool(remaining) and all(
        scout_ready(q) and not active_attempts(q) and set(worker_ids) <= attempted_by(q) for q in remaining
    )


def next_joint_question(questions: list[TaskRun]) -> TaskRun | None:
    remaining = unfinished(questions)
    return min(remaining, key=question_order) if remaining else None
