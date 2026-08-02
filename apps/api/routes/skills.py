"""Skills HTTP boundaries."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from tga.domain.retrieval import (
    CorpusDocument, CorpusSource, DocumentRevision, KnowledgeBase, OwnerScope,
    RetrievalPolicy,
)
from tga.infrastructure.persistence import PersistenceBundle
from tga.infrastructure.retrieval import StructuredDocumentParser
from tga.modes import is_task_mode
from tga.runtime.retrieval import RetrievalIndexService, RetrievalService, SkillIngestionService
from tga.skills.loader import load_skill_text
from tga.skills.registry import SkillRegistry
from tga.skills.store import MAX_SKILL_BYTES, SkillStore

from apps.api.routes.support import SkillUpdateRequest, _api_error, _task_root

router = APIRouter(tags=["skills"])


class SkillCorpusImportRequest(BaseModel):
    model_config = {"extra": "forbid"}

    knowledge_base_id: str = Field(min_length=1, max_length=255)
    source_id: str = Field(min_length=1, max_length=255)
    document_id: str = Field(min_length=1, max_length=255)
    revision_id: str = Field(min_length=1, max_length=255)
    name: str = Field(min_length=1, max_length=255)
    owner: OwnerScope
    markdown: str = Field(min_length=1, max_length=512_000)
    publication_status: str = Field(default="draft", pattern=r"^(draft|reviewed|published|deprecated|revoked)$")
    publication_reason: str = Field(default="", max_length=2_000)


class SkillPublicationRequest(BaseModel):
    model_config = {"extra": "forbid"}

    status: str = Field(pattern=r"^(draft|reviewed|published|deprecated|revoked)$")
    reason: str = Field(default="", max_length=2_000)
    requires: list[str] = Field(default_factory=list, max_length=16)
    conflicts_with: list[str] = Field(default_factory=list, max_length=16)
    supersedes: list[str] = Field(default_factory=list, max_length=16)
    compatible_solver_ids: list[str] = Field(default_factory=list, max_length=64)
    compatible_intent_kinds: list[str] = Field(default_factory=list, max_length=64)
    priority: int = Field(default=0, ge=-10_000, le=10_000)


class SkillSnapshotRequest(BaseModel):
    model_config = {"extra": "forbid"}

    owner: OwnerScope
    knowledge_base_ids: list[str] = Field(min_length=1, max_length=128)
    source_ids: list[str] = Field(default_factory=list, max_length=128)
    index_version: int = Field(default=1, ge=1)


class SkillSnapshotBindingRequest(BaseModel):
    model_config = {"extra": "forbid"}

    owner: OwnerScope
    snapshot_id: str = Field(min_length=1, max_length=255)


def _corpus_path() -> Path:
    configured = os.environ.get("TGA_SKILL_CORPUS_DB")
    if configured:
        return Path(configured)
    return Path(os.environ.get("TGA_RUN_ROOT", "runs")) / "_skill-corpus" / "evidence.db"


def _corpus_bundle() -> PersistenceBundle:
    return PersistenceBundle.open(_corpus_path())


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@router.get("/settings/skills")
def skill_settings() -> dict[str, Any]:
    """List packaged and operator-authored skills by compatible scene."""
    return {"schema_version": 3, **SkillRegistry().snapshot()}


@router.get("/settings/skills/{name}")
def skill_detail(name: str) -> dict[str, Any]:
    try:
        detail = SkillRegistry().detail(name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=_api_error("INVALID_SKILL_NAME", str(exc))) from exc
    if detail is None:
        raise HTTPException(status_code=404, detail=_api_error("SKILL_NOT_FOUND", "skill not found"))
    return {"skill": detail}


@router.post("/settings/skills/import", status_code=201)
async def import_skill(request: Request) -> dict[str, Any]:
    filename = unquote(request.headers.get("x-tga-filename") or "")
    if not filename.lower().endswith(".md") or Path(filename).name != filename:
        raise HTTPException(status_code=422, detail=_api_error("INVALID_SKILL_FILE", "upload one .md file"))
    try:
        content_length = int(request.headers.get("content-length") or 0)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=_api_error("INVALID_CONTENT_LENGTH", "invalid content-length")) from exc
    if content_length > MAX_SKILL_BYTES:
        raise HTTPException(status_code=413, detail=_api_error("SKILL_TOO_LARGE", "skill file exceeds 512 KB"))
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_SKILL_BYTES:
            raise HTTPException(status_code=413, detail=_api_error("SKILL_TOO_LARGE", "skill file exceeds 512 KB"))
    try:
        text = bytes(body).decode("utf-8")
        candidate = load_skill_text(text, source="upload")
        scene = request.headers.get("x-tga-scene")
        if scene:
            if not is_task_mode(scene):
                raise HTTPException(status_code=422, detail=_api_error("INVALID_SKILL_SCENE", "unknown skill scene"))
            if scene not in candidate.modes:
                raise HTTPException(
                    status_code=422,
                    detail=_api_error("SKILL_SCENE_MISMATCH", "skill does not declare the selected scene"),
                )
        existing = SkillRegistry().detail(candidate.name)
        if existing is not None:
            raise HTTPException(status_code=409, detail=_api_error("SKILL_EXISTS", "a skill with this name already exists"))
        skill = SkillStore().import_markdown(bytes(body))
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=422, detail=_api_error("INVALID_SKILL_FILE", "skill must be UTF-8 Markdown")) from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=_api_error("INVALID_SKILL_FILE", str(exc))) from exc
    return {"skill": SkillRegistry().detail(skill.name)}


@router.put("/settings/skills/{name}")
def update_skill(name: str, payload: SkillUpdateRequest) -> dict[str, Any]:
    registry = SkillRegistry()
    existing = registry.detail(name)
    if existing is None:
        raise HTTPException(status_code=404, detail=_api_error("SKILL_NOT_FOUND", "skill not found"))
    try:
        skill = SkillStore().update(
            name,
            modes=payload.modes,
            capabilities=payload.capabilities,
            tags=payload.tags,
            version=payload.version,
            body=payload.body,
        )
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=_api_error("INVALID_SKILL", str(exc))) from exc
    return {"skill": SkillRegistry().detail(skill.name)}


@router.delete("/settings/skills/{name}")
def delete_skill(name: str) -> dict[str, Any]:
    registry = SkillRegistry()
    existing = registry.detail(name)
    if existing is None:
        raise HTTPException(status_code=404, detail=_api_error("SKILL_NOT_FOUND", "skill not found"))
    try:
        store = SkillStore()
        if registry.is_builtin(name):
            store.disable(name)
            deleted = True
        else:
            deleted = store.delete(name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=_api_error("INVALID_SKILL_NAME", str(exc))) from exc
    return {"name": name, "deleted": deleted}


@router.get("/settings/skill-corpus")
def list_skill_corpus() -> dict[str, Any]:
    """List publication records; publication is distinct from activation."""
    bundle = _corpus_bundle()
    try:
        return {
            "view_version": 1,
            "publications": [
                item.model_dump(mode="json")
                for item in bundle.retrieval.list_skill_publications()
            ],
        }
    finally:
        bundle.close()


@router.post("/settings/skill-corpus/import", status_code=201)
def import_skill_corpus(payload: SkillCorpusImportRequest) -> dict[str, Any]:
    bundle = _corpus_bundle()
    try:
        now = _utc_now()
        knowledge_base = bundle.retrieval.get_knowledge_base(payload.knowledge_base_id)
        if knowledge_base is None:
            knowledge_base = KnowledgeBase(
                id=payload.knowledge_base_id,
                name=f"Skill Corpus {payload.knowledge_base_id}",
                owner=payload.owner,
                created_at=now,
            )
        elif knowledge_base.owner != payload.owner or knowledge_base.status != "active":
            raise ValueError("KnowledgeBase owner or status does not permit Skill import")
        source = bundle.retrieval.get_source(payload.source_id)
        if source is None:
            source = CorpusSource(
                id=payload.source_id,
                knowledge_base_id=knowledge_base.id,
                name=payload.name,
                kind="knowledge_base",
                channel="skill",
                owner=payload.owner,
                trust_level="trusted",
                created_at=now,
            )
        elif (
            source.knowledge_base_id != knowledge_base.id
            or source.owner != payload.owner
            or source.channel != "skill"
            or source.status != "active"
        ):
            raise ValueError("CorpusSource ancestry, owner, channel, or status is invalid")
        document = bundle.retrieval.get_document(payload.document_id)
        if document is None:
            document = CorpusDocument(
                id=payload.document_id,
                source_id=source.id,
                knowledge_base_id=knowledge_base.id,
                owner=payload.owner,
                title=f"{payload.name}.md",
                created_at=now,
            )
        elif (
            document.source_id != source.id
            or document.knowledge_base_id != knowledge_base.id
            or document.owner != payload.owner
            or document.status != "active"
        ):
            raise ValueError("CorpusDocument ancestry, owner, or status is invalid")
        revisions = bundle.retrieval.list_revisions(document.id)
        if bundle.retrieval.get_revision(payload.revision_id) is not None:
            raise ValueError("revision_id already exists")
        revision = DocumentRevision(
            id=payload.revision_id,
            document_id=document.id,
            source_id=source.id,
            owner=payload.owner,
            revision=max((item.revision for item in revisions), default=0) + 1,
            content_sha256="0" * 64,
            created_at=now,
        )
        result = SkillIngestionService(
            bundle.retrieval, parser=StructuredDocumentParser()
        ).ingest_skill_document(
            knowledge_base=knowledge_base,
            source=source,
            document=document,
            revision=revision,
            raw=payload.markdown.encode("utf-8"),
            publication_status=payload.publication_status,  # type: ignore[arg-type]
            published_by="api",
            publication_reason=payload.publication_reason,
        )
        return {
            "document": result.document.model_dump(mode="json"),
            "revision": result.revision.model_dump(mode="json"),
            "publication": result.publication.model_dump(mode="json"),
            "state": "candidate" if result.publication.status != "published" else "published",
        }
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=_api_error("INVALID_SKILL_CORPUS_DOCUMENT", str(exc))) from exc
    finally:
        bundle.close()


@router.post("/settings/skill-corpus/revisions/{revision_id}/publication")
def publish_skill_revision(revision_id: str, payload: SkillPublicationRequest) -> dict[str, Any]:
    bundle = _corpus_bundle()
    try:
        publication = SkillIngestionService(
            bundle.retrieval, parser=StructuredDocumentParser()
        ).publish(
            revision_id=revision_id,
            status=payload.status,  # type: ignore[arg-type]
            published_by="api",
            reason=payload.reason,
            requires=tuple(payload.requires),
            conflicts_with=tuple(payload.conflicts_with),
            supersedes=tuple(payload.supersedes),
            compatible_solver_ids=tuple(payload.compatible_solver_ids),
            compatible_intent_kinds=tuple(payload.compatible_intent_kinds),
            priority=payload.priority,
        )
        return {"publication": publication.model_dump(mode="json")}
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=_api_error("INVALID_SKILL_PUBLICATION", str(exc))) from exc
    finally:
        bundle.close()


@router.post("/settings/skill-corpus/snapshots")
def create_skill_snapshot(payload: SkillSnapshotRequest) -> dict[str, Any]:
    bundle = _corpus_bundle()
    try:
        snapshot = RetrievalIndexService(
            bundle.retrieval, parser=StructuredDocumentParser()
        ).create_snapshot(
            owner=payload.owner,
            knowledge_base_ids=tuple(payload.knowledge_base_ids),
            source_ids=tuple(payload.source_ids),
            index_version=payload.index_version,
        )
        return {"index_snapshot": snapshot.model_dump(mode="json")}
    except (KeyError, ValueError, PermissionError) as exc:
        raise HTTPException(status_code=422, detail=_api_error("INVALID_SKILL_SNAPSHOT", str(exc))) from exc
    finally:
        bundle.close()


@router.put("/settings/skill-corpus/snapshot-binding")
def bind_skill_snapshot(payload: SkillSnapshotBindingRequest) -> dict[str, Any]:
    """Explicitly refresh the Skill selection snapshot for one principal."""
    bundle = _corpus_bundle()
    try:
        binding = RetrievalService(bundle.retrieval).refresh_snapshot_binding(
            owner=payload.owner,
            snapshot_id=payload.snapshot_id,
            purpose="skill_selection",
        )
        return {"binding": binding.model_dump(mode="json")}
    except (KeyError, ValueError, PermissionError) as exc:
        raise HTTPException(
            status_code=422,
            detail=_api_error("INVALID_SKILL_SNAPSHOT_BINDING", str(exc)),
        ) from exc
    finally:
        bundle.close()


@router.get("/settings/skill-corpus/candidates")
def preview_skill_candidates(
    task_id: str = Query(min_length=1, max_length=128),
    solver_id: str = Query(min_length=1, max_length=128),
    snapshot_id: str = Query(min_length=1, max_length=255),
    query: str = Query(min_length=1, max_length=8_000),
    workspace_id: str | None = Query(default=None, min_length=1, max_length=255),
) -> dict[str, Any]:
    """Preview candidate references without binding or activating a Skill."""
    bundle = _corpus_bundle()
    try:
        scopes = ("solver", "task", "workspace", "global") if workspace_id else (
            "solver", "task", "global"
        )
        pack = RetrievalService(bundle.retrieval).retrieve_for_principal(
            owner=OwnerScope(scope="solver", task_id=task_id, solver_id=solver_id),
            task_id=task_id,
            solver_id=solver_id,
            intent_id=None,
            query=query,
            policy=RetrievalPolicy(
                allowed_trust_levels=("authoritative", "trusted"),
                allowed_owner_scopes=scopes,
                max_results=20,
                max_context_tokens=24_000,
            ),
            channels=("skill",),
            snapshot_id=snapshot_id,
            workspace_id=workspace_id,
            request_prefix="skill_preview",
        )
        if pack is None:
            raise HTTPException(status_code=404, detail=_api_error("SKILL_SNAPSHOT_NOT_VISIBLE", "snapshot is not visible"))
        return {
            "state": "candidate",
            "retrieval_run_id": pack.retrieval_run_id,
            "index_snapshot_id": pack.index_snapshot_id,
            "candidates": [
                {
                    "hit_id": item.hit_id,
                    "knowledge_base_id": item.knowledge_base_id,
                    "source_id": item.source_id,
                    "document_id": item.document_id,
                    "revision_id": item.revision_id,
                    "name": item.metadata.get("skill_name"),
                    "version": item.metadata.get("skill_version"),
                    "content_sha256": item.metadata.get("skill_body_sha256"),
                    "retrieval_score": item.retrieval_score,
                    "rerank_score": item.rerank_score,
                    "safety_flags": list(item.safety_flags),
                    "state": "candidate",
                }
                for item in pack.items
            ],
        }
    finally:
        bundle.close()


@router.get("/tasks/{task_id}/solvers/{solver_id}/skill-activations")
def skill_activation_history(task_id: str, solver_id: str) -> dict[str, Any]:
    db_path = _task_root(task_id) / "evidence.db"
    if not db_path.is_file():
        raise HTTPException(status_code=404, detail=_api_error("TASK_NOT_FOUND", "task not found"))
    bundle = PersistenceBundle.open(db_path)
    try:
        solver = bundle.solvers.get_solver(solver_id)
        if solver is None or solver.task_id != task_id:
            raise HTTPException(status_code=404, detail=_api_error("SOLVER_NOT_FOUND", "solver not found"))
        activations = bundle.solvers.list_skill_activations(solver_id)
        return {
            "state": "active" if activations else "inactive",
            "skill_snapshot": (
                solver.skill_snapshot.model_dump(mode="json")
                if solver.skill_snapshot is not None else None
            ),
            "selection_decisions": [
                item.model_dump(mode="json")
                for item in bundle.solvers.list_skill_selection_decisions(
                    task_id, solver_id=solver_id
                )
            ],
            "activations": [item.model_dump(mode="json") for item in activations],
        }
    finally:
        bundle.close()
