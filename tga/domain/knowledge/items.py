"""Authoritative knowledge items with explicit evidence and provenance."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator

from tga.domain.knowledge.scopes import (
    KnowledgeKind,
    KnowledgeScope,
    KnowledgeStatus,
    validate_scope_target,
)


class KnowledgeItem(BaseModel):
    model_config = {"extra": "forbid"}

    id: str
    task_id: str
    scope: KnowledgeScope
    target_id: str | None = None
    status: KnowledgeStatus = "candidate"
    kind: KnowledgeKind
    subject: str | None = Field(
        default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}$"
    )
    value: str | None = Field(default=None, max_length=8_000)
    content: str = Field(min_length=1, max_length=8_000)
    evidence_claim_ids: list[str] = Field(default_factory=list, max_length=256)
    source_hint_ids: list[str] = Field(default_factory=list, max_length=256)
    source_retrieval_run_ids: list[str] = Field(default_factory=list, max_length=256)
    human_source: str | None = Field(default=None, max_length=512)
    created_by_solver_id: str
    supersedes_id: str | None = None
    created_at: str
    updated_at: str | None = None
    legacy_import: bool = False
    provenance: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_semantics(self) -> "KnowledgeItem":
        validate_scope_target(self.scope, self.target_id)
        if self.supersedes_id == self.id:
            raise ValueError("KnowledgeItem cannot supersede itself")
        if self.value is not None and self.subject is None:
            raise ValueError("structured knowledge value requires subject")
        if self.status == "verified" and self.kind == "fact":
            if not self.evidence_claim_ids and not (self.human_source or "").strip():
                raise ValueError(
                    "verified factual knowledge requires an EvidenceClaim or explicit human source"
                )
        if (
            self.status == "verified"
            and self.source_retrieval_run_ids
            and not self.evidence_claim_ids
            and not (self.human_source or "").strip()
        ):
            raise ValueError(
                "retrieval output cannot directly become verified Knowledge"
            )
        for values, label in (
            (self.evidence_claim_ids, "evidence_claim_ids"),
            (self.source_hint_ids, "source_hint_ids"),
            (self.source_retrieval_run_ids, "source_retrieval_run_ids"),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{label} must be unique")
        return self


__all__ = ["KnowledgeItem"]
