import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import SecretStr

from tga3.config import TGA3Config
from tga3.docker_runtime import DockerContainerRuntime, LaunchSpec
from tga3.errors import RuntimeUnavailableError


class FakeContainers:
    def __init__(self):
        self.kwargs = None

    def run(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(id="container-id")


def test_scout_mounts_intels_once_for_both_phases(tmp_path):
    config = TGA3Config(Path(__file__).parents[1] / "config")
    for provider in config.models.providers:
        provider.api_keys[0].api_key = SecretStr("test-key")
    config.runtime.task_root = str(tmp_path / "tasks")
    containers = FakeContainers()
    runtime = DockerContainerRuntime.__new__(DockerContainerRuntime)
    runtime.config, runtime.client = config, SimpleNamespace(containers=containers)
    runtime._launch_sync(LaunchSpec(task_id=uuid4(), agent=config.resolve_agent("scout"), scout_phase="executing"))
    mounts = {value["bind"]: value["mode"] for value in containers.kwargs["volumes"].values()}
    assert mounts["/artifacts"] == "rw" and mounts["/intels"] == "ro"
    environment = containers.kwargs["environment"]
    assert environment["TGA3_SCOUT_EXTRACT_TIMEOUT_SECONDS"] == str(config.runtime.scout_extract_timeout_seconds)
    assert environment["TGA3_SCOUT_PHASE"] == "executing"


def test_workers_get_private_workspaces_and_evidence_reporter_gets_read_only_union(tmp_path):
    config = TGA3Config(Path(__file__).parents[1] / "config")
    config.runtime.task_root = str(tmp_path / "tasks")
    for provider in config.models.providers:
        provider.api_keys[0].api_key = SecretStr("test-key")
    containers = FakeContainers()
    runtime = DockerContainerRuntime.__new__(DockerContainerRuntime)
    runtime.config, runtime.client = config, SimpleNamespace(containers=containers)
    task_id = uuid4()
    paths = {}
    for agent_id in ("scout", "worker-openai", "worker-claude", "reporter"):
        runtime._launch_sync(LaunchSpec(task_id=task_id, agent=config.resolve_agent(agent_id)))
        args = containers.kwargs
        mounts = {value["bind"]: (Path(key), value["mode"]) for key, value in args["volumes"].items()}
        paths[agent_id] = mounts
        assert args["working_dir"] == "/workspace"
        assert mounts["/inputs"][1] == "ro"
    first, second, reporter = (paths[key] for key in ("worker-openai", "worker-claude", "reporter"))
    assert len({paths[key]["/workspace"][0] for key in paths}) == 4
    assert first["/inputs"] == second["/inputs"] == reporter["/inputs"]
    assert all("/recon" not in mounts for mounts in paths.values())
    assert first["/artifacts"][0] != second["/artifacts"][0]
    for key, content in (("worker-openai", "first"), ("worker-claude", "second")):
        (paths[key]["/artifacts"][0] / "proof.txt").write_text(content)
        assert (reporter["/evidence"][0] / key / "proof.txt").read_text() == content
    assert reporter["/evidence"][1] == "ro"
    assert reporter["/artifacts"][1] == "rw"


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
    assert args["volumes"][str(tmp_path / "artifacts")]["mode"] == "rw"
    assert args["volumes"][str(tmp_path / "workspace/report-input.json")]["mode"] == "ro"
    assert args["cap_add"] == []


@pytest.mark.parametrize("execution_reason", ["completed", "timeout", "error"])
async def test_scout_reuses_container_after_execution_and_cleans_up_after_extraction(tmp_path, execution_reason):
    config = TGA3Config(Path(__file__).parents[1] / "config")
    config.runtime.task_root = str(tmp_path / "tasks")
    for provider in config.models.providers:
        provider.api_keys[0].api_key = SecretStr("test-key")
    task_id = uuid4()
    state = config.task_path(task_id, "workspaces", "scout") / ".tga3"
    state.mkdir(parents=True)
    events, launches, stops = [], [], []

    class Container:
        status = "running"

        def reload(self):
            phase = "extracting" if (state / "scout-extract-request").exists() else "executing"
            reason = execution_reason if phase == "executing" else "completed"
            (state / f"scout-{phase}-result.json").write_text(json.dumps({
                "finish_reason": reason, "error": "fixture failure" if reason == "error" else None,
            }))

        def logs(self):
            return b'{"method":"agent.session","params":{"cli_session_id":"same-session"}}\n'

    container = Container()
    runtime = DockerContainerRuntime.__new__(DockerContainerRuntime)
    runtime.config = config
    runtime.client = SimpleNamespace(containers=SimpleNamespace(get=lambda _: container))

    async def launch(spec):
        launches.append(spec)
        return "same-container"

    async def stop(task, agent):
        stops.append((task, agent))

    async def emit(method, params):
        events.append((method, params))

    runtime.launch, runtime.stop = launch, stop
    first = LaunchSpec(task_id=task_id, agent=config.resolve_agent("scout"), scout_phase="executing")
    if execution_reason == "completed":
        await runtime.scout(first, emit)
    else:
        with pytest.raises(TimeoutError if execution_reason == "timeout" else RuntimeUnavailableError):
            await runtime.scout(first, emit)
    assert not stops
    await runtime.scout(LaunchSpec(task_id=task_id, agent=first.agent, scout_phase="extracting"), emit)
    assert len(launches) == 1
    assert stops == [(task_id, "scout")]
    assert [method for method, _ in events] == ["container.started", "agent.session"]
    assert task_id not in runtime._scouts
