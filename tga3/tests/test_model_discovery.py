from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from tga3.config import TGA3Config
from tga3.model_discovery import discover_provider_models


@pytest.mark.asyncio
async def test_openai_compatible_discovery_uses_selected_key_and_models_endpoint():
    config = TGA3Config(Path(__file__).parents[1] / "config")
    provider = config.models.provider("openai").model_copy(deep=True)
    provider.api_keys[0].api_key = SecretStr("selected-secret")
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers.get("authorization", "")
        return httpx.Response(200, json={"data": [{"id": "gpt-a"}, {"id": "gpt-b"}, {"id": "gpt-a"}]})

    result = await discover_provider_models(provider, transport=httpx.MockTransport(handler))

    assert result == ["gpt-a", "gpt-b"]
    assert seen == {
        "url": "https://api.openai.com/v1/models",
        "authorization": "Bearer selected-secret",
    }


@pytest.mark.asyncio
async def test_anthropic_discovery_follows_model_pagination():
    config = TGA3Config(Path(__file__).parents[1] / "config")
    provider = config.models.provider("anthropic").model_copy(deep=True)
    provider.api_keys[0].api_key = SecretStr("anthropic-secret")
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        assert request.headers["x-api-key"] == "anthropic-secret"
        if "after_id" not in request.url.params:
            return httpx.Response(
                200,
                json={"data": [{"id": "claude-a"}], "has_more": True, "last_id": "claude-a"},
            )
        return httpx.Response(200, json={"data": [{"id": "claude-b"}], "has_more": False})

    result = await discover_provider_models(provider, transport=httpx.MockTransport(handler))

    assert result == ["claude-a", "claude-b"]
    assert calls[0].startswith("https://api.anthropic.com/v1/models?limit=100")
    assert "after_id=claude-a" in calls[1]


@pytest.mark.asyncio
async def test_deepseek_uses_root_models_endpoint_and_anthropic_runtime_endpoint():
    config = TGA3Config(Path(__file__).parents[1] / "config")
    provider = config.models.provider("deepseek").model_copy(deep=True)
    provider.api_keys[0].api_key = SecretStr("deepseek-secret")
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers.get("authorization", "")
        return httpx.Response(
            200,
            json={"data": [{"id": "deepseek-v4-flash"}, {"id": "deepseek-v4-pro"}]},
        )

    result = await discover_provider_models(provider, transport=httpx.MockTransport(handler))

    assert result == ["deepseek-v4-flash", "deepseek-v4-pro"]
    assert seen == {
        "url": "https://api.deepseek.com/models",
        "authorization": "Bearer deepseek-secret",
    }
    assert provider.sdk_base_url() == "https://api.deepseek.com/anthropic"


@pytest.mark.asyncio
async def test_custom_openai_provider_probes_common_paths_from_origin():
    config = TGA3Config(Path(__file__).parents[1] / "config")
    provider = config.models.provider("openrouter").model_copy(
        update={"preset_id": "custom", "base_url": "https://gateway.example.test/supplied/path"},
        deep=True,
    )
    provider.api_keys[0].api_key = SecretStr("custom-secret")
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.path != "/v1/models":
            return httpx.Response(404)
        return httpx.Response(200, json={"data": [{"id": "custom-model"}]})

    result = await discover_provider_models(provider, transport=httpx.MockTransport(handler))

    assert result == ["custom-model"]
    assert calls == [
        "https://gateway.example.test/models",
        "https://gateway.example.test/v1/models",
    ]
