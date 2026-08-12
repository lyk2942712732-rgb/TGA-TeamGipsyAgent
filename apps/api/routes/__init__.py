"""HTTP route composition; all implementation state lives in the TGA2 container."""

from fastapi import APIRouter

from apps.api.routes.catalog import router as catalog_router
from apps.api.routes.mcp import router as mcp_router
from apps.api.routes.settings import router as settings_router
from apps.api.routes.tasks import router as tasks_router

router = APIRouter()
for child in (tasks_router, settings_router, mcp_router, catalog_router):
    router.include_router(child)

__all__ = ["router"]
