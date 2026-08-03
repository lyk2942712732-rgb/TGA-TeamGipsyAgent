"""Validated full-document ingestion and publication for retrieval Skills."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from tga.domain.retrieval import CorpusDocument, CorpusSource, DocumentRevision
from tga.domain.skills import SkillDocument, SkillPublication, SkillPublicationStatus
from tga.infrastructure.retrieval.parser import INJECTION_PATTERNS
from tga.skills.loader import load_skill_text


ALLOWED_SKILL_FRONTMATTER = {"name", "version", "modes", "capabilities", "tags"}


@dataclass(frozen=True, slots=True)
class IngestedSkillRevision:
    document: SkillDocument
    revision: DocumentRevision
    chunk_ids: tuple[str, ...]
    publication: SkillPublication


class SkillIngestionService:
    """Keep immutable Skill source bytes separate from searchable chunks."""

    def __init__(self, repository, *, parser) -> None:
        self.repository = repository
        self.parser = parser

    def ingest_skill_document(
        self,
        *,
        knowledge_base,
        source: CorpusSource,
        document: CorpusDocument,
        revision: DocumentRevision,
        raw: bytes,
        publication_status: SkillPublicationStatus = "draft",
        published_by: str = "system",
        publication_reason: str = "initial ingestion",
    ) -> IngestedSkillRevision:
        if source.channel != "skill":
            raise ValueError("Skill ingestion requires channel=skill")
        if source.status != "active":
            raise ValueError("Skill ingestion requires an active CorpusSource")
        if not raw or len(raw) > 512 * 1024:
            raise ValueError("Skill Markdown must be between 1 byte and 512 KB")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("Skill Markdown must be UTF-8") from exc
        keys = _frontmatter_keys(text)
        unknown = sorted(keys - ALLOWED_SKILL_FRONTMATTER)
        missing = sorted({"name", "version", "modes"} - keys)
        if unknown:
            raise ValueError(f"unsupported Skill frontmatter fields: {', '.join(unknown)}")
        if missing:
            raise ValueError(f"missing Skill frontmatter fields: {', '.join(missing)}")
        parsed = load_skill_text(text, source=f"revision:{revision.id}")
        skill = SkillDocument(
            name=parsed.name,
            version=parsed.version,
            modes=tuple(parsed.modes),
            capability_requirements=tuple(parsed.capabilities),
            tags=tuple(parsed.tags),
            body=parsed.body.strip(),
            origin="retrieval",
            source_ref=f"retrieval://{document.id}/{revision.id}",
        )
        full_sha256 = hashlib.sha256(raw).hexdigest()
        body_sha256 = hashlib.sha256(skill.body.encode("utf-8")).hexdigest()
        safety_flags = _safety_flags(text)
        metadata = {
            **revision.metadata,
            "document_type": "skill",
            "raw_markdown": text,
            "skill_document": skill.model_dump(mode="json"),
            "skill_body_sha256": body_sha256,
            "safety_flags": list(safety_flags),
            "frontmatter_fields": sorted(keys),
        }
        candidate_revision = revision.model_copy(update={
            "content_sha256": full_sha256,
            "media_type": "text/markdown",
            "byte_size": len(raw),
            "metadata": metadata,
        })
        parsed_revision, chunks = self.parser.parse(
            document=document,
            revision=candidate_revision,
            raw=_searchable_markdown(skill).encode("utf-8"),
            source=source,
        )
        parsed_revision = parsed_revision.model_copy(update={
            "content_sha256": full_sha256,
            "byte_size": len(raw),
            "metadata": metadata,
            "extraction_status": "parsed" if chunks else "failed",
        })
        chunks = tuple(item.model_copy(update={
            "metadata": {
                **item.metadata,
                "skill_name": skill.name,
                "skill_version": skill.version,
                "skill_body_sha256": body_sha256,
            },
            "safety_flags": tuple(dict.fromkeys((*item.safety_flags, *safety_flags))),
        }) for item in chunks)

        self.repository.add_knowledge_base(knowledge_base)
        self.repository.add_source(source)
        existing_document = self.repository.get_document(document.id)
        if existing_document is None:
            self.repository.add_document(document)
            existing_document = document
        elif (
            existing_document.source_id != document.source_id
            or existing_document.owner != document.owner
            or existing_document.knowledge_base_id != document.knowledge_base_id
        ):
            raise PermissionError("Skill document ownership changed across revisions")
        self.repository.add_revision(parsed_revision)
        self.repository.set_current_revision(
            document.id,
            parsed_revision.id,
            expected_current_revision_id=existing_document.current_revision_id,
        )
        if chunks:
            self.repository.add_chunks(chunks)
        publication = self.publish(
            revision_id=parsed_revision.id,
            status=publication_status,
            published_by=published_by,
            reason=publication_reason,
        )
        return IngestedSkillRevision(
            document=skill,
            revision=parsed_revision,
            chunk_ids=tuple(item.id for item in chunks),
            publication=publication,
        )

    def publish(
        self,
        *,
        revision_id: str,
        status: SkillPublicationStatus,
        published_by: str,
        reason: str = "",
        requires: tuple[str, ...] = (),
        conflicts_with: tuple[str, ...] = (),
        supersedes: tuple[str, ...] = (),
        compatible_solver_ids: tuple[str, ...] = (),
        compatible_intent_kinds: tuple[str, ...] = (),
        priority: int = 0,
        created_at: str | None = None,
    ) -> SkillPublication:
        from tga.evidence.database import utc_now

        revision = self.repository.get_revision(revision_id)
        if revision is None:
            raise KeyError(f"DocumentRevision not found: {revision_id}")
        payload = revision.metadata.get("skill_document")
        if not isinstance(payload, dict):
            raise ValueError("DocumentRevision is not a full Skill document")
        skill = SkillDocument.model_validate(payload)
        timestamp = created_at or utc_now()
        fingerprint = hashlib.sha256(
            f"{revision_id}\0{status}\0{timestamp}".encode()
        ).hexdigest()[:32]
        publication = SkillPublication(
            id=f"skillpub_{fingerprint}",
            revision_id=revision.id,
            document_id=revision.document_id,
            skill_name=skill.name,
            skill_version=skill.version,
            status=status,
            requires=requires,
            conflicts_with=conflicts_with,
            supersedes=supersedes,
            compatible_solver_ids=compatible_solver_ids,
            compatible_intent_kinds=compatible_intent_kinds,
            priority=priority,
            published_by=published_by,
            reason=reason,
            created_at=timestamp,
        )
        self.repository.save_skill_publication(publication)
        return publication


def _frontmatter_keys(text: str) -> set[str]:
    if not text.startswith("---\n"):
        raise ValueError("Skill is missing YAML frontmatter")
    parts = text.split("---", 2)
    if len(parts) != 3 or not parts[2].strip():
        raise ValueError("Skill requires a non-empty Markdown body")
    keys: set[str] = set()
    for line in parts[1].strip().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            raise ValueError("Skill frontmatter contains an invalid line")
        key = line.split(":", 1)[0].strip()
        if not re.fullmatch(r"[a-z_][a-z0-9_]*", key):
            raise ValueError(f"invalid Skill frontmatter field: {key}")
        if key in keys:
            raise ValueError(f"duplicate Skill frontmatter field: {key}")
        keys.add(key)
    return keys


def _searchable_markdown(skill: SkillDocument) -> str:
    metadata = " ".join((
        skill.name, skill.version, *skill.modes, *skill.tags,
        *skill.capability_requirements,
    ))
    return f"# {skill.name}\n\n{metadata}\n\n{skill.body}"


def _safety_flags(text: str) -> tuple[str, ...]:
    folded = text.casefold()
    flags = ["prompt_injection" for pattern in INJECTION_PATTERNS if re.search(pattern, folded)]
    authorization_markers = (
        "grant capability", "grant tool", "enable tool", "bypass tool policy",
        "expand scope", "override execution policy", "ignore tool policy",
    )
    if any(marker in folded for marker in authorization_markers):
        flags.append("authorization_escalation")
    return tuple(dict.fromkeys(flags))


__all__ = ["IngestedSkillRevision", "SkillIngestionService"]
