"""Shared dependency container and request schemas for TGA3 control-plane routers."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..blackboard_delivery import BlackboardDelivery
from ..config import AgentRuntime, TGA3Config
from ..coordinator import TaskCoordinator
from ..docker_runtime import ContainerRuntime
from ..file_publications import FilePublications
from ..host_agents import Automation
from ..knowledge_relay import KnowledgeRelay
from ..local_knowledge import LocalKnowledge
from ..provider_profiles import ProviderProtocol
from ..skills import SkillCatalog
from ..storage import Storage
from ..submission_guard import SubmissionGuard
from ..token_usage import TokenUsageTracker
from ..web_search import WebSearchService


class ModelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider_id: str
    model_id: str
    protocol: ProviderProtocol
    runtime: AgentRuntime | None = None


class ModelEnabledRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_ids: list[str] = Field(min_length=1)
    enabled: bool


class SkillPackageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    files: dict[str, str] = Field(min_length=1)


class RealtimePromptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    text: str = Field(min_length=1, max_length=20000)
    idempotency_key: str = Field(min_length=1, max_length=180)


class ScoutSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    timeout_seconds: int = Field(ge=60, le=600)


class ExtendQuestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    minutes: int = Field(default=5, ge=1, le=240)


class ScoutWatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["enable_sentinel", "enable_llm_poll", "disable"]
    interval_seconds: int | None = Field(default=None, ge=15, le=3600)


class FinishDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["continue_standby", "finish_after_reports", "finish_now"]
    auto_standby_future: bool = False


class RestartTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rerun_scout: bool = False
    retry_abandoned: bool = False
    agent_models: dict[str, ModelRequest] | None = None
    aoe_first_attempt_minutes: int | None = Field(default=None, ge=1, le=1440)
    aoe_second_attempt_minutes: int | None = Field(default=None, ge=1, le=1440)
    aoe_joint_minutes: int | None = Field(default=None, ge=1, le=1440)


@dataclass
class AppServices:
    config: TGA3Config
    storage: Storage
    containers: ContainerRuntime
    coordinator: TaskCoordinator
    automation: Automation
    skills: SkillCatalog
    delivery: BlackboardDelivery
    usage: TokenUsageTracker
    publications: FilePublications
    submission_guard: SubmissionGuard
    web_search: WebSearchService
    knowledge_relay: KnowledgeRelay
    local_knowledge: LocalKnowledge
    config_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
