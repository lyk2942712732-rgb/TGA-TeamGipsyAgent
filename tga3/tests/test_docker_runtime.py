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


async def test_aoe_collection_requests_reuse_container_and_ignore_stale_results(tmp_path):
    config = TGA3Config(Path(__file__).parents[1] / "config")
    config.runtime.task_root = str(tmp_path / "tasks")
    config.models.provider("openai").api_keys[0].api_key = SecretStr("fixture-key")
    parent, first, second = uuid4(), uuid4(), uuid4()
    overview = config.task_path(parent) / 'overview'
    overview.mkdir(parents=True)
    for name in ['scene.md', 'task.md']:
        (overview / name).write_text('Full original '+name)
    directory = config.task_path(parent, "workspaces", "scout") / ".tga3"
    emitted, launched, seen = [], [], []

    class Container:
        id, status = "scout-container", "running"
        output = b""

        def reload(self):
            request = json.loads((directory / "aoe-scout-request.json").read_text())
            if request["id"] in seen:
                return
            seen.append(request["id"])
            response = {
                **request,
                "finish_reason": "timeout"
                if request["question_id"] == str(first) and request["phase"] == "executing"
                else "completed",
            }
            results = directory / "aoe-scout-results"
            results.mkdir(exist_ok=True)
            (results / (request["id"] + ".json")).write_text(json.dumps(response))
            self.output += (
                json.dumps(
                    {
                        "method": "agent.session",
                        "params": {"cli_session_id": "same-native-session", "question_id": request["question_id"]},
                    }
                )
                + "\n"
            ).encode()

        def logs(self):
            return self.output

    container = Container()

    class Containers(FakeContainers):
        def list(self, **kwargs):
            return []

        def run(self, **kwargs):
            launched.append(kwargs)
            return container

        def get(self, ident):
            return container

    runtime = DockerContainerRuntime.__new__(DockerContainerRuntime)
    runtime.config, runtime.client = config, SimpleNamespace(containers=Containers())

    async def emit(method, params):
        emitted.append(params)

    for ident, phase in [(None, "collecting"), (None, "collecting")]:
        spec = LaunchSpec(
            task_id=parent,
            agent=config.resolve_agent("scout"),
            aoe_parent_id=parent,
            scout_question_id=ident,
            scout_phase=phase,
            phase_budget_seconds=300 if phase == "executing" else None,
        )
        if ident == first and phase == "executing":
            with pytest.raises(TimeoutError):
                await runtime.scout(spec, emit)
        else:
            await runtime.scout(spec, emit)
    assert len(launched) == 1 and len(set(seen)) == 2
    assert parent in runtime._scouts  # The controller closes it after collection completes.
    assert {e["cli_session_id"] for e in emitted if "cli_session_id" in e} == {"same-native-session"}
    assert {e["question_id"] for e in emitted if "cli_session_id" in e} == {None}
    with pytest.raises(RuntimeUnavailableError, match="only batch collection"):
        await runtime.scout(LaunchSpec(task_id=parent, agent=config.resolve_agent("scout"),
                                      aoe_parent_id=parent, scout_phase="executing"), emit)


def test_scout_mounts_intels_once_for_three_phases(tmp_path):
    config = TGA3Config(Path(__file__).parents[1] / "config")
    for provider in config.models.providers:
        provider.api_keys[0].api_key = SecretStr("test-key")
    config.runtime.task_root = str(tmp_path / "tasks")
    containers = FakeContainers()
    runtime = DockerContainerRuntime.__new__(DockerContainerRuntime)
    runtime.config, runtime.client = config, SimpleNamespace(containers=containers)
    runtime._launch_sync(LaunchSpec(task_id=uuid4(), agent=config.resolve_agent("scout"), scout_phase="executing"))
    mounts = {value["bind"]: value["mode"] for value in containers.kwargs["volumes"].values()}
    assert mounts["/logs"] == "rw" and mounts["/intels"] == "ro"
    environment = containers.kwargs["environment"]
    assert "TGA3_SCOUT_EXTRACT_TIMEOUT_SECONDS" not in environment
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
    assert first["/logs"][0] != second["/logs"][0]
    for key, content in (("worker-openai", "first"), ("worker-claude", "second")):
        (paths[key]["/logs"][0] / "proof.txt").write_text(content)
        assert (reporter["/evidence"][0] / key / "proof.txt").read_text() == content
    assert reporter["/evidence"][1] == "ro"
    assert reporter["/logs"][1] == "rw"


