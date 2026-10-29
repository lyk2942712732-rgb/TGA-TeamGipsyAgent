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


def test_workers_get_private_workspaces_and_evidence_reporter_gets_read_only_union(tmp_path):
    config = TGA3Config(Path(__file__).parents[1] / "config")
    config.runtime.workspace_root = str(tmp_path / "workspaces")
    config.runtime.input_root = str(tmp_path / "inputs")
    config.runtime.artifact_root = str(tmp_path / "artifacts")
    for provider in config.models.providers:
        provider.api_keys[0].api_key = SecretStr("test-key")
    containers = FakeContainers()
    runtime = DockerContainerRuntime.__new__(DockerContainerRuntime)
    runtime.config, runtime.client = config, SimpleNamespace(containers=containers)
    task_id = uuid4()
    paths = {}
    for agent_id in ("worker-openai", "worker-claude", "reporter"):
        runtime._launch_sync(LaunchSpec(task_id=task_id, agent=config.resolve_agent(agent_id)))
        args = containers.kwargs
        mounts = {value["bind"]: (Path(key), value["mode"]) for key, value in args["volumes"].items()}
        paths[agent_id] = mounts
        assert args["working_dir"] == "/workspace"
        assert mounts["/inputs"][1] == "ro"
    first, second, reporter = (paths[key] for key in ("worker-openai", "worker-claude", "reporter"))
    assert len({paths[key]["/workspace"][0] for key in paths}) == 3
    assert first["/inputs"] == second["/inputs"] == reporter["/inputs"]
    assert first["/artifacts"][0] != second["/artifacts"][0]
    for key, content in (("worker-openai", "first"), ("worker-claude", "second")):
        (paths[key]["/artifacts"][0] / "proof.txt").write_text(content)
        assert (reporter["/artifacts"][0] / key / "proof.txt").read_text() == content
    assert reporter["/artifacts"][1] == "ro"


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


def test_reporter_uses_codex_image_and_read_only_evidence(tmp_path):
    config = TGA3Config(Path(__file__).parents[1] / "config")
    config.models.provider("openai").api_keys[0].api_key = SecretStr("test-key")
    containers = FakeContainers()
    runtime = DockerContainerRuntime.__new__(DockerContainerRuntime)
    runtime.config, runtime.client = config, SimpleNamespace(containers=containers)
    for name in ("workspace", "inputs", "artifacts"):
        (tmp_path / name).mkdir()
    runtime._task_paths = lambda *_: tuple(tmp_path / name for name in ("workspace", "inputs", "artifacts"))
    spec = LaunchSpec(task_id=uuid4(), agent=config.resolve_agent("reporter"), report_input="[]")
    runtime._launch_sync(spec)
    args = containers.kwargs
    assert args["image"] == config.runtime.worker_images["worker-openai"]
    assert args["environment"]["TGA3_AGENT_ROLE"] == "reporter"
    assert args["volumes"][str(tmp_path / "artifacts")]["mode"] == "ro"
    assert args["volumes"][str(tmp_path / "workspace/report-input.json")]["mode"] == "ro"
    assert args["cap_add"] == []
