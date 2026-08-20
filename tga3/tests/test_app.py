from copy import deepcopy
from pathlib import Path
from shutil import copytree

from fastapi.testclient import TestClient
from pydantic import SecretStr

from tga3.app import create_app
from tga3.config import TGA3Config
from tga3.docker_runtime import FakeContainerRuntime
from tga3.host_agents import DeterministicHostModel
from tga3.storage import InMemoryStorage


def test_frontend_api_uses_safe_catalog_and_writable_config_source(tmp_path: Path):
    config_dir = tmp_path / "config"
    copytree(Path(__file__).parents[1] / "config", config_dir)
    config = TGA3Config(config_dir)
    for provider in config.models.providers:
        provider.api_keys[0].api_key = SecretStr("must-not-leak")
    config.runtime.workspace_root = str(tmp_path / "workspaces")
    config.runtime.input_root = str(tmp_path / "inputs")
    config.runtime.artifact_root = str(tmp_path / "artifacts")
    config.runtime.writeup_root = str(tmp_path / "writeups")
    storage = InMemoryStorage()
    containers = FakeContainerRuntime()
    app = create_app(
        config,
        storage,
        containers,
        host_model=DeterministicHostModel(),
    )

    with TestClient(app) as client:
        assert client.get("/api/v3/health").json() == {"status": "ok", "service": "tga3"}
        assert client.get("/health").status_code == 404
        assert client.get("/api/v3/tasks").json() == []
        catalog = client.get("/api/v3/models").json()
        assert catalog["providers"]
        assert "api_keys" not in catalog["providers"][0]
        assert "must-not-leak" not in str(catalog)
        assert "system_prompt" not in str(catalog)
        editable = client.get("/api/v3/config").json()
        assert editable["models"]["providers"][0]["api_keys"][0]["api_key"] == "must-not-leak"
        assert editable["agents"]["agents"]["supervisor"]["system_prompt"]
        invalid = deepcopy(editable)
        invalid["agents"]["agents"]["worker-openai"]["model_id"] = "missing-model"
        original_agents = (config_dir / "agents.json").read_text(encoding="utf-8")
        rejected = client.put("/api/v3/config", json=invalid)
        assert rejected.status_code == 422
        assert (config_dir / "agents.json").read_text(encoding="utf-8") == original_agents
        editable["scenes"]["scenes"][0]["description"] = "前端已更新场景说明"
        saved = client.put("/api/v3/config", json=editable)
        assert saved.status_code == 200
        assert config.scenes.scenes[0].description == "前端已更新场景说明"
        assert "前端已更新场景说明" in (config_dir / "scenes.json").read_text(encoding="utf-8")
        assert len(client.get("/api/v3/scenes").json()) == 8
        assert client.get("/api/v3/skills").status_code == 200
        skill = client.put("/api/v3/skills/web", json={"content": "# Web\n\n检查输入边界。"})
        assert skill.status_code == 200
        assert client.get("/api/v3/skills/web").json()["content"].startswith("# Web")
        assert client.delete("/api/v3/skills/web").status_code == 204
        created = client.post(
            "/api/v3/tasks",
            data={"title": "scene task", "prompt": "analyze this", "scene_id": "reverse_engineering"},
            files=[("files", ("challenge.bin", b"binary", "application/octet-stream"))],
        )
        assert created.status_code == 200
        task_id = created.json()["id"]
        assert created.json()["scene_id"] == "reverse_engineering"
        detail = client.get(f"/api/v3/tasks/{task_id}").json()
        assert len(detail["agents"]) == 4
        assert {agent["runtime_location"] for agent in detail["agents"]} == {"host", "container"}
        board = client.get(f"/api/v3/tasks/{task_id}/blackboard").json()["entries"]
        assert [entry["topic"] for entry in board] == ["scene", "task", "input"]
        assert len(containers.launched) == 2
