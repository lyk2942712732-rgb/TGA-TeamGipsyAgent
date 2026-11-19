"""AOE question input and deterministic scheduling rules, separate from single-task policy."""

from __future__ import annotations

import math
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

from .domain import RunState, TaskRun
from .submissions import SubmissionSpec
from .target_instances import TargetSpec


class AttachmentDownloadSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    url: str = Field(min_length=1, max_length=4000)
    filename: str = Field(default="", max_length=255)
    method: Literal["GET", "POST"] = "GET"
    headers: dict[str, str] = Field(default_factory=dict)
    params: dict[str, str] = Field(default_factory=dict)
    body: Any = None

    @model_validator(mode="after")
    def http_url(self):
        parsed = urlsplit(self.url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Remote attachment must be an HTTP(S) URL")
        return self


class AOEQuestionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, populate_by_name=True)
    source_key: str = Field(min_length=1, max_length=200)
    title: str = Field(default="", max_length=500)
    description: str = Field(default="", max_length=100000)
    difficulty: str | float | None = None
    category: str = Field(default="", max_length=200)
    tags: list[str] = Field(default_factory=list, max_length=50)
    connection_info: str = Field(default="", max_length=20000)
    submission_instructions: str = Field(default="", max_length=20000)
    flag_format: str = Field(default="", max_length=1000)
    collection_notes: str = Field(default="", max_length=20000)
    already_solved: bool = False
    solved_flag: str | None = Field(default=None, max_length=4096)
    attachments: list[str | AttachmentDownloadSpec] = Field(default_factory=list, max_length=100)
    target: TargetSpec = Field(default_factory=lambda: TargetSpec(mode="unknown"))
    submission: SubmissionSpec | None = None
    # Exact raw source record is file-backed in source.json; never copy a second blob into SQL.
    source: dict[str, Any] | None = Field(default=None, validation_alias=AliasChoices("source", "raw_source"))

    @field_validator("difficulty", mode="before")
    @classmethod
    def valid_difficulty(cls, value):
        if isinstance(value, bool) or isinstance(value, (float, int)) and not math.isfinite(value):
            raise ValueError("Difficulty must be an original label or finite numeric level")
        return value

    @field_validator("attachments", mode="before")
    @classmethod
    def normalize_attachments(cls, value):
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("attachments must be a list of paths, URLs, or download specs")
        normalized: list[Any] = []
        for item in value:
            if isinstance(item, str):
                text = item.strip()
                if not text:
                    continue
                if text.startswith(("http://", "https://")):
                    normalized.append(AttachmentDownloadSpec(url=text))
                else:
                    normalized.append(text)
            elif isinstance(item, dict):
                normalized.append(AttachmentDownloadSpec.model_validate(item))
            elif isinstance(item, AttachmentDownloadSpec):
                normalized.append(item)
            else:
                raise ValueError("Each attachment must be a local path, HTTP(S) URL, or download spec object")
        return normalized

    @field_validator("tags", mode="before")
    @classmethod
    def clean_tags(cls, value):
        if value is None:
            return []
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            raise ValueError("tags must be a list of strings")
        seen: list[str] = []
        for item in value:
            text = str(item).strip()
            if text and text not in seen:
                seen.append(text)
        return seen

    @model_validator(mode="after")
    def require_content_and_title(self):
        if not self.title:
            self.title = self.source_key
        if not self.already_solved and not self.description and not self.attachments:
            raise ValueError("Each unsolved question requires a non-empty description or at least one attachment")
        return self

    def split_attachments(self) -> tuple[list[str], list[AttachmentDownloadSpec]]:
        local: list[str] = []
        remote: list[AttachmentDownloadSpec] = []
        for item in self.attachments:
            if isinstance(item, AttachmentDownloadSpec):
                remote.append(item)
            else:
                local.append(item)
        return local, remote


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
    return {attempt["agent_id"] for attempt in question.metadata.get("attempts", [])
            if attempt["phase"] in {"solo", "reinforcement"}}


def solo_attempts(question: TaskRun) -> list[dict]:
    """Reinforcement counts as participation in round one, not a second round."""
    return [a for a in question.metadata.get('attempts', []) if a['phase'] == 'solo']


def difficulty_key(value: Any) -> str | None:
    if value is None or value == '':
        return None
    rank = difficulty_order(value)
    return f'level:{rank}' if math.isfinite(rank) else f'label:{str(value).strip().lower()}'


