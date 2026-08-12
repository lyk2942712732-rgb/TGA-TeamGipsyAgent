"""One persisted configuration source shared by API, CLI and Runtime."""

from __future__ import annotations

import json
import os
from pathlib import Path
from threading import RLock
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from tga2.core.models import SUPPORTED_MODES
from tga2.integrations.model import (
    ModelRegistry,
    ModelSettings,
    RegisteredAPIKey,
    RegisteredModel,
    RegisteredProvider,
)

DEFAULT_PROMPTS = {
    "common": "Evidence first. Never expand task authorization.",
    "supervisor": "",
    "worker": "",
    "reviewer": "",
    "reporter": "",
}

KALI_PROFILE_ID = "tga2-kali"
DEFAULT_KALI_IMAGE = (
    "ghcr.io/lyk2942712732-rgb/tga-kali-universal:sandbox-v0.2.1"
)
DEFAULT_KALI_IMAGE_DIGEST = (
    "sha256:300fca8aaf785e6f8a589e595e08b0174657609e0ca1935960b5bbfa28e7970f"
)


class KaliSandboxSettings(BaseModel):
    """The single Kali image profile used by LangChain's Docker policy."""

    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    profile_id: str = KALI_PROFILE_ID
    image: str = DEFAULT_KALI_IMAGE
    expected_digest: str | None = Field(
        default=DEFAULT_KALI_IMAGE_DIGEST,
        pattern=r"^sha256:[a-fA-F0-9]{64}$",
    )


class RuntimeSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompts: dict[str, str] = Field(default_factory=lambda: dict(DEFAULT_PROMPTS))
    mode_prompts: list[dict[str, Any]] = Field(default_factory=list)
    solver_tools: dict[str, list[str]] = Field(
        default_factory=lambda: {
            "supervisor": [],
            "worker": [
                "list_inputs",
                "read_input",
                "glob_search",
                "grep_search",
                "save_note",
                "run_command",
            ],
            "reviewer": [],
            "reporter": [],
        }
    )
    kali: KaliSandboxSettings = Field(default_factory=KaliSandboxSettings)

    @property
    def sandbox_image(self) -> str | None:
        """Compatibility projection consumed by the agent composition root."""

        return self.kali.image if self.kali.enabled else None


