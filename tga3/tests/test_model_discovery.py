from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from tga3.config import ModelConfig, TGA3Config
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

    result = await discover_provider_models(provider, "openai_responses", transport=httpx.MockTransport(handler))

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

    result = await discover_provider_models(provider, "anthropic", transport=httpx.MockTransport(handler))

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

    result = await discover_provider_models(provider, "openai_chat_completions", transport=httpx.MockTransport(handler))

    assert result == ["deepseek-v4-flash", "deepseek-v4-pro"]
    assert seen == {
        "url": "https://api.deepseek.com/models",
        "authorization": "Bearer deepseek-secret",
    }
    assert provider.sdk_base_url("anthropic") == "https://api.deepseek.com/anthropic"
    assert provider.sdk_base_url("openai_chat_completions") == "https://api.deepseek.com"


def test_deepseek_protocol_is_selected_by_each_agent_binding():
    config = TGA3Config(Path(__file__).parents[1] / "config")
    provider = config.models.provider("deepseek")
    provider.endpoint("anthropic").models.append(ModelConfig(id="deepseek-v4", name="deepseek-v4"))

    with pytest.raises(ValueError, match="Responses"):
        config.resolve_agent(
            "worker-openai", "deepseek", "deepseek-v4", "openai_chat_completions", require_api_key=False
        )
    claude_binding = config.resolve_agent(
        "worker-claude",
        "deepseek",
        "deepseek-v4",
        "anthropic",
        require_api_key=False,
    )

    assert claude_binding.protocol == "anthropic"
    assert claude_binding.provider.sdk_base_url(claude_binding.protocol) == "https://api.deepseek.com/anthropic"
    with pytest.raises(ValueError, match="Responses"):
        config.resolve_agent(
            "worker-openai",
            "deepseek",
            "deepseek-v4",
            "anthropic",
            require_api_key=False,
        )


@pytest.mark.asyncio
async def test_custom_endpoint_uses_exact_path_for_discovery_and_runtime():
    from tga3.config import ProviderConfig

    provider = ProviderConfig(id="gateway", name="Gateway", api_keys=[
        {"id": "key", "api_key": "custom-secret"}
    ], selected_api_key_id="key", endpoints={
        "openai_responses": {"base_url": "https://gateway.test/supplied/path/api/v2"},
        "anthropic": {"base_url": "https://gateway.test/apps/anthropic"},
    })
    calls = []

    def handler(request):
        calls.append(str(request.url))
        assert request.headers["authorization"] == "Bearer custom-secret"
        return httpx.Response(200, json={"data": [{"id": "custom-model"}]})

    result = await discover_provider_models(provider, "openai_responses", transport=httpx.MockTransport(handler))
    assert result == ["custom-model"]
    assert calls == ["https://gateway.test/supplied/path/api/v2/models"]
    assert provider.sdk_base_url("openai_responses") == "https://gateway.test/supplied/path/api/v2"
    assert provider.sdk_base_url("anthropic") == "https://gateway.test/apps/anthropic"


def test_custom_gateway_prefixes_survive_config_and_runtime_resolution():
    from tga3.config import ProviderConfig

    common = dict(id="gateway", name="Gateway", api_keys=[], selected_api_key_id="", models=[])
    responses = ProviderConfig(
        **common, protocols=["openai_responses"], base_url="https://gateway.test/compatible-mode/v1"
    )
    assert responses.endpoint("openai_responses").base_url == "https://gateway.test/compatible-mode/v1"
    assert responses.sdk_base_url("openai_responses") == "https://gateway.test/compatible-mode/v1"
    anthropic = ProviderConfig(**common, protocols=["anthropic"], base_url="https://gateway.test/apps/anthropic")
    assert anthropic.sdk_base_url("anthropic") == "https://gateway.test/apps/anthropic"


@pytest.mark.asyncio
async def test_multi_protocol_provider_uses_anthropic_auth_and_accepts_empty_catalog():
    from tga3.config import ProviderConfig

    provider = ProviderConfig(id="gateway", name="Gateway", api_keys=[{"id": "key", "api_key": "test-only"}],
                              selected_api_key_id="key", endpoints={
                                  "openai_responses": {"base_url": "https://gateway.test/responses/v1"},
                                  "anthropic": {"base_url": "https://gateway.test/apps/anthropic"},
                              })

    def handler(request):
        assert request.url.path == "/apps/anthropic/v1/models"
        assert request.headers["x-api-key"] == "test-only"
        assert "authorization" not in request.headers
        return httpx.Response(200, json={"data": [], "has_more": False})

    assert await discover_provider_models(provider, "anthropic", transport=httpx.MockTransport(handler)) == []


def fallback_provider(openai_protocol="openai_responses", models=None):
    from tga3.config import ProviderConfig

    endpoints = {"anthropic": {"base_url": "https://messages.example.test/custom"}}
    if openai_protocol:
        endpoints[openai_protocol] = {"base_url": "https://catalog.example.test/gateway/v2", "models": models or []}
    return ProviderConfig(id="gateway", name="Gateway", api_keys=[{"id": "key", "api_key": "test-only"}],
                          selected_api_key_id="key", endpoints=endpoints)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [404, 405, 501])
@pytest.mark.parametrize("protocol", ["openai_responses", "openai_chat_completions"])
async def test_messages_without_catalog_reuses_own_openai_names_including_disabled_models(status, protocol):
    provider = fallback_provider(protocol, [
        {"id": "alias", "name": "remote-name", "enabled": False},
        {"id": "enabled", "name": "enabled", "enabled": True},
        {"id": "missing", "name": "missing", "available": False},
    ])
    calls = []

    def handler(request):
        calls.append(str(request.url))
        assert request.headers["x-api-key"] == "test-only"
        return httpx.Response(status)

    names = await discover_provider_models(provider, "anthropic", transport=httpx.MockTransport(handler))
    assert names == ["remote-name", "enabled"]
    assert calls == ["https://messages.example.test/custom/v1/models?limit=100"]
    assert provider.sdk_base_url("anthropic") == "https://messages.example.test/custom"
    assert not provider.endpoint(protocol).models[0].enabled
    assert provider.endpoint("anthropic").models == []


@pytest.mark.asyncio
async def test_messages_fallback_reads_configured_openai_endpoint_when_catalog_not_loaded():
    provider = fallback_provider()
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if request.url.host == "messages.example.test":
            return httpx.Response(404)
        assert request.url.host == "catalog.example.test"
        assert request.url.path == "/gateway/v2/models"
        assert request.headers["authorization"] == "Bearer test-only"
        assert "x-api-key" not in request.headers
        assert not request.url.params
        return httpx.Response(200, json={"data": [{"id": "shared-model"}]})

    result = await discover_provider_models(provider, "anthropic", transport=httpx.MockTransport(handler))
    assert result == ["shared-model"]
    assert len(calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 429, 500])
async def test_messages_auth_rate_limit_and_server_errors_do_not_trigger_catalog_fallback(status):
    provider = fallback_provider(models=[{"id": "cached", "name": "cached"}])

    with pytest.raises(ValueError, match=f"HTTP {status}"):
        await discover_provider_models(provider, "anthropic", transport=httpx.MockTransport(
            lambda request: httpx.Response(status)
        ))


@pytest.mark.asyncio
async def test_messages_without_own_openai_endpoint_does_not_guess_another_address():
    provider = fallback_provider(None)
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(404)

    with pytest.raises(ValueError, match="请先为此供应商配置 OpenAI 端点"):
        await discover_provider_models(provider, "anthropic", transport=httpx.MockTransport(handler))
    assert len(calls) == 1
