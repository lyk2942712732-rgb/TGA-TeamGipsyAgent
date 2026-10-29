from pathlib import Path
from shutil import copytree

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from tga3.app import create_app
from tga3.config import ConfigBundle, TGA3Config
from tga3.docker_runtime import FakeContainerRuntime
from tga3.host_agents import DeterministicHostModel
from tga3.storage import InMemoryStorage


def test_removed_providers_leave_unconfigured_solvers_and_readable_history(tmp_path):
    copytree(Path(__file__).parents[1] / "config", tmp_path / "config")
    config = TGA3Config(tmp_path / "config")
    for provider in config.models.providers:
        provider.api_keys[0].api_key = SecretStr("test-key")
    for name in ("workspace_root", "input_root", "artifact_root", "writeup_root"):
        setattr(config.runtime, name, str(tmp_path / name))
    app = create_app(config, InMemoryStorage(), FakeContainerRuntime(), host_model=DeterministicHostModel())
    with TestClient(app) as client:
        created = client.post("/api/v3/tasks", data={
            "title": "historical task", "prompt": "test", "scene_id": "penetration_test",
        })
        assert created.status_code == 200
        task_id = created.json()["id"]
        models = config.models.model_copy(deep=True)
        models.providers = []
        agents = config.agents.model_copy(deep=True)
        for binding in agents.agents.values():
            binding.provider_id = binding.model_id = ""
        config.apply_bundle(ConfigBundle(models=models, agents=agents, scenes=config.scenes, runtime=config.runtime))
        reloaded = TGA3Config(tmp_path / "config")
        assert not reloaded.models.providers
        with pytest.raises(ValueError, match="尚未配置模型"):
            reloaded.resolve_agent("worker-openai")
        historical = client.get(f"/api/v3/tasks/{task_id}")
        assert historical.status_code == 200
        assert next(a for a in historical.json()["agents"] if a["agent_id"] == "worker-openai")[
            "model_name"
        ] == "openai-worker-model"
        rejected = client.post("/api/v3/tasks", data={
            "title": "unconfigured task", "prompt": "test", "scene_id": "penetration_test",
        })
        assert rejected.status_code == 422
        assert "尚未配置模型" in rejected.text
        assert len(client.get("/api/v3/tasks").json()) == 1
        assert client.delete(f"/api/v3/tasks/{task_id}").status_code == 204
