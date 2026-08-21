"""Canonical provider presets and endpoint resolution.

Users configure only an API origin (for example ``https://api.deepseek.com``).
Provider-specific protocol paths stay here instead of leaking into models.json or
the settings UI.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class ProviderProfile:
    id: str
    name: str
    protocol: str
    base_url: str
    runtime_path: str
    models_path: str
    discovery_auth: str = "bearer"
    anthropic_pagination: bool = False

    def public_dict(self) -> dict[str, str]:
        value = asdict(self)
        return {
            "id": value["id"],
            "name": value["name"],
            "protocol": value["protocol"],
            "base_url": value["base_url"],
        }


PROVIDER_PROFILES: tuple[ProviderProfile, ...] = (
    ProviderProfile(
        id="openai",
        name="OpenAI",
        protocol="openai_responses",
        base_url="https://api.openai.com",
        runtime_path="/v1",
        models_path="/v1/models",
    ),
    ProviderProfile(
        id="anthropic",
        name="Anthropic",
        protocol="anthropic",
        base_url="https://api.anthropic.com",
        runtime_path="",
        models_path="/v1/models",
        discovery_auth="anthropic",
        anthropic_pagination=True,
    ),
    ProviderProfile(
        id="deepseek",
        name="DeepSeek",
        protocol="anthropic",
        base_url="https://api.deepseek.com",
        runtime_path="/anthropic",
        models_path="/models",
    ),
    ProviderProfile(
        id="gemini",
        name="Google Gemini",
        protocol="openai_chat_completions",
        base_url="https://generativelanguage.googleapis.com",
        runtime_path="/v1beta/openai",
        models_path="/v1beta/openai/models",
    ),
    ProviderProfile(
        id="openrouter",
        name="OpenRouter",
        protocol="openai_chat_completions",
        base_url="https://openrouter.ai",
        runtime_path="/api/v1",
        models_path="/api/v1/models",
    ),
    ProviderProfile(
        id="groq",
        name="Groq",
        protocol="openai_chat_completions",
        base_url="https://api.groq.com",
        runtime_path="/openai/v1",
        models_path="/openai/v1/models",
    ),
)

_BY_ID = {item.id: item for item in PROVIDER_PROFILES}


def provider_profile(preset_id: str | None) -> ProviderProfile | None:
    return _BY_ID.get(preset_id or "")


def normalize_api_origin(raw: str) -> str:
    """Validate a URL and keep only scheme, host and optional port."""

    parsed = urlsplit(raw.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("API 地址必须是有效的 http(s) 根地址")
    return f"{parsed.scheme}://{parsed.netloc}"


def join_origin(origin: str, path: str) -> str:
    return f"{normalize_api_origin(origin)}{path}"


def runtime_base_url(preset_id: str | None, origin: str, protocol: str) -> str:
    profile = provider_profile(preset_id)
    if profile:
        return join_origin(origin, profile.runtime_path)
    # Custom providers use the conventional SDK roots. Known non-standard
    # layouts should be represented as presets above.
    path = "" if protocol == "anthropic" else "/v1"
    return join_origin(origin, path)


def discovery_urls(preset_id: str | None, origin: str, protocol: str) -> tuple[str, ...]:
    profile = provider_profile(preset_id)
    if profile:
        return (join_origin(origin, profile.models_path),)
    normalized = normalize_api_origin(origin)
    paths = (
        ("/v1/models", "/models")
        if protocol == "anthropic"
        else ("/models", "/v1/models", "/api/v1/models", "/openai/v1/models")
    )
    return tuple(f"{normalized}{path}" for path in paths)


__all__ = [
    "PROVIDER_PROFILES",
    "ProviderProfile",
    "discovery_urls",
    "normalize_api_origin",
    "provider_profile",
    "runtime_base_url",
]