def test_joint_handoff_mounts_only_recorded_evidence_from_this_question_read_only(tmp_path):
    from tga3.workers.action_journal import ActionJournal

    config = TGA3Config(Path(__file__).parents[1] / 'config')
    config.runtime.task_root = str(tmp_path / 'tasks')
    for provider in config.models.providers:
        provider.api_keys[0].api_key = SecretStr('fixture-key')
    parent, question, unrelated = uuid4(), uuid4(), uuid4()
    config.register_question_path(parent, question)
    config.register_question_path(parent, unrelated)
    overview = config.task_path(parent) / 'overview'; overview.mkdir(parents=True)
    for name in ['scene.md','task.md']:
        (overview / name).write_text('original')
    source = config.task_path(question, 'workspaces', 'worker-openai'); source.mkdir(parents=True)
    (source / '.codex').mkdir(); (source / '.codex/private-session.json').write_text('private state')
    journal = ActionJournal(config.task_path(question,'logs','worker-openai'),
                            identity={'agent_id':'worker-openai'},output_directory=source)
    journal.record('call','Bash',inputs={'command':'python solve.py'},before_execution=True)
    (source / 'solve.py').write_text('print("reusable recorded method")')
    journal.record('call','Bash',result='observed',status='completed')
    containers = FakeContainers()
    runtime = DockerContainerRuntime.__new__(DockerContainerRuntime)
    runtime.config, runtime.client = config, SimpleNamespace(containers=containers)
    runtime._launch_sync(LaunchSpec(task_id=question,aoe_parent_id=parent,agent=config.resolve_agent('worker-qwen'),
                                   evidence_agents=('worker-openai',)))
    mounts = containers.kwargs['volumes']
    assert mounts[str(source / 'solve.py')] == {'bind':'/evidence-files/worker-openai/solve.py','mode':'ro'}
    assert mounts[str(source / 'result')] == {'bind':'/evidence-files/worker-openai/result','mode':'ro'}
    assert str(source) not in mounts and str(source / '.codex') not in mounts
    assert all(not Path(path).is_relative_to(config.task_path(unrelated)) for path in mounts)
    assert json.loads(containers.kwargs['environment']['TGA3_PEER_EVIDENCE']) == ['worker-openai']


def test_batch_uploads_are_scout_only_and_question_inputs_are_separate(tmp_path):
    config = TGA3Config(Path(__file__).parents[1]/'config')
    config.runtime.task_root = str(tmp_path/'tasks')
    for provider in config.models.providers:
        provider.api_keys[0].api_key = SecretStr('fixture-key')
    parent, question = uuid4(), uuid4()
    config.register_question_path(parent, question)
    config.task_path(question).mkdir(parents=True)
    (config.task_path(question) / 'source.json').write_text('{"original":true}')
    overview = config.task_path(parent) / 'overview'
    overview.mkdir(parents=True)
    for name in ['scene.md', 'task.md']:
        (overview / name).write_text('Full original '+name)
    containers = FakeContainers()
    runtime = DockerContainerRuntime.__new__(DockerContainerRuntime)
    runtime.config, runtime.client = config, SimpleNamespace(containers=containers)
    runtime._launch_sync(LaunchSpec(task_id=parent, aoe_parent_id=parent, agent=config.resolve_agent('scout')))
    scout = containers.kwargs['volumes']
    assert scout[str(config.task_path(parent))] == {'bind':'/aoe','mode':'ro'}
    runtime._launch_sync(LaunchSpec(task_id=question, aoe_parent_id=parent, agent=config.resolve_agent('worker-openai')))
    mounts = containers.kwargs['volumes']
    assert mounts[str(config.task_path(question,'inputs'))] == {'bind':'/inputs','mode':'ro'}
    assert str(config.task_path(parent,'inputs')) not in mounts and str(config.task_path(parent)) not in mounts
    assert containers.kwargs['environment']['TGA3_WEB_SEARCH_MCP_URL'].endswith('/web-mcp/')
    assert mounts[str(overview)] == {'bind': '/task-overview', 'mode': 'ro'}
    assert '/task-overview/scene.md' in containers.kwargs['environment']['TGA3_STARTUP_PROMPT']
    assert mounts[str(config.task_path(question) / 'source.json')] == {'bind': '/question-source.json', 'mode': 'ro'}
    with pytest.raises(RuntimeUnavailableError, match='Batch uploads'):
        runtime._launch_sync(LaunchSpec(task_id=parent, aoe_parent_id=parent, agent=config.resolve_agent('worker-claude')))


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
    artifacts = tmp_path / "logs"
    for path in (workspace, inputs, artifacts):
        path.mkdir()
    runtime._task_paths = lambda _task_id, _agent_id: (workspace, inputs, artifacts)

    assert runtime._launch_sync(LaunchSpec(task_id=uuid4(), agent=worker)) == "container-id"
    assert containers.kwargs["user"] == "1000:1000"
    assert containers.kwargs["environment"]["HOME"] == "/home/tga3"
    assert containers.kwargs["environment"]["TGA3_BLACKBOARD_MCP_URL"].endswith("/mcp/")
    assert containers.kwargs["volumes"][str(workspace)] == {"bind": "/workspace", "mode": "rw"}
    assert containers.kwargs["volumes"][str(artifacts)] == {"bind": "/logs", "mode": "rw"}


