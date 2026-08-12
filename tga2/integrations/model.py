"""Small provider configuration; LangChain owns provider-specific clients."""

from __future__ import annotations

import os

from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel
from pydantic import BaseModel, ConfigDict, Field, SecretStr


class ModelSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = "openai"
    model: str = "gpt-5-mini"
    api_key: SecretStr | None = None
    base_url: str | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_retries: int = Field(default=2, ge=0, le=10)
    offline: bool = True
    verified: bool = False

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
        return not self.offline and self.api_key is not None


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


__all__ = ["ModelSettings", "build_chat_model"]
