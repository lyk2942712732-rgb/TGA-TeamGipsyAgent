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


@pytest.mark.asyncio
@pytest.mark.parametrize("host", [
    "workspace.cn-beijing.maas.aliyuncs.com",
    "workspace.ap-southeast-1.maas.aliyuncs.com",
])
async def test_aliyun_messages_uses_shared_catalog_with_bearer_auth_without_changing_runtime(host):
    from tga3.config import ProviderConfig

    provider = ProviderConfig(id="aliyun", name="Aliyun", api_keys=[{"id": "key", "api_key": "test-only"}],
                              selected_api_key_id="key", endpoints={
                                  "anthropic": {"base_url": f"https://{host}/apps/anthropic"},
                              })
    calls = []

    def handler(request):
        calls.append(str(request.url))
        assert request.headers["authorization"] == "Bearer test-only"
        assert "x-api-key" not in request.headers
        assert "anthropic-version" not in request.headers
        assert not request.url.params
        return httpx.Response(200, json={"data": [{"id": "qwen-model"}]})

    result = await discover_provider_models(provider, "anthropic", transport=httpx.MockTransport(handler))
    assert result == ["qwen-model"]
    assert calls == [f"https://{host}/compatible-mode/v1/models"]
    assert provider.sdk_base_url("anthropic") == f"https://{host}/apps/anthropic"


@pytest.mark.asyncio
async def test_non_aliyun_messages_endpoint_does_not_use_aliyun_catalog_mapping():
    from tga3.config import ProviderConfig

    base = "https://maas.aliyuncs.com.example.test/apps/anthropic"
    provider = ProviderConfig(id="gateway", name="Gateway", api_keys=[{"id": "key", "api_key": "test-only"}],
                              selected_api_key_id="key", endpoints={"anthropic": {"base_url": base}})
    calls = []

    def handler(request):
        calls.append(request.url.path)
        assert request.headers["x-api-key"] == "test-only"
        return httpx.Response(404)

    with pytest.raises(ValueError, match="HTTP 404"):
        await discover_provider_models(provider, "anthropic", transport=httpx.MockTransport(handler))
    assert calls == ["/apps/anthropic/v1/models"]
