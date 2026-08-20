from pathlib import Path

from fastapi.testclient import TestClient
from pydantic import SecretStr

from tga3.app import create_app
from tga3.config import TGA3Config
from tga3.docker_runtime import FakeContainerRuntime
from tga3.host_agents import DeterministicHostModel
from tga3.storage import InMemoryStorage


def test_frontend_api_is_versioned_and_never_exposes_provider_keys(tmp_path: Path):
    config = TGA3Config(Path(__file__).parents[1] / "config")
    for provider in config.models.providers:
        provider.api_keys[0].api_key = SecretStr("must-not-leak")
    config.runtime.workspace_root = str(tmp_path / "workspaces")
    config.runtime.input_root = str(tmp_path / "inputs")
    config.runtime.artifact_root = str(tmp_path / "artifacts")
    config.runtime.writeup_root = str(tmp_path / "writeups")
    app = create_app(
        config,
        InMemoryStorage(),
        FakeContainerRuntime(),
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
        assert client.get("/api/v3/skills").status_code == 200
