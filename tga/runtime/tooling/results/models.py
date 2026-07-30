"""Execution-neutral results and bounded observations."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ExecutionError(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    message: str
    retryable: bool = False


class RawExecutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action_id: str
    status: Literal[
        "pending_approval", "succeeded", "failed", "blocked", "rejected",
        "expired", "cancelled",
    ]
    output: dict[str, Any] = Field(default_factory=dict)
    artifact_ids: list[str] = Field(default_factory=list, max_length=1_024)
    telemetry: dict[str, Any] = Field(default_factory=dict)
    error: ExecutionError | None = None


class ToolObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action_id: str
    summary: str = Field(default="", max_length=8_000)
    deterministic_facts: list[str] = Field(default_factory=list, max_length=256)
    leads: list[str] = Field(default_factory=list, max_length=256)
    candidate_evidence_claim_ids: list[str] = Field(default_factory=list, max_length=1_024)
    candidate_knowledge_ids: list[str] = Field(default_factory=list, max_length=1_024)


class ToolGatewayResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action_id: str | None = None
    status: str
    observation: ToolObservation | None = None
    artifact_ids: list[str] = Field(default_factory=list)
    error: ExecutionError | None = None
    model_payload: dict[str, Any] = Field(default_factory=dict)
    idempotent_replay: bool = False


__all__ = [
    "ExecutionError", "RawExecutionResult", "ToolGatewayResult", "ToolObservation",
]
