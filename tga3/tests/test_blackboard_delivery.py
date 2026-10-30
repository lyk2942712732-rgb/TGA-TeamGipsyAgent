from pathlib import Path

from tga3.blackboard import Blackboard
from tga3.blackboard_delivery import BlackboardDelivery
from tga3.config import TGA3Config
from tga3.domain import SYSTEM_ACTOR, AgentRun, PublishRequest
from tga3.storage import InMemoryStorage


async def test_independent_cursors_interval_updates_final_and_replay(monkeypatch):
    config = TGA3Config(Path(__file__).parents[1] / "config")
    storage = InMemoryStorage()
    task = await storage.create_task("sync", "pwn")
    for agent in ("worker-openai", "worker-claude"):
        await storage.upsert_agent(AgentRun(
            task_id=task.id, agent_id=agent, sdk="codex_cli", provider_id="openai",
            model_id="test", protocol="openai_responses",
        ))
    clock = [100.0]
    monkeypatch.setattr("tga3.blackboard_delivery.time.time", lambda: clock[0])
    service = BlackboardDelivery(config, storage, Blackboard(storage))

    async def publish(text):
        await storage.publish(task.id, SYSTEM_ACTOR, PublishRequest(
            kind="user_prompt", body={"text": text}, idempotency_key=text,
        ))

    await publish("first")
    initial = await service.checkpoint(task.id, "worker-openai")
    assert initial["through_seq"] == 1
    assert "first" in initial["context"]
    assert await service.checkpoint(task.id, "worker-openai") == initial
    assert await service.checkpoint(task.id, "worker-openai", ack=initial["delivery_id"]) == {}
    await publish("second")
    clock[0] = 159
    assert await service.checkpoint(task.id, "worker-openai") == {}
    clock[0] = 160
    assert await service.checkpoint(task.id, "worker-openai", initial_only=True) == {}
    delta = await service.checkpoint(task.id, "worker-openai")
    assert "second" in delta["context"] and "first" not in delta["context"]
    assert (await service.checkpoint(task.id, "worker-claude"))["through_seq"] == 2
    await service.checkpoint(task.id, "worker-openai", ack=delta["delivery_id"])
    clock[0] = 161
    await publish("third")
    final = await service.checkpoint(task.id, "worker-openai", final=True)
    assert "third" in final["context"] and "second" not in final["context"]
    await service.checkpoint(task.id, "worker-openai", ack=final["delivery_id"])
    await publish("fourth")
    clock[0] = 220
    assert await service.checkpoint(task.id, "worker-openai") == {}
    config.agents.agents["worker-openai"].blackboard_interval_seconds = 10
    assert "fourth" in (await service.checkpoint(task.id, "worker-openai"))["context"]


async def test_unchanged_blackboard_does_not_fetch_content(monkeypatch):
    config = TGA3Config(Path(__file__).parents[1] / "config")
    storage = InMemoryStorage()
    task = await storage.create_task("empty", "pwn")
    await storage.upsert_agent(AgentRun(
        task_id=task.id, agent_id="worker-openai", sdk="codex_cli", provider_id="openai",
        model_id="x", protocol="openai_responses", metadata={"blackboard_cursor": 0, "blackboard_read_at": 0},
    ))

    async def forbidden(*args, **kwargs):
        raise AssertionError("must not read unchanged blackboard content")

    monkeypatch.setattr(storage, "list_entries", forbidden)
    assert await BlackboardDelivery(config, storage, Blackboard(storage)).checkpoint(
        task.id, "worker-openai", final=True,
    ) == {}
