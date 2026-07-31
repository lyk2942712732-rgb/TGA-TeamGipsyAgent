"""Historical schema-v5 evidence records used only by offline migration."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class LegacyArtifactRecord(BaseModel):
    id: str
    task_id: str
    intent_id: str | None = None
    kind: Literal[
        "stdout", "stderr", "tool_output", "http_response", "http_body", "file", "report",
    ]
    path: str
    sha256: str
    tool: str | None = None
    target: str | None = None
    input_id: str | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class LegacyFinding(BaseModel):
    id: str
    task_id: str
    title: str
    target: str
    severity: Literal["info", "low", "medium", "high", "critical"]
    status: Literal["candidate", "confirmed", "rejected"] = "candidate"
    evidence_artifact_id: str | None = None
    evidence_excerpt: str | None = None
    reproduction_steps: list[str] = Field(default_factory=list)
    remediation: str | None = None
    tool: str | None = None


__all__ = ["LegacyArtifactRecord", "LegacyFinding"]
