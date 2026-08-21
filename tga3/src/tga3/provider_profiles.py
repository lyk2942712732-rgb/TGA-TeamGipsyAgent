"""Canonical provider capabilities and protocol-aware endpoint resolution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit


ProviderProtocol = Literal["openai_responses", "openai_chat_completions", "anthropic"]


@dataclass(frozen=True)
class ProviderProfile:
    id: str
    name: str
    protocols: tuple[ProviderProtocol, ...]
    default_protocol: ProviderProtocol
    base_url: str
    runtime_paths: dict[ProviderProtocol, str]
    models_path: str
    discovery_auth: str = "bearer"
    anthropic_pagination: bool = False

    def public_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "protocols": list(self.protocols),
            "default_protocol": self.default_protocol,
            "base_url": self.base_url,
        }


PROVIDER_PROFILES: tuple[ProviderProfile, ...] = (
    ProviderProfile(
        id="openai",
        name="OpenAI",
        protocols=("openai_responses", "openai_chat_completions"),
        default_protocol="openai_responses",
        base_url="https://api.openai.com",
        runtime_paths={"openai_responses": "/v1", "openai_chat_completions": "/v1"},
        models_path="/v1/models",
    ),
    ProviderProfile(
        id="anthropic",
        name="Anthropic",
        protocols=("anthropic",),
        default_protocol="anthropic",
        base_url="https://api.anthropic.com",
        runtime_paths={"anthropic": ""},
        models_path="/v1/models",
        discovery_auth="anthropic",
        anthropic_pagination=True,
    ),
    ProviderProfile(
        id="deepseek",
        name="DeepSeek",
        protocols=("openai_chat_completions", "anthropic"),
        default_protocol="openai_chat_completions",
        base_url="https://api.deepseek.com",
        runtime_paths={"openai_chat_completions": "", "anthropic": "/anthropic"},
        models_path="/models",
    ),
    ProviderProfile(
        id="gemini",
        name="Google Gemini",
        protocols=("openai_chat_completions",),
        default_protocol="openai_chat_completions",
        base_url="https://generativelanguage.googleapis.com",
        runtime_paths={"openai_chat_completions": "/v1beta/openai"},
        models_path="/v1beta/openai/models",
    ),
    ProviderProfile(
        id="openrouter",
        name="OpenRouter",
        protocols=("openai_chat_completions",),
        default_protocol="openai_chat_completions",
        base_url="https://openrouter.ai",
        runtime_paths={"openai_chat_completions": "/api/v1"},
        models_path="/api/v1/models",
    ),
    ProviderProfile(
        id="groq",
        name="Groq",
        protocols=("openai_chat_completions",),
        default_protocol="openai_chat_completions",
        base_url="https://api.groq.com",
        runtime_paths={"openai_chat_completions": "/openai/v1"},
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


def runtime_base_url(preset_id: str | None, origin: str, protocol: ProviderProtocol) -> str:
    profile = provider_profile(preset_id)
    if profile:
        try:
            path = profile.runtime_paths[protocol]
        except KeyError as exc:
            raise ValueError(f"{profile.name} 不支持协议 {protocol}") from exc
        return join_origin(origin, path)
    path = "" if protocol == "anthropic" else "/v1"
    return join_origin(origin, path)


def discovery_urls(
    preset_id: str | None,
    origin: str,
    protocols: tuple[ProviderProtocol, ...] | list[ProviderProtocol],
) -> tuple[str, ...]:
    profile = provider_profile(preset_id)
    if profile:
        return (join_origin(origin, profile.models_path),)
    normalized = normalize_api_origin(origin)
    paths = (
        ("/v1/models", "/models")
        if set(protocols) == {"anthropic"}
        else ("/models", "/v1/models", "/api/v1/models", "/openai/v1/models")
    )
    return tuple(f"{normalized}{path}" for path in paths)


__all__ = [
    "PROVIDER_PROFILES",
    "ProviderProfile",
    "ProviderProtocol",
    "discovery_urls",
    "normalize_api_origin",
    "provider_profile",
    "runtime_base_url",
]
