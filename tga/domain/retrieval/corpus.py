"""Scope-neutral corpus ownership, knowledge bases, sources, and policy."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


OwnerScopeName = Literal["global", "workspace", "task", "solver"]
TrustLevel = Literal["authoritative", "trusted", "unverified"]
CorpusSourceKind = Literal[
    "documentation", "code_repository", "knowledge_base", "previous_task",
    "uploaded_file", "web_reference",
]
RetrievalChannel = Literal["skill", "reference", "task_artifact"]


class OwnerScope(BaseModel):
    """Typed owner union; task identity is present only where semantically needed."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scope: OwnerScopeName
    workspace_id: str | None = Field(default=None, min_length=1, max_length=255)
    task_id: str | None = Field(default=None, min_length=1, max_length=128)
    solver_id: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_owner(self) -> "OwnerScope":
        if self.scope == "global":
            if any((self.workspace_id, self.task_id, self.solver_id)):
                raise ValueError("global owner cannot carry narrower owner ids")
        elif self.scope == "workspace":
            if not self.workspace_id or self.task_id or self.solver_id:
                raise ValueError("workspace owner requires only workspace_id")
        elif self.scope == "task":
            if not self.task_id or self.solver_id:
                raise ValueError("task owner requires task_id and no solver_id")
        elif not self.task_id or not self.solver_id:
            raise ValueError("solver owner requires task_id and solver_id")
        return self

    @property
    def owner_id(self) -> str:
        return self.solver_id or self.task_id or self.workspace_id or "global"


class KnowledgeBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=255)
    name: str = Field(min_length=1, max_length=255)
    owner: OwnerScope
    description: str = Field(default="", max_length=4_000)
    status: Literal["active", "archived"] = "active"
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str | None = None


class CorpusSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=255)
    knowledge_base_id: str = Field(min_length=1, max_length=255)
    name: str = Field(min_length=1, max_length=255)
    kind: CorpusSourceKind
    channel: RetrievalChannel
    owner: OwnerScope
    trust_level: TrustLevel
    canonical_uri: str | None = Field(default=None, max_length=4_096)
    status: Literal["active", "failed", "archived"] = "active"
    error: str | None = Field(default=None, max_length=4_000)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str | None = None


class RetrievalPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    allowed_knowledge_base_ids: tuple[str, ...] | None = None
    allowed_source_ids: tuple[str, ...] | None = None
    allowed_source_kinds: tuple[CorpusSourceKind, ...] | None = (
        "documentation", "code_repository", "knowledge_base",
        "uploaded_file", "web_reference",
    )
    allowed_trust_levels: tuple[TrustLevel, ...] = (
        "authoritative", "trusted",
    )
    allowed_owner_scopes: tuple[OwnerScopeName, ...] = ("task", "solver")
    task_artifact_access: bool = False
    cross_solver_access: bool = False
    max_results: int = Field(default=6, ge=1, le=100)
    max_context_tokens: int = Field(default=2_048, ge=1, le=100_000)

    @model_validator(mode="after")
    def validate_unique_filters(self) -> "RetrievalPolicy":
        for name in (
            "allowed_knowledge_base_ids", "allowed_source_ids",
            "allowed_source_kinds", "allowed_trust_levels", "allowed_owner_scopes",
        ):
            values = getattr(self, name)
            if values is not None and len(values) != len(set(values)):
                raise ValueError(f"{name} must be unique")
        return self


__all__ = [
    "CorpusSource", "CorpusSourceKind", "KnowledgeBase", "OwnerScope",
    "OwnerScopeName", "RetrievalChannel", "RetrievalPolicy", "TrustLevel",
]
