from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from pydantic import SecretStr

from tga3.config import TGA3Config
from tga3.docker_runtime import DockerContainerRuntime, LaunchSpec


class FakeContainers:
    def __init__(self):
        self.kwargs = None

    def run(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(id="container-id")


def test_worker_container_uses_stable_non_root_identity(tmp_path: Path):
    config = TGA3Config(Path(__file__).parents[1] / "config")
    for provider in config.models.providers:
        provider.api_keys[0].api_key = SecretStr("test-key")
    worker = config.resolve_agent("worker-openai")
    containers = FakeContainers()
    runtime = DockerContainerRuntime.__new__(DockerContainerRuntime)
    runtime.config = config
    runtime.client = SimpleNamespace(containers=containers)
    workspace = tmp_path / "workspace"
    inputs = tmp_path / "inputs"
    artifacts = tmp_path / "artifacts"
    for path in (workspace, inputs, artifacts):
        path.mkdir()
    runtime._task_paths = lambda _task_id, _agent_id: (workspace, inputs, artifacts)

    assert runtime._launch_sync(LaunchSpec(task_id=uuid4(), agent=worker)) == "container-id"
    assert containers.kwargs["user"] == "1000:1000"
    assert containers.kwargs["environment"]["HOME"] == "/home/tga3"
    assert containers.kwargs["environment"]["TGA3_BLACKBOARD_MCP_URL"].endswith("/mcp/")
    assert containers.kwargs["volumes"][str(workspace)] == {"bind": "/workspace", "mode": "rw"}
    assert containers.kwargs["volumes"][str(artifacts)] == {"bind": "/artifacts", "mode": "rw"}
