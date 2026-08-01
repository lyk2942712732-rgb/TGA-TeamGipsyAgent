"""Catalog adapter that reloads complete published Skills from revisions."""

from __future__ import annotations

from tga.domain.skills import SkillDocument
from tga.modes import TaskMode


class RetrievalSkillCatalog:
    def __init__(self, repository, *, published_only: bool = True) -> None:
        self.repository = repository
        self.published_only = published_only

    def all(self) -> tuple[SkillDocument, ...]:
        by_name: dict[str, tuple[object, SkillDocument]] = {}
        latest_by_revision = {}
        for publication in self.repository.list_skill_publications():
            current = latest_by_revision.get(publication.revision_id)
            if current is None or (publication.created_at, publication.id) > (
                current.created_at, current.id
            ):
                latest_by_revision[publication.revision_id] = publication
        for publication in latest_by_revision.values():
            if self.published_only and publication.status != "published":
                continue
            revision = self.repository.get_revision(publication.revision_id)
            if revision is None:
                continue
            payload = revision.metadata.get("skill_document")
            if not isinstance(payload, dict):
                continue
            document = SkillDocument.model_validate(payload)
            current = by_name.get(document.name)
            if current is None or (publication.created_at, publication.id) > (
                current[0].created_at, current[0].id
            ):
                by_name[document.name] = (publication, document)
        return tuple(by_name[name][1] for name in sorted(by_name))

    def get(self, name: str) -> SkillDocument | None:
        return next((skill for skill in self.all() if skill.name == name), None)

    def compatible(self, mode: TaskMode) -> tuple[SkillDocument, ...]:
        return tuple(skill for skill in self.all() if mode in skill.modes)

    def get_revision_document(self, revision_id: str) -> SkillDocument:
        revision = self.repository.get_revision(revision_id)
        if revision is None:
            raise KeyError(f"DocumentRevision not found: {revision_id}")
        payload = revision.metadata.get("skill_document")
        if not isinstance(payload, dict):
            raise ValueError("DocumentRevision does not contain a full SkillDocument")
        return SkillDocument.model_validate(payload)


__all__ = ["RetrievalSkillCatalog"]
