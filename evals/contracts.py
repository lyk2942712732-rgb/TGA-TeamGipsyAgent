"""Public contracts for deterministic v2 challenge evaluations.

The models in this module are safe to serialize into prompts, reports, and UI
fixtures. Private flags and oracle callbacks deliberately live elsewhere.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ChallengeContract(BaseModel):
    case_id: str = Field(pattern=r"^W[1-6]$")
    title: str
    theme: Literal["hidden-route", "form-post", "signed-header", "idor", "code-leak", "binary-decode"]
    task_mode: Literal["ctf", "penetration_test", "incident_response", "vulnerability_research", "reverse_engineering"] = "ctf"
    goal: str
    flag_format: str = r"flag\{w[1-6]_[a-f0-9]{16}\}"
    solver_roles: list[str] = Field(default_factory=lambda: ["recon", "targeted"])
    required_capabilities: list[str]
    max_actions: int = Field(default=8, ge=1, le=32)
    manager_semantic_attempt_budget: int = Field(default=3, ge=1, le=3)
    executor_semantic_attempt_budget: int = Field(default=3, ge=1, le=3)
    required_events: list[str] = Field(
        default_factory=lambda: [
            "HYPOTHESIS_CREATED",
            "ACTION_FINISHED",
            "FLAG_CONFIRMED",
        ]
    )


class EvalResult(BaseModel):
    case_id: str
    outcome: Literal["solved", "blocked", "failed"]
    flag_confirmed: bool
    # Current project calibration explicitly removed challenge submission.
    submission_status: Literal["not_required"] = "not_required"
    artifact_provenance_ok: bool
    action_count: int = Field(ge=0)
    semantic_repeat_count: int = Field(ge=0)
    scope_rejection_count: int = Field(ge=0)
    solver_roles: list[str]
    coverage_gaps: list[str]
    failure_domain: Literal["none", "model", "manager", "executor", "bridge", "scope", "ui_sse", "fixture", "unknown"]
    checks: dict[str, bool]
    duration_ms: int = Field(ge=0)
    hint_utilization: float = Field(ge=0, le=1)
    hint_to_first_strategy_turns: int | None = Field(default=None, ge=0)
    hint_to_flag_actions: int | None = Field(default=None, ge=0)
    hint_to_flag_turns: int | None = Field(default=None, ge=0)
    hint_to_flag_wall_ms: int | None = Field(default=None, ge=0)
    duplicate_action_rate: float = Field(ge=0, le=1)
    consecutive_failures_without_new_hypothesis: int = Field(ge=0)
    latest_context_chars: int = Field(ge=0)
    artifact_retrieval_hits: int = Field(ge=0)
    observer_correction_adoption_rate: float = Field(ge=0, le=1)
    observer_invalid_interruption_rate: float = Field(ge=0, le=1)
    flag_artifact_provenance_completeness: float = Field(ge=0, le=1)
    unaudited_persistent_state_changes: int = Field(ge=0)
    replay_path: str | None = None
    passed: bool
