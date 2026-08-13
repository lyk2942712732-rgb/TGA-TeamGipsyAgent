"""Persisted configuration loaded exclusively from ``<run_root>/.config``."""

from __future__ import annotations

import json
import os
from pathlib import Path
from threading import RLock
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from tga2.integrations.model import ModelRegistry, ModelSettings

ROLES = ("supervisor", "worker", "reviewer", "reporter")
KALI_PROFILE_ID = "tga2-kali"
DEFAULT_KALI_IMAGE = (
    "ghcr.io/lyk2942712732-rgb/tga-kali-universal:sandbox-v0.2.1"
)
DEFAULT_KALI_IMAGE_DIGEST = (
    "sha256:300fca8aaf785e6f8a589e595e08b0174657609e0ca1935960b5bbfa28e7970f"
)


class RoleModelSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider_id: str = "offline"
    model_id: str = "offline"


class RoleRuntimeSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str = ""
    model: RoleModelSelection = Field(default_factory=RoleModelSelection)
    tools: list[str] = Field(default_factory=list)
    model_call_limit: int = Field(default=8, ge=1, le=100)
    model_retries: int = Field(default=2, ge=0, le=10)


class GraphSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_review_retries: int = Field(default=2, ge=0, le=20)
    max_intents: int = Field(default=8, ge=1, le=100)
    max_turns: int = Field(default=32, ge=1, le=1000)
    max_active_workers: int = Field(default=1, ge=1, le=32)
    max_total_solvers: int = Field(default=4, ge=4, le=128)


class SkillSelectionSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    automatic_limit: int = Field(default=3, ge=0, le=50)
    prompt_injection_limit: int = Field(default=5, ge=0, le=50)


class ToolDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid")
    allowed: list[str] = Field(default_factory=list)
    max_calls: int = Field(default=30, ge=1, le=1000)
    retry_count: int = Field(default=1, ge=0, le=10)


class KaliSandboxSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    profile_id: str = KALI_PROFILE_ID
    image: str = DEFAULT_KALI_IMAGE
    expected_digest: str | None = Field(
        default=DEFAULT_KALI_IMAGE_DIGEST,
        pattern=r"^sha256:[a-fA-F0-9]{64}$",
    )
    cpu_cores: float = Field(default=1, gt=0, le=64)
    memory_mb: int = Field(default=1024, ge=128, le=131072)
    max_processes: int = Field(default=256, ge=16, le=65536)
    command_timeout_seconds: int = Field(default=120, ge=1, le=3600)
    read_only_rootfs: bool = True


class FileLimitSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    upload_max_bytes: int = Field(default=25_000_000, ge=1)
    model_read_max_bytes: int = Field(default=2_000_000, ge=1)
    artifact_preview_max_bytes: int = Field(default=200_000, ge=1)
    skill_import_max_bytes: int = Field(default=512_000, ge=1)


class RuntimeSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = 2
    common_prompt: str = ""
    roles: dict[str, RoleRuntimeSettings]
    graph: GraphSettings = Field(default_factory=GraphSettings)
    skill_selection: SkillSelectionSettings = Field(default_factory=SkillSelectionSettings)
    tool_defaults: ToolDefaults = Field(default_factory=ToolDefaults)
    kali: KaliSandboxSettings = Field(default_factory=KaliSandboxSettings)
    files: FileLimitSettings = Field(default_factory=FileLimitSettings)

    @property
    def sandbox_image(self) -> str | None:
        return self.kali.image if self.kali.enabled else None


class SceneCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = 1
    scenes: list[dict[str, Any]]


