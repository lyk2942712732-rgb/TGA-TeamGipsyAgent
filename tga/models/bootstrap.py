"""Build model clients from browser-managed settings or environment fallback."""

from __future__ import annotations

from tga.models.openai_compatible import OpenAICompatibleClient
from tga.models.settings import effective_model_settings


def build_model_client() -> OpenAICompatibleClient | None:
    settings = effective_model_settings()
    api_key = settings["api_key"]
    base_url = settings["base_url"]
    model = settings["model"]
    if not api_key or not base_url or not model:
        return None
    return OpenAICompatibleClient(
        base_url=base_url, api_key=api_key, model=model,
        supports_vision=settings["supports_vision"], temperature=settings["temperature"],
        timeout_s=settings["timeout_seconds"],
        max_tokens=settings["max_output_tokens"],
        reasoning_mode=settings["reasoning_mode"],
        provider_name="openai-compatible",
    )


def model_config_status() -> dict:
    settings = effective_model_settings()
    client = build_model_client()
    return {
        "configured": client is not None,
        "base_url": settings["base_url"],
        "model": settings["model"],
        "provider": "openai-compatible",
        "api_key_set": bool(settings["api_key"]),
        "browser_configured": settings["browser_configured"],
        "temperature": settings["temperature"],
        "max_output_tokens": settings["max_output_tokens"],
        "timeout_seconds": settings["timeout_seconds"],
        "reasoning_mode": settings["reasoning_mode"],
        "supports_vision": settings["supports_vision"],
        "verification_status": settings["verification_status"],
        "verification": settings["verification"],
        "chat_completions_url": client.chat_completions_url if client else "",
    }
