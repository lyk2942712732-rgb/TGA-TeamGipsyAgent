"""Solver-specific Host overrides and optional Kali binding."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


KaliCapability = Literal["kali.exec", "kali.session"]


class HostCapabilityOverrides(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    add: tuple[str, ...] = Field(default_factory=tuple, max_length=64)
    remove: tuple[str, ...] = Field(default_factory=tuple, max_length=64)

    @model_validator(mode="after")
    def validate_overrides(self) -> "HostCapabilityOverrides":
        if len(self.add) != len(set(self.add)) or len(self.remove) != len(set(self.remove)):
            raise ValueError("Host capability overrides must be unique")
        overlap = set(self.add).intersection(self.remove)
        if overlap:
            raise ValueError(f"Host capability overrides conflict: {sorted(overlap)}")
        return self


class SolverKaliBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    profile_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
    capabilities: tuple[KaliCapability, ...] = Field(min_length=1, max_length=2)

    @field_validator("capabilities")
    @classmethod
    def validate_unique_capabilities(
        cls, value: tuple[KaliCapability, ...]
    ) -> tuple[KaliCapability, ...]:
        if len(value) != len(set(value)):
            raise ValueError("Kali capabilities must be unique")
        return value


__all__ = ["HostCapabilityOverrides", "KaliCapability", "SolverKaliBinding"]
