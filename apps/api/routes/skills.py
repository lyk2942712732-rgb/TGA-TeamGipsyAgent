"""Skills HTTP boundaries."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import unquote

from fastapi import APIRouter, HTTPException, Request

from tga.modes import is_task_mode
from tga.skills.loader import load_skill_text
from tga.skills.registry import SkillRegistry
from tga.skills.store import MAX_SKILL_BYTES, SkillStore

from apps.api.routes.support import SkillUpdateRequest, _api_error

router = APIRouter(tags=["skills"])


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