def first_success_statistics(questions: list[TaskRun]) -> list[dict]:
    groups = {}
    for question in questions:
        rounds = solo_attempts(question)
        difficulty = question.metadata.get('difficulty')
        key = difficulty_key(difficulty)
        if question.metadata.get('status') != 'solved' or not rounds or key is None:
            continue
        first = rounds[0]
        for attempt in question.metadata.get('attempts', []):
            if not (attempt['id'] == first['id'] or attempt.get('first_attempt_id') == first['id']):
                continue
            start, finish = attempt.get('started_at'), attempt.get('finished_at')
            default_credit = (question.metadata.get('solver_agent_id') == attempt['agent_id']
                              if attempt['phase'] == 'reinforcement' else attempt.get('outcome') == 'solved')
            credited = attempt.get('success_credit', default_credit)
            if not credited or start is None or finish is None or finish < start:
                continue
            if not math.isfinite(start) or not math.isfinite(finish):
                continue
            group = groups.setdefault((attempt['agent_id'], key), {
                'agent_id': attempt['agent_id'], 'difficulty': difficulty, 'difficulty_key': key,
                'solved_count': 0, 'total_seconds': 0})
            group['solved_count'] += 1
            group['total_seconds'] += finish - start
    return [{**group, 'average_seconds': group['total_seconds'] / group['solved_count']}
            for _, group in sorted(groups.items())]


def rescue_after(question: TaskRun, statistics: list[dict]) -> float | None:
    rounds = solo_attempts(question)
    if len(rounds) != 1 or question.metadata.get('swarm') or question.metadata.get('reinforcement'):
        return None
    first = rounds[0]
    if first.get('outcome') is not None or first.get('started_at') is None:
        return None
    baseline = next((row for row in statistics if row['agent_id'] == first['agent_id']
                     and row['difficulty_key'] == difficulty_key(question.metadata.get('difficulty'))), None)
    if baseline is None:
        return None
    return first['started_at'] + max(600, baseline['average_seconds'] + 180)


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
    pre_solved = bool(question.metadata.get("pre_solved"))
    timestamp = question.metadata.get("solved_at") if solved else None
    if timestamp is None and solved:
        timestamp = max((a.get("finished_at", 0) for a in question.metadata.get("attempts", [])
                         if a.get("outcome") == "solved"), default=question.updated_at.timestamp())
    timestamp = timestamp if solved else question.metadata.get("registered_at", question.created_at.timestamp())
    priority = (
        0 if question.metadata.get("status") == "running"
        else 2 if (solved and pre_solved)
        else 1 if solved
        else 3
    )
    return (priority, -timestamp, -question.metadata.get("ordinal", 0), str(question.id))


def attempt_budgets(metadata: dict) -> dict[str, int]:
    """New per-stage budgets, preserving the uniform budget of historical tasks."""
    legacy = metadata.get("aoe_attempt_seconds", 1200)
    configured = metadata.get("aoe_attempt_budgets", {})
    return {stage: configured.get(stage, legacy) for stage in ("first", "second", "joint")}


def attempt_budget_seconds(metadata: dict, attempts: list[dict], current: dict) -> int:
    if current["phase"] in {"joint", "swarm"}:
        stage = "joint"
    elif current['phase'] == 'reinforcement':
        stage = 'first'
    else:
        solo = [attempt for attempt in attempts if attempt["phase"] == "solo"]
        stage = "first" if solo[0]["id"] == current["id"] else "second"
    return attempt_budgets(metadata)[stage]


def next_solo_question(questions: list[TaskRun], agent_id: str) -> TaskRun | None:
    eligible = [
        q
        for q in unfinished(questions)
        if scout_ready(q) and not active_attempts(q) and agent_id not in attempted_by(q)
        and len(solo_attempts(q)) < 2
    ]
    # Untouched by either Worker always precedes a peer's previous question, regardless of difficulty.
    return min(eligible, key=lambda q: (len(attempted_by(q)), *question_order(q))) if eligible else None


def joint_ready(questions: list[TaskRun], worker_ids: tuple[str, ...]) -> bool:
    remaining = unfinished(questions)
    return bool(remaining) and all(
        scout_ready(q) and not active_attempts(q) and len(solo_attempts(q)) >= 2 for q in remaining
    )


def next_joint_question(questions: list[TaskRun]) -> TaskRun | None:
    remaining = unfinished(questions)
    return min(remaining, key=question_order) if remaining else None


def joint_workers(question: TaskRun, worker_ids: tuple[str, ...]) -> tuple[str, ...]:
    # Historical two-Worker batches retain their original collaboration policy.
    if len(worker_ids) == 2:
        return worker_ids
    reinforcement = question.metadata.get('reinforcement')
    if reinforcement:
        return (reinforcement['agent_id'], *(agent for agent in worker_ids if agent not in attempted_by(question)))
    return tuple(agent for agent in worker_ids if agent not in attempted_by(question))
