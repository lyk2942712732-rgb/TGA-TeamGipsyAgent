"""Skill package catalog CRUD and archive import routes."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, File, Form, UploadFile

from ..skills import MAX_ARCHIVE_BYTES
from .services import AppServices, SkillPackageRequest


def build_skills_router(services: AppServices) -> APIRouter:
    router = APIRouter()
    skills = services.skills

    @router.get("/skills")
    async def skill_index() -> list[dict[str, Any]]:
        return [
            {"name": item.name, "description": item.description, "file_count": item.file_count}
            for item in skills.list()
        ]

    @router.get("/skills/{name}")
    async def skill_package(name: str) -> dict[str, Any]:
        package = skills.package(name)
        return {
            "name": package.name,
            "description": package.description,
            "files": [item.__dict__ for item in package.files],
        }

    @router.put("/skills/{name}")
    async def write_skill(name: str, body: SkillPackageRequest) -> dict[str, Any]:
        package = skills.write_package(name, body.files)
        return {
            "name": package.name,
            "description": package.description,
            "files": [item.__dict__ for item in package.files],
        }

    @router.post("/skills/import", status_code=201)
    async def import_skill_archive(
        file: Annotated[UploadFile, File()],
        name: Annotated[str | None, Form()] = None,
    ) -> dict[str, Any]:
        content = await file.read(MAX_ARCHIVE_BYTES + 1)
        package = skills.import_archive(file.filename or "skill.zip", content, name)
        return {
            "name": package.name,
            "description": package.description,
            "files": [item.__dict__ for item in package.files],
        }

    @router.post("/skills/{name}", status_code=201)
    async def create_skill(name: str, body: SkillPackageRequest) -> dict[str, Any]:
        package = skills.create(name, body.files)
        return {
            "name": package.name,
            "description": package.description,
            "files": [item.__dict__ for item in package.files],
        }

    @router.delete("/skills/{name}", status_code=204)
    async def delete_skill(name: str) -> None:
        skills.delete(name)

    return router
