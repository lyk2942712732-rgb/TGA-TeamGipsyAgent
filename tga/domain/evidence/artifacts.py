"""Immutable raw materials produced or collected during a task."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Artifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    task_id: str
    intent_id: str | None = None
    kind: str = Field(min_length=1, max_length=128)
    path: str = Field(min_length=1, max_length=4_096)
    sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    media_type: str | None = Field(default=None, max_length=255)
    tool: str | None = Field(default=None, max_length=255)
    target: str | None = Field(default=None, max_length=4_096)
    input_id: str | None = None
    created_at: str
    legacy_import: bool = False
    provenance: dict[str, Any] = Field(default_factory=dict)


__all__ = ["Artifact"]

