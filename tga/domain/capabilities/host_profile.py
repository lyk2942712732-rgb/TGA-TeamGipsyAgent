"""Reusable role-oriented Host capability profiles."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class HostCapabilityProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    capability_ids: tuple[str, ...] = Field(min_length=1, max_length=64)

    @field_validator("capability_ids")
    @classmethod
    def unique_capabilities(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)):
            raise ValueError("Host capability profile entries must be unique")
        return values


__all__ = ["HostCapabilityProfile"]