class Configuration:
    """The one configuration repository shared by API, CLI and Runtime."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.runtime_path = self.root / "runtime.json"
        self.models_path = self.root / "models.json"
        self.scenes_path = self.root / "scenes.json"
        self._lock = RLock()
        self._require_files()
        self.runtime = self._load_runtime()
        self.scenes = self._load_scenes()
        self.model_registry = self._load_models()

    @property
    def supported_modes(self) -> tuple[str, ...]:
        return tuple(str(item["id"]) for item in self.scenes.scenes)

    def scene(self, scene_id: str) -> dict[str, Any]:
        value = next(
            (item for item in self.scenes.scenes if item.get("id") == scene_id), None
        )
        if value is None:
            raise KeyError(f"unknown task mode: {scene_id}")
        return value

    def save_runtime(self) -> None:
        self._write_json(self.runtime_path, self.runtime.model_dump(mode="json"))

    # Compatibility name used by older call sites.
    save = save_runtime

    def save_models(self) -> None:
        self._write_json(self.models_path, self.model_registry.persisted_payload(), 0o600)

    # Compatibility name while routes are migrated to the single models.json file.
    save_model_registry = save_models

    def save_scenes(self) -> None:
        self._write_json(self.scenes_path, self.scenes.model_dump(mode="json"))

    def update_role(
        self,
        role: str,
        *,
        model: dict[str, str] | None = None,
        tools: list[str] | None = None,
        prompt: str | None = None,
    ) -> None:
        if role not in ROLES:
            raise KeyError(f"unknown role: {role}")
        current = self.runtime.roles[role]
        update: dict[str, Any] = {}
        if model is not None:
            update["model"] = RoleModelSelection.model_validate(model)
        if tools is not None:
            update["tools"] = list(dict.fromkeys(tools))
        if prompt is not None:
            update["prompt"] = prompt
        roles = dict(self.runtime.roles)
        roles[role] = current.model_copy(update=update)
        self.runtime = self.runtime.model_copy(update={"roles": roles})
        self.save_runtime()

    def update_solver_tools(self, solver_id: str, tools: list[str]) -> None:
        self.update_role(solver_id, tools=tools)

    def update_kali(self, settings: KaliSandboxSettings) -> None:
        self.runtime = self.runtime.model_copy(update={"kali": settings})
        self.save_runtime()

    def update_prompts(self, payload: dict[str, Any]) -> dict[str, Any]:
        common = payload.get("common_system_prompt")
        roles = dict(self.runtime.roles)
        for role in ROLES:
            if role in payload:
                roles[role] = roles[role].model_copy(
                    update={"prompt": str(payload[role])}
                )
        self.runtime = self.runtime.model_copy(
            update={
                "common_prompt": self.runtime.common_prompt
                if common is None
                else str(common),
                "roles": roles,
            }
        )
        if "modes" in payload:
            incoming = {
                str(item.get("id")): item
                for item in payload["modes"]
                if isinstance(item, dict) and item.get("id") in self.supported_modes
            }
            scenes = []
            for scene in self.scenes.scenes:
                value = dict(scene)
                if scene["id"] in incoming:
                    mode = incoming[scene["id"]]
                    prompts = dict(value.get("prompts") or {})
                    for key in ("methodology", "completion_focus", "observer_focus"):
                        if key in mode:
                            prompts[key] = mode[key]
                    value["prompts"] = prompts
                scenes.append(value)
            self.scenes = self.scenes.model_copy(update={"scenes": scenes})
            self.save_scenes()
        self.save_runtime()
        return self.prompt_payload()

    def prompt_payload(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "common_system_prompt": self.runtime.common_prompt,
            "modes": [
                {
                    "id": scene["id"],
                    "label": scene["label"],
                    **(scene.get("prompts") or {}),
                }
                for scene in self.scenes.scenes
            ],
            **{role: self.runtime.roles[role].prompt for role in ROLES},
        }

    def agent_prompts(self) -> dict[str, Any]:
        return {
            "common": self.runtime.common_prompt,
            **{role: self.runtime.roles[role].prompt for role in ROLES},
            "__modes__": [
                {
                    "id": scene["id"],
                    "label": scene["label"],
                    **(scene.get("prompts") or {}),
                }
                for scene in self.scenes.scenes
            ],
        }

    def role_model_status(self, role: str) -> dict[str, Any]:
        selection = self.runtime.roles[role].model
        if (selection.provider_id, selection.model_id) == ("offline", "offline"):
            return {
                "provider_id": "offline",
                "provider_name": "Offline demo",
                "model_id": "offline",
                "model_name": "Rule-based demo",
                "ready": True,
                "verification_status": "verified",
            }
        try:
            provider = self.model_registry.provider(selection.provider_id)
            model = provider.model(selection.model_id)
            ready = (
                model.verification_status == "verified"
                and bool(provider.selected_api_key_id)
            )
            return {
                "provider_id": provider.id,
                "provider_name": provider.name,
                "model_id": model.id,
                "model_name": model.name,
                "ready": ready,
                "verification_status": model.verification_status,
            }
        except KeyError:
            return {
                "provider_id": selection.provider_id,
                "provider_name": "Missing provider",
                "model_id": selection.model_id,
                "model_name": "Missing model",
                "ready": False,
                "verification_status": "failed",
            }

    def role_model_settings(self, role: str) -> ModelSettings | None:
        selection = self.runtime.roles[role].model
        if (selection.provider_id, selection.model_id) == ("offline", "offline"):
            return None
        return self.model_registry.settings(
            selection.provider_id, selection.model_id, require_verified=True
        )

    def _require_files(self) -> None:
        missing = [
            path.name
            for path in (self.runtime_path, self.models_path, self.scenes_path)
            if not path.is_file()
        ]
        if missing:
            raise FileNotFoundError(
                f"missing configuration in {self.root}: {', '.join(missing)}; "
                "start from the tracked runs2/.config directory"
            )

    def _load_runtime(self) -> RuntimeSettings:
        return RuntimeSettings.model_validate_json(
            self.runtime_path.read_text(encoding="utf-8")
        )

    def _load_scenes(self) -> SceneCatalog:
        value = SceneCatalog.model_validate_json(
            self.scenes_path.read_text(encoding="utf-8")
        )
        ids = [str(item.get("id") or "") for item in value.scenes]
        if not ids or len(ids) != len(set(ids)) or any(not item for item in ids):
            raise ValueError("scenes.json must contain unique non-empty scene ids")
        return value

    def _load_models(self) -> ModelRegistry:
        return ModelRegistry.model_validate_json(
            self.models_path.read_text(encoding="utf-8")
        )

    def _write_json(self, path: Path, payload: dict[str, Any], mode: int | None = None) -> None:
        with self._lock:
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            temporary.replace(path)
            if mode is not None:
                try:
                    os.chmod(path, mode)
                except OSError:
                    pass


__all__ = [
    "DEFAULT_KALI_IMAGE",
    "DEFAULT_KALI_IMAGE_DIGEST",
    "KALI_PROFILE_ID",
    "ROLES",
    "Configuration",
    "KaliSandboxSettings",
    "RoleModelSelection",
    "RuntimeSettings",
]
