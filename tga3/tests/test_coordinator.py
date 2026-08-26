from pathlib import Path

import pytest
from pydantic import SecretStr

from tga3.config import TGA3Config
from tga3.coordinator import TaskCoordinator
from tga3.docker_runtime import FakeContainerRuntime
from tga3.domain import AgentState, EntryKind, RunState
from tga3.storage import InMemoryStorage


def configured() -> TGA3Config:
    config = TGA3Config(Path(__file__).parents[1] / "config")
    for provider in config.models.providers:
        provider.api_keys[0].api_key = SecretStr("test-key")
    return config


@pytest.mark.asyncio
async def test_task_start_launches_two_independent_worker_containers():
    storage = InMemoryStorage()
    containers = FakeContainerRuntime()
    coordinator = TaskCoordinator(configured(), storage, containers)

    task = await coordinator.create_and_start("dual", "solve this", "penetration_test")
    assert task.state == RunState.RUNNING
    assert [spec.agent.agent_id for spec in containers.launched] == [
        "worker-openai",
        "worker-claude",
    ]
    agents = await storage.list_agents(task.id)
    assert {agent.agent_id for agent in agents} == {
        "supervisor",
        "worker-openai",
        "worker-claude",
        "reporter",
    }
    entries = await storage.list_entries(task.id)
    assert entries[0].kind == EntryKind.USER_PROMPT
    assert entries[0].topic == "scene"
    assert "拿到" in str(entries[0].body) or "flag" in str(entries[0].body)
    assert entries[1].body["text"] == "solve this"


@pytest.mark.asyncio
async def test_worker_input_request_is_runtime_event_not_blackboard_type():
    storage = InMemoryStorage()
    coordinator = TaskCoordinator(configured(), storage, FakeContainerRuntime())
    task = await coordinator.create_and_start("question", "solve this", "penetration_test")
    before = len(await storage.list_entries(task.id))

    await coordinator.handle_agent_event(
        task.id,
        "worker-claude",
        "agent.needs_user_input",
        {"question": "Please provide the challenge URL"},
    )
    assert len(await storage.list_entries(task.id)) == before
    messages = await storage.list_dialogue(task.id)
    assert any(message.payload.get("origin_agent_id") == "worker-claude" for message in messages)
    assert (await storage.get_task(task.id)).state == RunState.WAITING_USER


@pytest.mark.asyncio
async def test_finishing_workers_treats_control_disconnect_as_expected():
    storage = InMemoryStorage()
    containers = FakeContainerRuntime()
    coordinator = TaskCoordinator(configured(), storage, containers)
    task = await coordinator.create_and_start("finish", "solve this", "penetration_test")

    async def disconnect_on_stop(task_id, agent_id, method, _params=None):
        assert method == "session.stop"
        await coordinator.handle_agent_event(task_id, agent_id, "session.disconnected", {})

    coordinator.gateway.notify = disconnect_on_stop
    await coordinator.finish_task_workers(task.id)

    workers = [
        agent
        for agent in await storage.list_agents(task.id)
        if agent.agent_id in {"worker-openai", "worker-claude"}
    ]
    assert workers
    assert all(agent.desired_state == AgentState.STOPPED for agent in workers)
    assert all(agent.actual_state == AgentState.STOPPED for agent in workers)
    assert all(agent.last_error is None for agent in workers)
    dialogue = await storage.list_dialogue(task.id)
    assert not any(message.payload.get("state") == AgentState.FAILED for message in dialogue)


@pytest.mark.asyncio
async def test_delete_task_keeps_files_when_database_delete_fails(tmp_path):
    class FailingDeleteStorage(InMemoryStorage):
        async def delete_task(self, task_id):
            raise RuntimeError("database delete failed")

    config = configured()
    roots = [tmp_path / name for name in ("workspaces", "inputs", "artifacts", "writeups")]
    (
        config.runtime.workspace_root,
        config.runtime.input_root,
        config.runtime.artifact_root,
        config.runtime.writeup_root,
    ) = tuple(str(root) for root in roots)
    storage = FailingDeleteStorage()
    coordinator = TaskCoordinator(config, storage, FakeContainerRuntime())
    task = await storage.create_task("delete", "penetration_test")
    await storage.update_task(task.id, state=RunState.CANCELLED)
    task_paths = [root / str(task.id) for root in roots]
    for path in task_paths:
        path.mkdir(parents=True)
        (path / "keep.txt").write_text("recoverable", encoding="utf-8")

    with pytest.raises(RuntimeError, match="database delete failed"):
        await coordinator.delete_task(task.id)

    assert all((path / "keep.txt").is_file() for path in task_paths)
