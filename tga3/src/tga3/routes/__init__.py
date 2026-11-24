"""Domain-scoped FastAPI routers for the TGA3 control plane."""

from .aoe import build_aoe_router
from .config import build_config_router
from .internal import build_internal_router
from .services import (
    AppServices,
    ExtendQuestionRequest,
    ModelEnabledRequest,
    ModelRequest,
    RealtimePromptRequest,
    RestartTaskRequest,
    ScoutSettingsRequest,
    SkillPackageRequest,
)
from .skills import build_skills_router
from .tasks import build_tasks_router

__all__ = [
    "AppServices",
    "ExtendQuestionRequest",
    "ModelEnabledRequest",
    "ModelRequest",
    "RealtimePromptRequest",
    "RestartTaskRequest",
    "ScoutSettingsRequest",
    "SkillPackageRequest",
    "build_aoe_router",
    "build_config_router",
    "build_internal_router",
    "build_skills_router",
    "build_tasks_router",
]
