"""Isolated real control API with local model-list endpoints for browser tests."""

import os
import sys
from pathlib import Path
from shutil import copytree
from tempfile import TemporaryDirectory

import uvicorn
from fastapi import HTTPException

root = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(root / "tga3" / "src"))

from tga3.app import create_app  # noqa: E402
from tga3.config import TGA3Config  # noqa: E402
from tga3.docker_runtime import FakeContainerRuntime  # noqa: E402
from tga3.host_agents import DeterministicHostModel  # noqa: E402
from tga3.storage import InMemoryStorage  # noqa: E402

with TemporaryDirectory(prefix="tga3-provider-e2e-") as directory:
    fixture_root = Path(directory)
    copytree(root / "tga3" / "config", fixture_root / "config")
    config = TGA3Config(fixture_root / "config")
    for name in ("workspace_root", "input_root", "artifact_root", "writeup_root", "skills_root"):
        setattr(config.runtime, name, str(fixture_root / name))
    app = create_app(config, InMemoryStorage(), FakeContainerRuntime(), host_model=DeterministicHostModel())

    @app.get("/mock/responses/v1/models")
    async def responses_models():
        return {"data": [{"id": "qwen-shared"}, {"id": "responses-only"},
                         {"id": "disabled-model"}, {"id": "anthropic-only"}]}

    @app.get("/mock/anthropic/v1/models")
    async def anthropic_models():
        raise HTTPException(status_code=404, detail="This gateway only exposes Messages on the Anthropic endpoint")

    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("TGA_PLAYWRIGHT_API_PORT", "18083")))