def test_reporter_uses_codex_image_and_read_only_evidence(tmp_path):
    config = TGA3Config(Path(__file__).parents[1] / "config")
    config.models.provider("openai").api_keys[0].api_key = SecretStr("test-key")
    containers = FakeContainers()
    runtime = DockerContainerRuntime.__new__(DockerContainerRuntime)
    runtime.config, runtime.client = config, SimpleNamespace(containers=containers)
    for name in ("workspace", "inputs", "logs"):
        (tmp_path / name).mkdir()
    runtime._task_paths = lambda *_: tuple(tmp_path / name for name in ("workspace", "inputs", "logs"))
    spec = LaunchSpec(task_id=uuid4(), agent=config.resolve_agent("reporter"), report_input="[]")
    runtime._launch_sync(spec)
    args = containers.kwargs
    assert args["image"] == config.runtime.worker_images["worker-openai"]
    assert args["environment"]["TGA3_AGENT_ROLE"] == "reporter"
    assert args["volumes"][str(tmp_path / "logs")]["mode"] == "rw"
    assert args["volumes"][str(tmp_path / "workspace/report-input.json")]["mode"] == "ro"
    assert args["cap_add"] == []


def test_question_reporter_environment_retains_provenance_in_action_manifest(tmp_path, monkeypatch):
    from tga3.action_logs import read_action_log
    from tga3.workers.cli_adapter import CodexAdapter

    config = TGA3Config(Path(__file__).parents[1] / "config")
    config.runtime.task_root = str(tmp_path / "tasks")
    config.models.provider("openai").api_keys[0].api_key = SecretStr("fixture-key")
    parent_id, question_id = uuid4(), uuid4()
    config.register_question_path(parent_id, question_id)
    containers = FakeContainers()
    runtime = DockerContainerRuntime.__new__(DockerContainerRuntime)
    runtime.config, runtime.client = config, SimpleNamespace(containers=containers)
    runtime._launch_sync(LaunchSpec(task_id=question_id, agent=config.resolve_agent("reporter")))
    for key, value in containers.kwargs["environment"].items():
        monkeypatch.setenv(key, value)
    workspace = config.task_path(question_id, "workspaces", "reporter")
    logs = config.task_path(question_id, "logs", "reporter")
    monkeypatch.setenv("TGA3_WORKSPACE", str(workspace))
    monkeypatch.setenv("TGA3_LOGS", str(logs))
    adapter = CodexAdapter()
    adapter.journal.record("report-read", "shell", result="literal question evidence", status="completed")
    manifest = read_action_log(logs)
    assert manifest["question_id"] == str(question_id)
    assert manifest["task_id"] == str(question_id)
    assert (workspace / manifest["actions"][0]["artifact"]["output_path"]).read_text() == "literal question evidence"


@pytest.mark.parametrize("execution_reason", ["completed", "timeout", "error"])
async def test_scout_reuses_container_through_extraction_and_cleans_up_after_merging(tmp_path, execution_reason):
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
            phase = (
                "merging"
                if (state / "scout-merge-request").exists()
                else "extracting"
                if (state / "scout-extract-request").exists()
                else "executing"
            )
            reason = execution_reason if phase == "executing" else "completed"
            (state / f"scout-{phase}-result.json").write_text(
                json.dumps(
                    {
                        "finish_reason": reason,
                        "error": "fixture failure" if reason == "error" else None,
                    }
                )
            )

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
    assert not stops
    await runtime.scout(LaunchSpec(task_id=task_id, agent=first.agent, scout_phase="merging"), emit)
    assert len(launches) == 1
    assert stops == [(task_id, "scout")]
    assert [method for method, _ in events] == ["container.started", "agent.session"]
    assert task_id not in runtime._scouts


def test_reporter_mounts_only_result_bodies_and_manifest_attachments(tmp_path):
    from tga3.workers.action_journal import ActionJournal

    config = TGA3Config(Path(__file__).parents[1] / "config")
    config.runtime.task_root = str(tmp_path / "tasks")
    for provider in config.models.providers:
        provider.api_keys[0].api_key = SecretStr("test-key")
    task_id = uuid4()
    root = config.task_path(task_id, "workspaces", "worker-openai")
    root.mkdir(parents=True)
    journal = ActionJournal(
        config.task_path(task_id, "logs", "worker-openai"),
        identity={"task_id": str(task_id), "agent_id": "worker-openai"},
        output_directory=root,
    )
    journal.record("download", "shell")
    (root / "download.bin").write_bytes(b"fixture")
    (root / ".codex").mkdir()
    (root / ".codex/config.toml").write_text("private CLI settings")
    journal.record("download", "shell", result={"aggregated_output": "downloaded"}, status="completed")
    containers = FakeContainers()
    runtime = DockerContainerRuntime.__new__(DockerContainerRuntime)
    runtime.config, runtime.client = config, SimpleNamespace(containers=containers)
    runtime._launch_sync(LaunchSpec(task_id=task_id, agent=config.resolve_agent("reporter")))
    mounts = {value["bind"]: value["mode"] for value in containers.kwargs["volumes"].values()}
    assert mounts["/evidence-files/worker-openai/result"] == "ro"
    assert mounts["/evidence-files/worker-openai/download.bin"] == "ro"
    assert not any(".codex" in path for path in mounts)
