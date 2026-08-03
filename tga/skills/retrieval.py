"""Retrieval port and local-registry adapter for Skill documents."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, Sequence

from tga.modes import TaskMode
from tga.skills.loader import Skill
from tga.skills.registry import SkillRegistry


@dataclass(frozen=True)
class SkillRetrievalQuery:
    mode: TaskMode
    text: str
    tags: tuple[str, ...] = ()
    capability_requirements: tuple[str, ...] = ()
    limit: int = 32


@dataclass(frozen=True)
class RetrievedSkill:
    skill: Skill
    origin: str
    retrieval_score: int = 0
    retrieval_reasons: tuple[str, ...] = field(default_factory=tuple)


class SkillRetriever(Protocol):
    """Replaceable candidate source; a vector or RAG adapter can implement it."""

    retriever_id: str

    def retrieve(self, query: SkillRetrievalQuery) -> Sequence[RetrievedSkill]: ...


class RegistrySkillRetriever:
    """Deterministic adapter backed by packaged and custom Markdown Skills."""

    retriever_id = "local-registry-v1"

    def __init__(self, registry: SkillRegistry | None = None) -> None:
        self.registry = registry or SkillRegistry()

    def retrieve(self, query: SkillRetrievalQuery) -> Sequence[RetrievedSkill]:
        requested = set(query.tags)
        values: list[RetrievedSkill] = []
        for skill, origin in self.registry.compatible(query.mode):
            matched = sorted(requested.intersection(skill.tags))
            values.append(RetrievedSkill(
                skill=skill,
                origin=origin,
                retrieval_score=len(matched) * 100,
                retrieval_reasons=tuple(f"标签匹配：{tag}" for tag in matched),
            ))
        # Operator-authored overlays must remain visible when a large packaged
        # catalog reaches the retrieval limit. They still need to match the
        # task mode/tags and pass capability filtering in SkillSelector.
        values.sort(key=lambda item: (
            -item.retrieval_score,
            0 if item.origin == "custom" else 1,
            item.skill.name,
        ))
        return values[:max(0, min(query.limit, 128))]
