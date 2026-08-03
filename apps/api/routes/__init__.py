"""Domain route composition for Runtime API v2."""

from fastapi import APIRouter

from apps.api.routes.agent_prompts import router as agent_prompts_router
from apps.api.routes.artifacts import router as artifacts_router
from apps.api.routes.capabilities import router as capabilities_router
from apps.api.routes.catalog import router as catalog_router
from apps.api.routes.events import router as events_router
from apps.api.routes.inputs import router as inputs_router
from apps.api.routes.kali import router as kali_router
from apps.api.routes.llm_settings import router as llm_settings_router
from apps.api.routes.mcp import router as mcp_router
from apps.api.routes.operations import router as operations_router
from apps.api.routes.reports import router as reports_router
from apps.api.routes.sessions import router as sessions_router
from apps.api.routes.skills import router as skills_router
from apps.api.routes.solvers import router as solvers_router
from apps.api.routes.system import router as system_router
from apps.api.routes.tasks import router as tasks_router


router = APIRouter(prefix="/v2")
for domain_router in (
    operations_router,
    tasks_router,
    sessions_router,
    events_router,
    artifacts_router,
    reports_router,
    inputs_router,
    llm_settings_router,
    agent_prompts_router,
    skills_router,
    mcp_router,
    capabilities_router,
    kali_router,
    solvers_router,
    catalog_router,
    system_router,
):
    router.include_router(domain_router)
