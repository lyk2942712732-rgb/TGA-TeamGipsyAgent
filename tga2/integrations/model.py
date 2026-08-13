"""Persisted model registry and LangChain provider adapters."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel
from pydantic import BaseModel, ConfigDict, Field, SecretStr


def utc_now() -> datetime:
    return datetime.now(UTC)


class ModelSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = "openai"
    preset_id: str = "custom"
    model: str = "gpt-5-mini"
    api_key: SecretStr | None = None
    base_url: str | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_retries: int = Field(default=2, ge=0, le=10)
    offline: bool = True
    verified: bool = False
    reasoning_mode: str = "auto"

    @classmethod
    def from_env(cls) -> ModelSettings:
        key = os.getenv("TGA2_MODEL_API_KEY") or os.getenv("OPENAI_API_KEY")
        return cls(
            provider=os.getenv("TGA2_MODEL_PROVIDER", "openai"),
            model=os.getenv("TGA2_MODEL", "gpt-5-mini"),
            api_key=SecretStr(key) if key else None,
            base_url=os.getenv("TGA2_MODEL_BASE_URL") or None,
            offline=(
                os.getenv("TGA2_OFFLINE", "").casefold() in {"1", "true", "yes"}
                or not key
            ),
        )

    @property
    def can_call_model(self) -> bool:
        return (
            not self.offline
            and self.api_key is not None
            and bool(self.api_key.get_secret_value())
        )

    @property
    def supports_forced_tool_choice(self) -> bool:
        """Whether Agent structured-output tools may force ``tool_choice``.

        LangChain's ToolStrategy uses ``tool_choice=required`` to obtain the
        final Pydantic response.  DeepSeek thinking endpoints reject that
        parameter even though they support ordinary, model-selected tools.
        Explicit reasoning mode receives the same conservative treatment for
        OpenAI-compatible gateways whose exact capability cannot be inferred.
        """

        if self.reasoning_mode.casefold() == "disabled":
            return True
        identity = " ".join(
            (
                self.preset_id,
                self.provider,
                self.model,
                self.base_url or "",
            )
        ).casefold()
        return self.reasoning_mode.casefold() != "enabled" and "deepseek" not in identity


class RegisteredModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: f"model_{uuid4().hex[:12]}")
    name: str
    max_output_tokens: int = 8192
    timeout_seconds: int = 120
    temperature: float | None = None
    reasoning_mode: str = "auto"
    verification_status: str = "unverified"
    verified_at: datetime | None = None
    last_error: dict[str, str] | None = None


class RegisteredAPIKey(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: f"key_{uuid4().hex[:12]}")
    label: str = "Active"
    api_key: SecretStr
    created_at: datetime = Field(default_factory=utc_now)


class RegisteredProvider(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: f"provider_{uuid4().hex[:12]}")
    name: str
    preset_id: str = "custom"
    # DeepSeek, OpenRouter and most competition gateways expose an
    # OpenAI-compatible protocol.  This is the LangChain adapter name, not the
    # user-facing provider name.
    model_provider: str = "openai"
    base_url: str | None = None
    models: list[RegisteredModel] = Field(default_factory=list)
    api_keys: list[RegisteredAPIKey] = Field(default_factory=list)
    selected_api_key_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    def model(self, model_id: str) -> RegisteredModel:
        value = next((item for item in self.models if item.id == model_id), None)
        if value is None:
            raise KeyError(f"model not found: {model_id}")
        return value

    def selected_key(self) -> RegisteredAPIKey:
        value = next(
            (item for item in self.api_keys if item.id == self.selected_api_key_id),
            None,
        )
        if value is None:
            raise KeyError(f"provider has no selected API key: {self.id}")
        return value

    def settings(self, model_id: str, *, require_verified: bool = False) -> ModelSettings:
        model = self.model(model_id)
        key = self.selected_key()
        if require_verified and model.verification_status != "verified":
            raise ValueError(f"model is not verified: {self.id}/{model.id}")
        return ModelSettings(
            provider=self.model_provider,
            preset_id=self.preset_id,
            model=model.name,
            api_key=key.api_key,
            base_url=self.base_url,
            temperature=model.temperature,
            offline=False,
            verified=model.verification_status == "verified",
            reasoning_mode=model.reasoning_mode,
        )


class ModelRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    presets: list[dict[str, str]] = Field(default_factory=list)
    providers: list[RegisteredProvider] = Field(default_factory=list)
    active_provider_id: str | None = None
    active_model_id: str | None = None

    def provider(self, provider_id: str) -> RegisteredProvider:
        value = next((item for item in self.providers if item.id == provider_id), None)
        if value is None:
            raise KeyError(f"provider not found: {provider_id}")
        return value

    def settings(
        self, provider_id: str, model_id: str, *, require_verified: bool = False
    ) -> ModelSettings:
        return self.provider(provider_id).settings(
            model_id, require_verified=require_verified
        )

    def active_settings(self) -> ModelSettings | None:
        if not self.active_provider_id or not self.active_model_id:
            return None
        try:
            return self.settings(
                self.active_provider_id,
                self.active_model_id,
                require_verified=True,
            )
        except (KeyError, ValueError):
            return None

    def persisted_payload(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json", exclude={"providers"})
        payload["providers"] = []
        for provider in self.providers:
            value = provider.model_dump(mode="json", exclude={"api_keys"})
            value["api_keys"] = [
                {
                    **key.model_dump(mode="json", exclude={"api_key"}),
                    "api_key": key.api_key.get_secret_value(),
                }
                for key in provider.api_keys
            ]
            payload["providers"].append(value)
        return payload


def build_chat_model(settings: ModelSettings) -> BaseChatModel:
    if not settings.can_call_model:
        raise RuntimeError("a model API key is required unless TGA2_OFFLINE=1")
    kwargs: dict[str, object] = {
        "api_key": settings.api_key.get_secret_value(),
        "max_retries": settings.max_retries,
    }
    if settings.base_url:
        kwargs["base_url"] = settings.base_url
    if settings.temperature is not None:
        kwargs["temperature"] = settings.temperature
    return init_chat_model(settings.model, model_provider=settings.provider, **kwargs)


__all__ = [
    "ModelRegistry",
    "ModelSettings",
    "RegisteredAPIKey",
    "RegisteredModel",
    "RegisteredProvider",
    "build_chat_model",
    "utc_now",
]
