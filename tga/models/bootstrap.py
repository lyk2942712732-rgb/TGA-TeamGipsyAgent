"""Build model clients from environment variables."""

from __future__ import annotations

import os

from tga.models.openai_compatible import OpenAICompatibleClient


def build_model_client_from_env() -> OpenAICompatibleClient | None:
    base_url = os.environ.get("TGA_LLM_BASE_URL")
    api_key = os.environ.get("TGA_LLM_API_KEY")
    model = os.environ.get("TGA_LLM_MODEL")
    if not base_url or not api_key or not model:
        return None
    return OpenAICompatibleClient(base_url=base_url, api_key=api_key, model=model)


def model_config_status() -> dict:
    return {
        "configured": build_model_client_from_env() is not None,
        "base_url": os.environ.get("TGA_LLM_BASE_URL", ""),
        "model": os.environ.get("TGA_LLM_MODEL", ""),
    }
