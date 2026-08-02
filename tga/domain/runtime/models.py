"""Durable schema-v6 runtime lifecycle records."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


SessionStatus = Literal[
    "created",
    "running",
    "paused",
    "awaiting_approval",
    "blocked",
    "completed",
    "failed",
    "cancelled",
]
ChallengeStatus = Literal["unknown", "active", "solved", "blocked", "expired"]


class SessionRecord(BaseModel):
    """Task-level lifecycle only.

    Multi-Solver execution state is authoritative in TaskOrchestratorState,
    SolverInstance and SolverRun.  A Task lifecycle never names one active
    Solver.
    """

    task_id: str
    schema_version: int = 6
    status: SessionStatus = "created"
    turn_count: int = 0
    max_turns: int = 48
    started_at: str | None = None
    finished_at: str | None = None
    stop_reason: str = ""
    workspace_path: str = ""
    mcp_catalog_version: str = ""


class ContextMetric(BaseModel):
    task_id: str
    solver_id: str
    turn: int = Field(ge=0)
    audit_message_count: int = Field(ge=0)
    working_message_count: int = Field(ge=0)
    working_chars: int = Field(ge=0)
    summary_hits: int = Field(default=0, ge=0)
    artifact_retrievals: int = Field(default=0, ge=0)
    retrieval_runs: int = Field(default=0, ge=0)
    retrieved_context_tokens: int = Field(default=0, ge=0)
    retrieval_failures: int = Field(default=0, ge=0)
    provider_input_tokens: int | None = Field(default=None, ge=0)
    provider_output_tokens: int | None = Field(default=None, ge=0)
    created_at: str


class ChallengeContract(BaseModel):
    """Durable completion state for an authorized challenge."""

    task_id: str
    entry_url: str | None = None
    allowed_origins: list[str]
    status: ChallengeStatus = "unknown"
    flag_format: str | None = None
    completion_proof_artifact_id: str | None = None
    status_reason: str = ""
    solved_at: str | None = None


__all__ = [
    "ChallengeContract",
    "ChallengeStatus",
    "ContextMetric",
    "SessionRecord",
    "SessionStatus",
]