class Configuration:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "runtime.json"
        self.model_path = self.root / "model.json"
        self.model_registry_path = self.root / "model-registry.json"
        self._lock = RLock()
        self.runtime = self._load()
        self.model = self._load_model()
        self.model_registry = self._load_model_registry()
        active = self.model_registry.active_settings()
        if active is not None:
            self.model = active

    def save(self) -> None:
        with self._lock:
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(
                self.runtime.model_dump_json(indent=2), encoding="utf-8"
            )
            temporary.replace(self.path)

    def save_model(self, settings: ModelSettings) -> None:
        with self._lock:
            secret = settings.api_key.get_secret_value() if settings.api_key else None
            payload = settings.model_dump(mode="json", exclude={"api_key"})
            payload["api_key"] = secret
            temporary = self.model_path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            temporary.replace(self.model_path)
            try:
                os.chmod(self.model_path, 0o600)
            except OSError:
                pass
            self.model = settings

    def save_model_registry(self) -> None:
        with self._lock:
            temporary = self.model_registry_path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(
                    self.model_registry.persisted_payload(),
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            temporary.replace(self.model_registry_path)
            try:
                os.chmod(self.model_registry_path, 0o600)
            except OSError:
                pass

    def update_prompts(self, payload: dict[str, Any]) -> dict[str, Any]:
        prompts = dict(self.runtime.prompts)
        common = payload.get("common_system_prompt")
        if common is not None:
            prompts["common"] = str(common)
        for role in ("supervisor", "worker", "reviewer", "reporter"):
            if role in payload:
                prompts[role] = str(payload[role])
        modes = payload.get("modes", self.runtime.mode_prompts)
        if not isinstance(modes, list):
            raise TypeError("modes must be a list")
        modes = [
            item
            for item in modes
            if isinstance(item, dict) and item.get("id") in SUPPORTED_MODES
        ]
        self.runtime = self.runtime.model_copy(
            update={"prompts": prompts, "mode_prompts": modes}
        )
        self.save()
        return self.prompt_payload()

    def prompt_payload(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "common_system_prompt": self.runtime.prompts.get("common", ""),
            "modes": self.runtime.mode_prompts,
            **{
                role: self.runtime.prompts.get(role, "")
                for role in ("supervisor", "worker", "reviewer", "reporter")
            },
        }

    def agent_prompts(self) -> dict[str, Any]:
        return {**self.runtime.prompts, "__modes__": self.runtime.mode_prompts}

    def update_solver_tools(self, solver_id: str, tools: list[str]) -> None:
        values = dict(self.runtime.solver_tools)
        values[solver_id] = list(dict.fromkeys(tools))
        self.runtime = self.runtime.model_copy(update={"solver_tools": values})
        self.save()

    def update_kali(self, settings: KaliSandboxSettings) -> None:
        self.runtime = self.runtime.model_copy(update={"kali": settings})
        self.save()

    def _load(self) -> RuntimeSettings:
        if not self.path.is_file():
            return RuntimeSettings()
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        # Migrate the first TGA2 rewrite, which stored one nullable image string
        # and whose UI could overwrite it with kali-rolling.  A null or that
        # obsolete default now selects the released project image.
        legacy_image = payload.pop("sandbox_image", None)
        if "kali" not in payload:
            image = (
                legacy_image
                if legacy_image and legacy_image != "kalilinux/kali-rolling:latest"
                else DEFAULT_KALI_IMAGE
            )
            payload["kali"] = {
                "enabled": True,
                "profile_id": KALI_PROFILE_ID,
                "image": image,
                "expected_digest": DEFAULT_KALI_IMAGE_DIGEST
                if image == DEFAULT_KALI_IMAGE
                else None,
            }
        payload["mode_prompts"] = [
            item
            for item in payload.get("mode_prompts", [])
            if isinstance(item, dict) and item.get("id") in SUPPORTED_MODES
        ]
        return RuntimeSettings.model_validate(payload)

    def _load_model(self) -> ModelSettings:
        if not self.model_path.is_file():
            return ModelSettings.from_env()
        payload = json.loads(self.model_path.read_text(encoding="utf-8"))
        return ModelSettings.model_validate(payload)

    def _load_model_registry(self) -> ModelRegistry:
        if self.model_registry_path.is_file():
            return ModelRegistry.model_validate_json(
                self.model_registry_path.read_text(encoding="utf-8")
            )
        # Migrate the previous single-model configuration.  Provider display
        # names were not persisted before this schema, so infer well-known
        # OpenAI-compatible endpoints where possible.
        current = self.model
        if not current.api_key or not current.api_key.get_secret_value():
            return ModelRegistry()
        host = (current.base_url or "").casefold()
        if "deepseek" in host:
            name, preset = "DeepSeek", "deepseek"
        elif "openrouter" in host:
            name, preset = "OpenRouter", "openrouter"
        else:
            name, preset = "OpenAI", "openai"
        model = RegisteredModel(
            name=current.model,
            verification_status="verified" if current.verified else "unverified",
        )
        key = RegisteredAPIKey(label="Migrated key", api_key=current.api_key)
        provider = RegisteredProvider(
            name=name,
            preset_id=preset,
            model_provider="openai",
            base_url=current.base_url,
            models=[model],
            api_keys=[key],
            selected_api_key_id=key.id,
        )
        registry = ModelRegistry(
            providers=[provider],
            active_provider_id=provider.id if current.verified else None,
            active_model_id=model.id if current.verified else None,
        )
        self.model_registry = registry
        self.save_model_registry()
        return registry


__all__ = [
    "DEFAULT_KALI_IMAGE",
    "DEFAULT_KALI_IMAGE_DIGEST",
    "KALI_PROFILE_ID",
    "Configuration",
    "KaliSandboxSettings",
    "RuntimeSettings",
]
