"""TGA3 configuration with models and keys kept together in models.json."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class APIKeyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    label: str = "Active"
    api_key: SecretStr


class ModelConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    name: str
    max_output_tokens: int = Field(default=8192, ge=1)
    timeout_seconds: int = Field(default=180, ge=1)
    temperature: float | None = Field(default=None, ge=0, le=2)


class ProviderConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    name: str
    protocol: Literal["openai_responses", "openai_chat_completions", "anthropic"]
    base_url: str | None = None
    api_keys: list[APIKeyConfig]
    selected_api_key_id: str
    models: list[ModelConfig]

    def key(self) -> str:
        item = next(
            (value for value in self.api_keys if value.id == self.selected_api_key_id),
            None,
        )
        if item is None:
            raise KeyError(f"selected API key not found for provider {self.id}")
        value = item.api_key.get_secret_value()
        if not value:
            raise ValueError(f"API key is empty for provider {self.id}")
        return value

    def model(self, model_id: str) -> ModelConfig:
        item = next((value for value in self.models if value.id == model_id), None)
        if item is None:
            raise KeyError(f"model not found: {self.id}/{model_id}")
        return item


class ModelsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = 1
    providers: list[ProviderConfig]

    def provider(self, provider_id: str) -> ProviderConfig:
        item = next((value for value in self.providers if value.id == provider_id), None)
        if item is None:
            raise KeyError(f"provider not found: {provider_id}")
        return item


class AgentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str = Field(min_length=1, max_length=200)
    role: Literal["supervisor", "worker", "reporter"]
    runtime: Literal["openai_agents", "claude_agent"]
    provider_id: str
    model_id: str
    max_turns_per_cycle: int = Field(default=3, ge=1, le=50)
    system_prompt: str = Field(min_length=1)


class AgentsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = 1
    agents: dict[str, AgentConfig]


class SceneConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=500)
    system_prompt: str = Field(min_length=1)


class ScenesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = 1
    scenes: list[SceneConfig]

    def scene(self, scene_id: str) -> SceneConfig:
        item = next((value for value in self.scenes if value.id == scene_id), None)
        if item is None:
            raise KeyError(f"scene not found: {scene_id}")
        return item


class CadenceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    worker_sync_seconds: int = Field(default=90, ge=5, le=3600)
    supervisor_debounce_seconds: int = Field(default=2, ge=0, le=60)
    supervisor_cooldown_seconds: int = Field(default=20, ge=0, le=3600)
    finalization_grace_seconds: int = Field(default=30, ge=0, le=600)


class DockerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    network: str = "bridge"
    cpu_cores: float = Field(default=2, gt=0, le=64)
    memory_mb: int = Field(default=4096, ge=256, le=131072)
    pids_limit: int = Field(default=512, ge=32, le=65536)
    cap_add: list[str] = Field(default_factory=lambda: ["NET_RAW"])


class WorkerCyclePromptsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    startup: str = Field(min_length=1)
    periodic: str = Field(min_length=1)
    blackboard_changed: str = Field(min_length=1)


class RuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = 1
    postgres_dsn: str
    control_ws_url: str
    blackboard_mcp_url: str
    listen_host: str = "0.0.0.0"
    listen_port: int = Field(default=8083, ge=1, le=65535)
    workspace_root: str
    input_root: str
    artifact_root: str
    writeup_root: str
    skills_root: str
    mcp_instructions: str = Field(min_length=1)
    worker_images: dict[str, str]
    worker_cycle_prompts: WorkerCyclePromptsConfig
    docker: DockerConfig = Field(default_factory=DockerConfig)
    cadence: CadenceConfig = Field(default_factory=CadenceConfig)


class ResolvedAgent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    agent_id: str
    display_name: str
    role: Literal["supervisor", "worker", "reporter"]
    runtime: Literal["openai_agents", "claude_agent"]
    provider: ProviderConfig
    model: ModelConfig
    api_key: SecretStr
    max_turns_per_cycle: int
    system_prompt: str


class TGA3Config:
    """Loads the single config directory; no environment-secret overlay is supported."""

    REQUIRED_AGENTS = {"supervisor", "worker-openai", "worker-claude", "reporter"}
    REQUIRED_SCENES = {
        "penetration_test",
        "incident_response",
        "vulnerability_research",
        "reverse_engineering",
        "pwn",
        "security_misc",
        "cryptography",
        "forensics",
    }

    def __init__(self, config_dir: str | Path) -> None:
        self.config_dir = Path(config_dir).resolve()
        self.project_root = self.config_dir.parent
        self.models = ModelsConfig.model_validate_json(self._read("models.json"))
        self.agents = AgentsConfig.model_validate_json(self._read("agents.json"))
        self.scenes = ScenesConfig.model_validate_json(self._read("scenes.json"))
        self.runtime = RuntimeConfig.model_validate_json(self._read("runtime.json"))
        self._validate_bindings()

    def resolve_agent(self, agent_id: str) -> ResolvedAgent:
        try:
            binding = self.agents.agents[agent_id]
        except KeyError as exc:
            raise KeyError(f"agent config not found: {agent_id}") from exc
        provider = self.models.provider(binding.provider_id)
        model = provider.model(binding.model_id)
        return ResolvedAgent(
            agent_id=agent_id,
            display_name=binding.display_name,
            role=binding.role,
            runtime=binding.runtime,
            provider=provider,
            model=model,
            api_key=SecretStr(provider.key()),
            max_turns_per_cycle=binding.max_turns_per_cycle,
            system_prompt=binding.system_prompt,
        )

    def scene(self, scene_id: str) -> SceneConfig:
        return self.scenes.scene(scene_id)

    @property
    def worker_agent_ids(self) -> tuple[str, ...]:
        return tuple(agent_id for agent_id, item in self.agents.agents.items() if item.role == "worker")

    def resolve_path(self, raw: str) -> Path:
        path = Path(raw)
        return path.resolve() if path.is_absolute() else (self.project_root / path).resolve()

    @property
    def skills_root(self) -> Path:
        return self.resolve_path(self.runtime.skills_root)

    def ensure_directories(self) -> None:
        for value in (
            self.runtime.workspace_root,
            self.runtime.input_root,
            self.runtime.artifact_root,
            self.runtime.writeup_root,
            self.runtime.skills_root,
        ):
            self.resolve_path(value).mkdir(parents=True, exist_ok=True)

    def _read(self, name: str) -> str:
        path = self.config_dir / name
        if not path.is_file():
            raise FileNotFoundError(f"missing TGA3 configuration: {path}")
        return path.read_text(encoding="utf-8")

    def _validate_bindings(self) -> None:
        missing = sorted(self.REQUIRED_AGENTS.difference(self.agents.agents))
        if missing:
            raise ValueError(f"agents.json is missing: {', '.join(missing)}")
        expected_roles = {
            "supervisor": "supervisor",
            "worker-openai": "worker",
            "worker-claude": "worker",
            "reporter": "reporter",
        }
        for agent_id, expected_role in expected_roles.items():
            if self.agents.agents[agent_id].role != expected_role:
                raise ValueError(f"{agent_id} must use role={expected_role}")
        scene_ids = {scene.id for scene in self.scenes.scenes}
        if len(scene_ids) != len(self.scenes.scenes):
            raise ValueError("scenes.json contains duplicate scene ids")
        missing_scenes = sorted(self.REQUIRED_SCENES.difference(scene_ids))
        if missing_scenes:
            raise ValueError(f"scenes.json is missing: {', '.join(missing_scenes)}")
        expected_runtimes = {
            "supervisor": "openai_agents",
            "worker-openai": "openai_agents",
            "worker-claude": "claude_agent",
            "reporter": "openai_agents",
        }
        for agent_id, expected_runtime in expected_runtimes.items():
            if self.agents.agents[agent_id].runtime != expected_runtime:
                raise ValueError(f"{agent_id} must use runtime={expected_runtime}")
        for agent_id, binding in self.agents.agents.items():
            provider = self.models.provider(binding.provider_id)
            provider.model(binding.model_id)
            if binding.runtime == "claude_agent" and provider.protocol != "anthropic":
                raise ValueError(f"{agent_id} requires an anthropic provider")
            if binding.runtime == "openai_agents" and provider.protocol == "anthropic":
                raise ValueError(f"{agent_id} cannot use anthropic through openai_agents")


__all__ = [
    "AgentConfig",
    "AgentsConfig",
    "DockerConfig",
    "ModelConfig",
    "ModelsConfig",
    "ProviderConfig",
    "ResolvedAgent",
    "RuntimeConfig",
    "SceneConfig",
    "ScenesConfig",
    "TGA3Config",
    "WorkerCyclePromptsConfig",
]
