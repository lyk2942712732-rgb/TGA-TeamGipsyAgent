import asyncio
from pathlib import Path

import pytest
from pydantic import SecretStr, TypeAdapter, ValidationError

from tga3.blackboard import Blackboard
from tga3.config import TGA3Config
from tga3.coordinator import TaskCoordinator
from tga3.docker_runtime import FakeContainerRuntime
from tga3.domain import Actor, AgentPublishRequest, PublishRequest, RunState, ScoutIntelPublication
from tga3.errors import ContractError
from tga3.scout_intel import ScoutIntelCollector
from tga3.storage import InMemoryStorage


class WaitingScout(FakeContainerRuntime):
    def __init__(self, storage):
        super().__init__()
        self.storage = storage
        self.started = asyncio.Event()
        self.finish = asyncio.Event()

    async def scout(self, spec, emit):
        await self.launch(spec)
        await ScoutIntelCollector(Blackboard(self.storage)).collect(ScoutIntelPublication(
            task_id=spec.task_id, actor={"agent_id": "scout", "display_name": "Scout", "role": "scout"},
            kind="intel", body={"claim": "ELF found"}, idempotency_key="scout-intel",
        ))
        self.started.set()
        try:
            await self.finish.wait()
        finally:
            await self.stop(spec.task_id, "scout")


def setup():
    config = TGA3Config(Path(__file__).parents[1] / "config")
    for provider in config.models.providers:
        provider.api_keys[0].api_key = SecretStr("test-key")
    storage = InMemoryStorage()
    runtime = WaitingScout(storage)
    return config, storage, runtime, TaskCoordinator(config, storage, runtime)


async def test_scout_finishes_and_stops_before_either_worker_starts():
    _, storage, runtime, coordinator = setup()
    task = await coordinator.create_and_start("serial", "inspect", "pwn")
    await runtime.started.wait()
    assert task.state == RunState.SCOUTING
    assert [spec.agent.agent_id for spec in runtime.launched] == ["scout"]
    assert not any(entry.kind == "intel" for entry in await storage.list_entries(task.id))
    assert (await storage.get_agent(task.id, "scout")).metadata["intel_draft_count"] == 1
    with pytest.raises(ContractError, match="wait"):
        await coordinator.blackboard.publish(
            task.id, Actor(agent_id="worker-openai", display_name="Worker", role="worker"),
            PublishRequest(kind="final_candidate", body={"conclusion": "x", "rationale": "x",
                           "finding_ids": [task.id]}, idempotency_key="early"),
        )
    runtime.finish.set()
    await coordinator._startup_jobs[task.id]
    assert (task.id, "scout") in runtime.stopped
    assert [spec.agent.agent_id for spec in runtime.launched] == ["scout", "worker-openai", "worker-claude"]
    assert (await storage.get_task(task.id)).state == RunState.RUNNING
    scout = await storage.get_agent(task.id, "scout")
    assert scout.metadata["intel_count"] == 1
    with pytest.raises(ContractError, match="during scouting"):
        await coordinator.blackboard.publish(
            task.id, Actor(agent_id="scout", display_name="Scout", role="scout"),
            PublishRequest(kind="intel", body={"claim": "late"}, idempotency_key="late"),
        )


async def test_scout_timeout_keeps_partial_intel_and_launches_workers():
    config, storage, runtime, coordinator = setup()
    config.runtime.scout_timeout_seconds = 0.02
    task = await coordinator.create_and_start("timeout", "inspect", "pwn")
    await coordinator._startup_jobs[task.id]
    assert (await storage.get_agent(task.id, "scout")).metadata["finish_reason"] == "timeout"
    assert (await storage.get_task(task.id)).state == RunState.RUNNING
    assert any(entry.kind == "intel" for entry in await storage.list_entries(task.id))
    assert (task.id, "scout") in runtime.stopped


async def test_cancel_during_scout_never_starts_workers():
    _, storage, runtime, coordinator = setup()
    task = await coordinator.create_and_start("cancel", "inspect", "pwn")
    await runtime.started.wait()
    await coordinator.stop_task(task.id)
    assert [spec.agent.agent_id for spec in runtime.launched] == ["scout"]
    assert (await storage.get_task(task.id)).state == RunState.CANCELLED
    assert (task.id, "scout") in runtime.stopped
    assert any(entry.kind == "intel" for entry in await storage.list_entries(task.id))


async def test_scout_crash_flushes_collected_intel_before_workers_launch():
    _, storage, runtime, coordinator = setup()
    original = runtime.scout

    async def crash(spec, emit):
        runtime.finish.set()
        await original(spec, emit)
        raise RuntimeError("simulated scout failure")

    runtime.scout = crash
    task = await coordinator.create_and_start("crash", "inspect", "pwn")
    await coordinator._startup_jobs[task.id]
    assert (await storage.get_agent(task.id, "scout")).metadata["finish_reason"] == "error"
    assert (await storage.get_task(task.id)).state == RunState.RUNNING
    assert any(entry.kind == "intel" for entry in await storage.list_entries(task.id))


async def test_collector_recreation_replays_drafts_without_duplicate_blackboard_entries():
    _, storage, runtime, coordinator = setup()
    task = await coordinator.create_and_start("replay", "inspect", "pwn")
    await runtime.started.wait()
    await ScoutIntelCollector(coordinator.blackboard).flush(task.id)
    await ScoutIntelCollector(coordinator.blackboard).flush(task.id)
    assert sum(entry.kind == "intel" for entry in await storage.list_entries(task.id)) == 1
    with pytest.raises(ContractError, match="closed"):
        await coordinator.scout_intel.collect(ScoutIntelPublication(
            task_id=task.id, actor={"agent_id": "scout", "display_name": "Scout", "role": "scout"},
            kind="intel", body={"claim": "too late"}, idempotency_key="late",
        ))
    runtime.finish.set()
    await coordinator._startup_jobs[task.id]


def test_worker_cannot_publish_intel_and_scout_cannot_publish_findings():
    adapter = TypeAdapter(AgentPublishRequest)
    with pytest.raises(ValidationError):
        adapter.validate_python({"kind": "intel", "actor": {"agent_id": "worker-openai", "role": "worker"}})
