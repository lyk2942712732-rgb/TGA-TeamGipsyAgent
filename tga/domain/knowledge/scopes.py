"""Knowledge scope, kind and review-state vocabulary."""

from typing import Literal


KnowledgeScope = Literal["solver", "intent", "task"]
KnowledgeStatus = Literal["candidate", "verified", "rejected", "superseded"]
KnowledgeKind = Literal["fact", "constraint", "decision", "failure_boundary", "hypothesis"]


def validate_scope_target(scope: KnowledgeScope, target_id: str | None) -> None:
    if scope == "task" and target_id is not None:
        raise ValueError("task-scoped knowledge must not have target_id")
    if scope != "task" and not target_id:
        raise ValueError(f"{scope}-scoped knowledge requires target_id")


__all__ = ["KnowledgeKind", "KnowledgeScope", "KnowledgeStatus", "validate_scope_target"]

