from copy import deepcopy
from pathlib import Path
from shutil import copytree
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from tga3.app import create_app
from tga3.config import ProviderConfig, TGA3Config
from tga3.docker_runtime import FakeContainerRuntime
from tga3.host_agents import DeterministicHostModel
from tga3.storage import InMemoryStorage


def test_requires_nonempty_explicit_endpoint_and_preserves_paths():
    common = dict(id="gateway", name="Gateway", api_keys=[], selected_api_key_id="")
    with pytest.raises(ValidationError, match="至少填写一个协议端点"):
        ProviderConfig(**common, endpoints={})
    with pytest.raises(ValidationError, match="有效"):
        ProviderConfig(**common, endpoints={"anthropic": {"base_url": ""}})
    provider = ProviderConfig(
        **common,
        endpoints={
            "openai_responses": {"base_url": "https://gateway.test/compatible-mode/v1/"},
            "anthropic": {"base_url": "https://gateway.test/apps/anthropic"},
        },
    )
    assert provider.sdk_base_url("openai_responses") == "https://gateway.test/compatible-mode/v1"
    assert provider.sdk_base_url("anthropic") == "https://gateway.test/apps/anthropic"
    with pytest.raises(ValidationError, match="重复模型"):
        ProviderConfig(
            **common,
            endpoints={
                "anthropic": {
                    "base_url": "https://gateway.test",
                    "models": [{"id": "a", "name": "a"}] * 2,
                }
            },
        )


def test_legacy_configs_migrate_once_with_independent_model_choices():
    provider = ProviderConfig(
        id="gateway",
        name="Gateway",
        preset_id="deepseek",
        api_keys=[],
        selected_api_key_id="",
        base_url="https://api.deepseek.com",
        protocols=["openai_chat_completions", "anthropic"],
        models=[{"id": "model", "name": "model"}],
    )
    assert provider.sdk_base_url("anthropic") == "https://api.deepseek.com/anthropic"
    assert provider.sdk_base_url("openai_chat_completions") == "https://api.deepseek.com"
    provider.model("model", "anthropic").enabled = False
    assert provider.model("model", "openai_chat_completions").enabled
    document = provider.model_dump(mode="json")
    assert not {"protocols", "base_url", "models"}.intersection(document)
    assert ProviderConfig.model_validate(document).model_dump(mode="json") == document


def test_discovery_and_enable_state_are_scoped_to_protocol_and_persist(tmp_path):
    config_dir = tmp_path / "config"
    copytree(Path(__file__).parents[1] / "config", config_dir)
    config = TGA3Config(config_dir)
    for provider in config.models.providers:
        provider.api_keys[0].api_key = SecretStr("test-only")
    for attribute in ("workspace_root", "input_root", "artifact_root", "writeup_root"):
        setattr(config.runtime, attribute, str(tmp_path / attribute))
    original_agents = deepcopy(config.export_document("agents"))
    provider = config.models.provider("openai")
    sibling_before = provider.endpoint("openai_chat_completions").model_dump(mode="json")
    app = create_app(config, InMemoryStorage(), FakeContainerRuntime(), host_model=DeterministicHostModel())
    path = "/api/v3/config/models/openai"
    with TestClient(app) as client:
        with patch(
            "tga3.app.discover_provider_models", new=AsyncMock(return_value=["gpt-5.6", "new-model"])
        ) as discover:
            result = client.post(f"{path}/discover?protocol=openai_responses")
        assert result.status_code == 200
        assert discover.call_args.args[1] == "openai_responses"
        assert (
            config.models.provider("openai").endpoint("openai_chat_completions").model_dump(mode="json")
            == sibling_before
        )
        assert not config.models.provider("openai").model("new-model", "openai_responses").enabled
        binding = deepcopy(original_agents)
        binding["agents"]["worker-openai"]["model_id"] = "new-model"
        assert client.put("/api/v3/config/agents", json=binding).status_code == 422
        assert (
            client.patch(
                f"{path}/endpoints/openai_responses/models",
                json={
                    "model_ids": ["new-model"],
                    "enabled": True,
                },
            ).status_code
            == 200
        )
        assert client.put("/api/v3/config/agents", json=binding).status_code == 200
        assert config.resolve_agent("worker-openai").model.id == "new-model"
        assert (
            client.patch(
                f"{path}/endpoints/openai_chat_completions/models",
                json={
                    "model_ids": ["new-model"],
                    "enabled": True,
                },
            ).status_code
            == 422
        )
        with patch(
            "tga3.app.discover_provider_models", new=AsyncMock(return_value=["gpt-5.6", "new-model", "another"])
        ):
            assert client.post(f"{path}/discover?protocol=openai_responses").status_code == 200
        assert config.resolve_agent("worker-openai").model.enabled
        assert not config.models.provider("openai").model("another", "openai_responses").enabled
        # Disabling a bound model is allowed, but launching/switching to it is not.
        assert (
            client.patch(
                f"{path}/endpoints/openai_responses/models",
                json={
                    "model_ids": ["new-model"],
                    "enabled": False,
                },
            ).status_code
            == 200
        )
        with pytest.raises(ValueError, match="未启用"):
            config.resolve_agent("worker-openai")
        with patch("tga3.app.discover_provider_models", new=AsyncMock(return_value=["gpt-5.6"])):
            assert client.post(f"{path}/discover?protocol=openai_responses").status_code == 200
        assert config.agents.agents["worker-openai"].model_id == "new-model"
        assert not config.models.provider("openai").model("new-model", "openai_responses").available
        assert (
            client.patch(
                f"{path}/endpoints/openai_responses/models",
                json={
                    "model_ids": ["new-model"],
                    "enabled": True,
                },
            ).status_code
            == 422
        )
    reloaded = TGA3Config(config_dir)
    assert not reloaded.models.provider("openai").model("new-model", "openai_responses").enabled
    assert (
        reloaded.models.provider("openai").endpoint("openai_chat_completions").model_dump(mode="json") == sibling_before
    )
