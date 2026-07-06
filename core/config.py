"""Configuration owned by the agent core."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class CoreConfig(BaseModel):
    """Small, explicit set of controls for the MVP graph."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_steps: int = Field(default=24, ge=1, le=200)
    repeat_failure_threshold: int = Field(default=3, ge=1, le=10)
    reflection_threshold: int = Field(default=2, ge=1, le=20)
    message_window: int = Field(default=24, ge=6, le=100)
    evidence_excerpt_chars: int = Field(default=1200, ge=200, le=8000)
    max_facts_in_prompt: int = Field(default=12, ge=1, le=50)
    max_failures_in_prompt: int = Field(default=10, ge=1, le=50)
    max_hypotheses_in_prompt: int = Field(default=8, ge=1, le=30)
    recursion_limit: int = Field(default=100, ge=10, le=1000)
    hidden_tool_names: frozenset[str] = frozenset({"submit_flag"})
    flag_patterns: tuple[str, ...] = (
        r"(?i)(?<![a-z0-9_])[a-z0-9_]*(?:flag|ctf)\{[^{}\r\n]{1,256}\}",
    )
