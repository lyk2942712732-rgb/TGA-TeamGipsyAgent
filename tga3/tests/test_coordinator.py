from pathlib import Path

import pytest
from pydantic import SecretStr

from tga3.config import TGA3Config
from tga3.coordinator import TaskCoordinator
from tga3.docker_runtime import FakeContainerRuntime
from tga3.domain import EntryKind, RunState
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

    task = await coordinator.create_and_start("dual", "solve this")
    assert task.state == RunState.RUNNING
    assert [spec.agent.agent_id for spec in containers.launched] == [
        "worker-openai",
        "worker-claude",
    ]
    agents = await storage.list_agents(task.id)
    assert {agent.sdk for agent in agents} == {"openai_agents", "claude_agent"}
    assert (await storage.list_entries(task.id))[0].kind == EntryKind.USER_PROMPT


@pytest.mark.asyncio
async def test_worker_question_is_runtime_event_not_blackboard_type():
    storage = InMemoryStorage()
    coordinator = TaskCoordinator(configured(), storage, FakeContainerRuntime())
    task = await coordinator.create_and_start("question", "solve this")
    before = len(await storage.list_entries(task.id))

    await coordinator.handle_agent_event(
        task.id,
        "worker-claude",
        "agent.needs_user_input",
        {"question": "Please provide the challenge URL"},
    )
    assert len(await storage.list_entries(task.id)) == before
    messages = await storage.list_dialogue(task.id)
    assert any("worker-claude" in message.text for message in messages)
    assert (await storage.get_task(task.id)).state == RunState.WAITING_USER
