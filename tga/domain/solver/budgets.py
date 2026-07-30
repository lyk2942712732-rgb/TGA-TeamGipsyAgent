"""Separate solver budget limits and measured usage."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SolverBudget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_turns: int = Field(ge=1, le=100_000)
    max_input_tokens: int = Field(ge=1, le=100_000_000)
    max_output_tokens: int = Field(ge=1, le=100_000_000)
    max_tool_calls: int = Field(ge=0, le=1_000_000)
    max_artifacts: int = Field(ge=0, le=1_000_000)
    deadline: str | None = None


class SolverBudgetUsage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    turns: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    artifacts: int = Field(default=0, ge=0)

    def within(self, budget: SolverBudget) -> bool:
        return (
            self.turns <= budget.max_turns
            and self.input_tokens <= budget.max_input_tokens
            and self.output_tokens <= budget.max_output_tokens
            and self.tool_calls <= budget.max_tool_calls
            and self.artifacts <= budget.max_artifacts
        )


__all__ = ["SolverBudget", "SolverBudgetUsage"]

