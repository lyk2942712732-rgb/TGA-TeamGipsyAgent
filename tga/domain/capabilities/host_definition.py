"""Host-owned capabilities exposed to Solvers."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

HostCapabilityCategory = Literal[
    "orchestration",
    "task_input",
    "artifact",
    "evidence",
    "knowledge",
    "retrieval",
    "result",
    "review",
    "reporting",
]
HostCapabilityRole = Literal["supervisor", "worker", "reviewer", "reporter"]


class HostCapabilityDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,127}$")
    display_name: str = Field(min_length=1, max_length=128)
    category: HostCapabilityCategory
    description: str = Field(min_length=1, max_length=1_000)
    allowed_roles: tuple[HostCapabilityRole, ...] = Field(min_length=1, max_length=4)
    risk: Literal["passive", "active"]
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    handler_key: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,255}$")

    @field_validator("allowed_roles")
    @classmethod
    def unique_roles(
        cls, values: tuple[HostCapabilityRole, ...]
    ) -> tuple[HostCapabilityRole, ...]:
        if len(values) != len(set(values)):
            raise ValueError("Host capability roles must be unique")
        return values


__all__ = ["HostCapabilityCategory", "HostCapabilityDefinition"]
