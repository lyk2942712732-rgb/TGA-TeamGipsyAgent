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


class AgentBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    runtime: Literal["openai_agents", "claude_agent"]
    provider_id: str
    model_id: str
    max_turns_per_cycle: int = Field(default=3, ge=1, le=50)


class AgentsModelsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = 1
    agents: dict[str, AgentBinding]


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
    worker_images: dict[str, str]
    docker: DockerConfig = Field(default_factory=DockerConfig)
    cadence: CadenceConfig = Field(default_factory=CadenceConfig)


class ResolvedAgent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    agent_id: str
    runtime: Literal["openai_agents", "claude_agent"]
    provider: ProviderConfig
    model: ModelConfig
    api_key: SecretStr
    max_turns_per_cycle: int


class TGA3Config:
    """Loads exactly three files; no environment-secret overlay is supported."""

    REQUIRED_AGENTS = {"supervisor", "worker-openai", "worker-claude", "reporter"}

    def __init__(self, config_dir: str | Path) -> None:
        self.config_dir = Path(config_dir).resolve()
        self.project_root = self.config_dir.parent
        self.models = ModelsConfig.model_validate_json(self._read("models.json"))
        self.agent_models = AgentsModelsConfig.model_validate_json(self._read("agents-models.json"))
        self.runtime = RuntimeConfig.model_validate_json(self._read("runtime.json"))
        self._validate_bindings()

    def resolve_agent(self, agent_id: str) -> ResolvedAgent:
        try:
            binding = self.agent_models.agents[agent_id]
        except KeyError as exc:
            raise KeyError(f"agent binding not found: {agent_id}") from exc
        provider = self.models.provider(binding.provider_id)
        model = provider.model(binding.model_id)
        return ResolvedAgent(
            agent_id=agent_id,
            runtime=binding.runtime,
            provider=provider,
            model=model,
            api_key=SecretStr(provider.key()),
            max_turns_per_cycle=binding.max_turns_per_cycle,
        )

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
        missing = sorted(self.REQUIRED_AGENTS.difference(self.agent_models.agents))
        if missing:
            raise ValueError(f"agents-models.json is missing: {', '.join(missing)}")
        for agent_id, binding in self.agent_models.agents.items():
            provider = self.models.provider(binding.provider_id)
            provider.model(binding.model_id)
            if binding.runtime == "claude_agent" and provider.protocol != "anthropic":
                raise ValueError(f"{agent_id} requires an anthropic provider")
            if binding.runtime == "openai_agents" and provider.protocol == "anthropic":
                raise ValueError(f"{agent_id} cannot use anthropic through openai_agents")


__all__ = [
    "AgentBinding",
    "AgentsModelsConfig",
    "DockerConfig",
    "ModelConfig",
    "ModelsConfig",
    "ProviderConfig",
    "ResolvedAgent",
    "RuntimeConfig",
    "TGA3Config",
]
