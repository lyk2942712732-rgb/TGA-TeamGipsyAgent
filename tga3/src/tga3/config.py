"""TGA3 configuration with models and keys kept together in models.json."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from .provider_profiles import (
    ProviderProtocol,
    normalize_endpoint_base,
    runtime_base_url,
)


class APIKeyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    label: str = "Active"
    api_key: SecretStr


class ModelConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    name: str
    enabled: bool = True
    available: bool = True
    max_output_tokens: int = Field(default=8192, ge=1)
    timeout_seconds: int = Field(default=180, ge=1)
    temperature: float | None = Field(default=None, ge=0, le=2)


class ProviderEndpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_url: str
    models: list[ModelConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_endpoint(self) -> ProviderEndpoint:
        self.base_url = normalize_endpoint_base(self.base_url)
        ids = [model.id for model in self.models]
        if len(ids) != len(set(ids)):
            raise ValueError("协议端点包含重复模型 ID")
        return self


class ProviderConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    name: str
    preset_id: str = "custom"
    built_in: bool = False
    endpoints: dict[ProviderProtocol, ProviderEndpoint]
    api_keys: list[APIKeyConfig]
    selected_api_key_id: str

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_endpoints(cls, raw: Any) -> Any:
        if not isinstance(raw, dict):
            return raw
        data = dict(raw)
        if "endpoints" not in data:
            # Resolve the old implicit paths once. New endpoints are always explicit.
            protocols = data.get("protocols", [])
            if len(protocols) != len(set(protocols)):
                raise ValueError("供应商包含重复协议")
            data["endpoints"] = {
                protocol: {
                    "base_url": runtime_base_url(data.get("preset_id"), data.get("base_url") or "", protocol),
                    "models": data.get("models", []),
                }
                for protocol in protocols
            }
        for field in ("protocols", "base_url", "models"):
            data.pop(field, None)
        return data

    @model_validator(mode="after")
    def validate_endpoints(self) -> ProviderConfig:
        if not self.endpoints:
            raise ValueError("至少填写一个协议端点")
        return self

    def endpoint(self, protocol: ProviderProtocol) -> ProviderEndpoint:
        if protocol not in self.endpoints:
            raise ValueError(f"provider {self.id} does not support protocol={protocol}")
        return self.endpoints[protocol]

    def sdk_base_url(self, protocol: ProviderProtocol) -> str:
        return self.endpoint(protocol).base_url

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

    def model(self, model_id: str, protocol: ProviderProtocol, *, require_enabled: bool = False) -> ModelConfig:
        item = next((value for value in self.endpoint(protocol).models if value.id == model_id), None)
        if item is None:
            raise KeyError(f"model not found: {self.id}/{protocol}/{model_id}")
        if require_enabled and (not item.enabled or not item.available):
            raise ValueError(f"模型未启用或已不可用：{self.name}/{item.name}")
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
    role: Literal["scout", "worker", "reporter"]
    runtime: Literal["codex_cli", "claude_cli"]
    provider_id: str
    model_id: str
    protocol: ProviderProtocol
    blackboard_interval_seconds: int = Field(default=60, ge=1, le=3600)
    system_prompt: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_model_binding(self) -> AgentConfig:
        if bool(self.provider_id) != bool(self.model_id):
            raise ValueError("供应商和模型必须同时配置或同时留空")
        return self


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
    finalization_grace_seconds: int = Field(default=30, ge=0, le=600)


class DockerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    network: str = "bridge"
    cpu_cores: float = Field(default=2, gt=0, le=64)
    memory_mb: int = Field(default=4096, ge=256, le=131072)
    pids_limit: int = Field(default=512, ge=32, le=65536)
    worker_uid: int = Field(default=1000, ge=1, le=65535)
    worker_gid: int = Field(default=1000, ge=1, le=65535)
    cap_add: list[str] = Field(default_factory=lambda: ["NET_RAW", "SYS_PTRACE"])


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
    cli_timeout_seconds: int = Field(default=1800, ge=30, le=86400)
    scout_timeout_seconds: int = Field(default=300, ge=60, le=600)
    worker_images: dict[str, str]
    worker_cycle_prompts: WorkerCyclePromptsConfig
    docker: DockerConfig = Field(default_factory=DockerConfig)
    cadence: CadenceConfig = Field(default_factory=CadenceConfig)



class ConfigBundle(BaseModel):
    """The complete editable config surface persisted below config/."""

    model_config = ConfigDict(extra="forbid")
    models: ModelsConfig
    agents: AgentsConfig
    scenes: ScenesConfig
    runtime: RuntimeConfig

    def documents(self) -> dict[str, dict]:
        models = self.models.model_dump(mode="json")
        for provider_data, provider in zip(models["providers"], self.models.providers, strict=True):
            for key_data, key in zip(provider_data["api_keys"], provider.api_keys, strict=True):
                key_data["api_key"] = key.api_key.get_secret_value()
        return {
            "models.json": models,
            "agents.json": self.agents.model_dump(mode="json"),
            "scenes.json": self.scenes.model_dump(mode="json"),
            "runtime.json": self.runtime.model_dump(mode="json"),
        }


class ResolvedAgent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    agent_id: str
    display_name: str
    role: Literal["scout", "worker", "reporter"]
    runtime: Literal["codex_cli", "claude_cli"]
    provider: ProviderConfig
    model: ModelConfig
    protocol: ProviderProtocol
    api_key: SecretStr
    system_prompt: str


class TGA3Config:
    """Loads the single config directory; no environment-secret overlay is supported."""

    REQUIRED_AGENTS = {"scout", "worker-openai", "worker-claude", "reporter"}
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
        self._validate_values(self.models, self.agents, self.scenes, self.runtime)

    def resolve_agent(
        self,
        agent_id: str,
        provider_id: str | None = None,
        model_id: str | None = None,
        protocol: ProviderProtocol | None = None,
        *,
        require_api_key: bool = True,
    ) -> ResolvedAgent:
        try:
            binding = self.agents.agents[agent_id]
        except KeyError as exc:
            raise KeyError(f"agent config not found: {agent_id}") from exc
        selected_provider = provider_id or binding.provider_id
        selected_model = model_id or binding.model_id
        if not selected_provider or not selected_model:
            raise ValueError(f"{binding.display_name} 尚未配置模型，请先在 Solver 页面选择已启用模型")
        provider = self.models.provider(selected_provider)
        selected_protocol = protocol or binding.protocol
        self.validate_runtime_protocol(binding.runtime, selected_protocol)
        model = provider.model(selected_model, selected_protocol, require_enabled=require_api_key)
        selected_key = next(
            (value.api_key for value in provider.api_keys if value.id == provider.selected_api_key_id),
            None,
        )
        if selected_key is None:
            raise KeyError(f"selected API key not found for provider {provider.id}")
        api_key = SecretStr(provider.key()) if require_api_key else selected_key
        return ResolvedAgent(
            agent_id=agent_id,
            display_name=binding.display_name,
            role=binding.role,
            runtime=binding.runtime,
            provider=provider,
            model=model,
            protocol=selected_protocol,
            api_key=api_key,
            system_prompt=binding.system_prompt,
        )

    @staticmethod
    def validate_runtime_protocol(runtime: str, protocol: ProviderProtocol) -> None:
        if runtime == "claude_cli" and protocol != "anthropic":
            raise ValueError("Claude Code requires an Anthropic-compatible provider")
        if runtime == "codex_cli" and protocol != "openai_responses":
            raise ValueError("Codex CLI requires the OpenAI Responses protocol")

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

    def export_bundle(self) -> dict[str, dict]:
        documents = ConfigBundle(
            models=self.models,
            agents=self.agents,
            scenes=self.scenes,
            runtime=self.runtime,
        ).documents()
        return {
            "models": documents["models.json"],
            "agents": documents["agents.json"],
            "scenes": documents["scenes.json"],
            "runtime": documents["runtime.json"],
        }

    def export_document(self, name: Literal["models", "agents", "scenes", "runtime"]) -> dict:
        """Return one editable config document for its owning settings screen."""

        return self.export_bundle()[name]

    def apply_document(
        self,
        name: Literal["models", "agents", "scenes", "runtime"],
        value: ModelsConfig | AgentsConfig | ScenesConfig | RuntimeConfig,
    ) -> None:
        """Validate the whole live configuration, but persist only the owned document."""

        if name == "agents":
            assert isinstance(value, AgentsConfig)
            if set(value.agents) != set(self.agents.agents):
                raise ValueError("Agent definitions cannot be added, removed, or renamed")
            for agent_id, current in self.agents.agents.items():
                proposed = value.agents[agent_id]
                if (proposed.display_name, proposed.role, proposed.runtime) != (
                    current.display_name,
                    current.role,
                    current.runtime,
                ):
                    raise ValueError(f"{agent_id} name, role, and runtime are fixed definitions")
                if proposed.provider_id and (proposed.provider_id, proposed.model_id, proposed.protocol) != (
                    current.provider_id,
                    current.model_id,
                    current.protocol,
                ):
                    self.models.provider(proposed.provider_id).model(
                        proposed.model_id, proposed.protocol, require_enabled=True
                    )
        if name == "scenes":
            assert isinstance(value, ScenesConfig)
            current_definitions = [(item.id, item.name, item.description) for item in self.scenes.scenes]
            proposed_definitions = [(item.id, item.name, item.description) for item in value.scenes]
            if proposed_definitions != current_definitions:
                raise ValueError("Scene id, name, order, and description are fixed definitions")

        candidates = {
            "models": self.models,
            "agents": self.agents,
            "scenes": self.scenes,
            "runtime": self.runtime,
        }
        candidates[name] = value
        models = candidates["models"]
        agents = candidates["agents"]
        scenes = candidates["scenes"]
        runtime = candidates["runtime"]
        assert isinstance(models, ModelsConfig)
        assert isinstance(agents, AgentsConfig)
        assert isinstance(scenes, ScenesConfig)
        assert isinstance(runtime, RuntimeConfig)
        self._validate_values(models, agents, scenes, runtime)

        documents = ConfigBundle(
            models=models,
            agents=agents,
            scenes=scenes,
            runtime=runtime,
        ).documents()
        filename = f"{name}.json"
        target = self.config_dir / filename
        temporary = self.config_dir / f".{filename}.{uuid4().hex}.tmp"
        try:
            temporary.write_text(
                json.dumps(documents[filename], ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary.chmod(0o600 if name == "models" else target.stat().st_mode & 0o777)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

        setattr(self, name, value)
        self.ensure_directories()

    def apply_bundle(self, bundle: ConfigBundle) -> None:
        """Validate, atomically replace all config documents, then refresh this live snapshot."""

        self._validate_values(bundle.models, bundle.agents, bundle.scenes, bundle.runtime)
        documents = bundle.documents()
        targets = {name: self.config_dir / name for name in documents}
        originals = {name: path.read_bytes() for name, path in targets.items()}
        staged: dict[str, Path] = {}
        replaced: list[str] = []
        try:
            for name, payload in documents.items():
                temporary = self.config_dir / f".{name}.{uuid4().hex}.tmp"
                temporary.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                temporary.chmod(0o600 if name == "models.json" else targets[name].stat().st_mode & 0o777)
                staged[name] = temporary
            for name, target in targets.items():
                os.replace(staged[name], target)
                replaced.append(name)
        except Exception:
            for name in replaced:
                recovery = self.config_dir / f".{name}.{uuid4().hex}.rollback"
                recovery.write_bytes(originals[name])
                os.replace(recovery, targets[name])
            raise
        finally:
            for temporary in staged.values():
                temporary.unlink(missing_ok=True)

        self.models = bundle.models
        self.agents = bundle.agents
        self.scenes = bundle.scenes
        self.runtime = bundle.runtime
        self.ensure_directories()

    def _read(self, name: str) -> str:
        path = self.config_dir / name
        if not path.is_file():
            raise FileNotFoundError(f"missing TGA3 configuration: {path}")
        return path.read_text(encoding="utf-8")

    @classmethod
    def _validate_values(
        cls,
        models: ModelsConfig,
        agents: AgentsConfig,
        scenes: ScenesConfig,
        runtime: RuntimeConfig,
    ) -> None:
        if set(agents.agents) != cls.REQUIRED_AGENTS:
            raise ValueError("agents.json must contain exactly scout, worker-openai, worker-claude and reporter")
        missing = sorted(cls.REQUIRED_AGENTS.difference(agents.agents))
        if missing:
            raise ValueError(f"agents.json is missing: {', '.join(missing)}")
        expected_roles = {
            "scout": "scout",
            "worker-openai": "worker",
            "worker-claude": "worker",
            "reporter": "reporter",
        }
        for agent_id, expected_role in expected_roles.items():
            if agents.agents[agent_id].role != expected_role:
                raise ValueError(f"{agent_id} must use role={expected_role}")
        scene_ids = {scene.id for scene in scenes.scenes}
        if len(scene_ids) != len(scenes.scenes):
            raise ValueError("scenes.json contains duplicate scene ids")
        missing_scenes = sorted(cls.REQUIRED_SCENES.difference(scene_ids))
        if missing_scenes:
            raise ValueError(f"scenes.json is missing: {', '.join(missing_scenes)}")
        expected_runtimes = {
            "scout": "codex_cli",
            "worker-openai": "codex_cli",
            "worker-claude": "claude_cli",
            "reporter": "codex_cli",
        }
        for agent_id, expected_runtime in expected_runtimes.items():
            if agents.agents[agent_id].runtime != expected_runtime:
                raise ValueError(f"{agent_id} must use runtime={expected_runtime}")
        for _agent_id, binding in agents.agents.items():
            cls.validate_runtime_protocol(binding.runtime, binding.protocol)
            if not binding.provider_id and not binding.model_id:
                continue
            provider = models.provider(binding.provider_id)
            provider.model(binding.model_id, binding.protocol)
        missing_images = sorted(
            agent_id
            for agent_id, binding in agents.agents.items()
            if binding.role in {"scout", "worker", "reporter"} and agent_id not in runtime.worker_images
        )
        if missing_images:
            raise ValueError(f"runtime.json worker_images is missing: {', '.join(missing_images)}")


__all__ = [
    "AgentConfig",
    "AgentsConfig",
    "ConfigBundle",
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
