import asyncio
import json
from pathlib import Path

import httpx
import pytest

from tga3.app import create_app
from tga3.blackboard import Blackboard
from tga3.blackboard_delivery import BlackboardDelivery
from tga3.config import TGA3Config
from tga3.docker_runtime import FakeContainerRuntime
from tga3.domain import USER_ACTOR, AgentRun, PublishRequest, RunState, ScoutIntelPublication
from tga3.errors import ConflictError, FindingRejectedError
from tga3.scout_intel import ScoutIntelCollector
from tga3.storage import InMemoryStorage
from tga3.token_usage import TokenUsageTracker


async def setup(tmp_path):
    config = TGA3Config(Path(__file__).parents[1] / "config")
    config.runtime.workspace_root = str(tmp_path / "workspaces")
    storage = InMemoryStorage()
    task = await storage.create_task("collaboration", "pwn")
    await storage.update_task(task.id, state=RunState.SCOUTING)
    for agent_id, agent in config.agents.agents.items():
        await storage.upsert_agent(AgentRun(
            task_id=task.id, agent_id=agent_id, sdk=agent.runtime, provider_id=agent.provider_id,
            model_id=agent.model_id, protocol=agent.protocol,
        ))
    return config, storage, task


async def test_realtime_prompt_validation_idempotence_and_terminal_rejection(tmp_path):
    config, storage, task = await setup(tmp_path)
    app = create_app(config, storage, FakeContainerRuntime())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        url = f"/api/v3/tasks/{task.id}/blackboard/prompts"
        body = {"text": "  优先检查提供的附件  ", "idempotency_key": "hint-1"}
        first = await client.post(url, json=body)
        assert first.status_code == 200
        assert first.json()["actor"]["role"] == "user"
        assert first.json()["body"]["text"] == "优先检查提供的附件"
        assert (await client.post(url, json=body)).json()["id"] == first.json()["id"]
        assert len(await storage.list_entries(task.id)) == 1
        assert (await client.post(url, json={**body, "text": "   "})).status_code == 422
        assert (await client.post(url, json={**body, "text": "x" * 20001})).status_code == 422
        await storage.update_task(task.id, state=RunState.CANCELLED)
        assert (await client.post(url, json={**body, "idempotency_key": "late"})).status_code == 422


@pytest.mark.parametrize("agent_id", ["scout", "worker-openai", "worker-claude"])
async def test_realtime_user_prompt_bypasses_delivery_interval_for_scout_and_workers(tmp_path, agent_id):
    config, storage, task = await setup(tmp_path)
    board = Blackboard(storage)
    delivery = BlackboardDelivery(config, storage, board)
    initial = await delivery.checkpoint(task.id, agent_id)
    await delivery.checkpoint(task.id, agent_id, ack=initial["delivery_id"])
    await board.publish(task.id, USER_ACTOR, PublishRequest(
        kind="user_prompt", topic="realtime", body={"text": "new direction"}, idempotency_key="hint",
    ))
    update = await delivery.checkpoint(task.id, agent_id)
    assert "new direction" in update["context"]
    assert "direct user instructions" in update["context"]


async def test_collector_deduplicates_concurrent_retries_and_rejects_bad_evidence(tmp_path):
    _, storage, task = await setup(tmp_path)
    collector = ScoutIntelCollector(Blackboard(storage))
    request = ScoutIntelPublication(
        task_id=task.id, actor={"agent_id": "scout", "display_name": "Scout", "role": "scout"},
        kind="intel", body={"claim": "confirmed entry"}, idempotency_key="entry",
    )
    await asyncio.gather(*(collector.collect(request) for _ in range(8)))
    assert (await storage.get_agent(task.id, "scout")).metadata["intel_draft_count"] == 1
    with pytest.raises(ConflictError):
        changed = request.body.model_copy(update={"claim": "changed"})
        await collector.collect(request.model_copy(update={"body": changed}))
    with pytest.raises(FindingRejectedError):
        bad = request.model_dump(mode="json")
        bad["artifact_refs"] = [{"artifact_id": str(task.id), "locator": "missing:1"}]
        await collector.collect(ScoutIntelPublication.model_validate(bad))


def append(path, record):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as journal:
        journal.write(json.dumps(record) + "\n")


async def test_native_usage_includes_cached_inputs_and_survives_polling_and_tracker_restart(tmp_path):
    config, storage, task = await setup(tmp_path)
    base = Path(config.runtime.workspace_root) / str(task.id)
    codex = base / "scout/.codex/sessions/2026/10/31/session.jsonl"
    usage = {"input_tokens": 1000, "cached_input_tokens": 600, "output_tokens": 200, "total_tokens": 1200}
    append(codex, {"type": "token_usage_record", "payload": {"thread_token_usage": usage}})
    append(codex, {"type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": usage}}})
    claude = base / "worker-claude/.claude/projects/-workspace/session.jsonl"
    message = {"id": "message-1", "usage": {"input_tokens": 100, "cache_read_input_tokens": 400,
                                              "cache_creation_input_tokens": 50, "output_tokens": 20}}
    append(claude, {"type": "assistant", "message": message})
    append(claude, {"type": "assistant", "message": message})
    tracker = TokenUsageTracker(config, storage)
    for _ in range(3):
        await tracker.refresh_task(task.id)
    await TokenUsageTracker(config, storage).refresh_task(task.id)
    scout = (await storage.get_agent(task.id, "scout")).metadata["token_usage"]
    worker = (await storage.get_agent(task.id, "worker-claude")).metadata["token_usage"]
    assert scout["total_tokens"] == 1200
    assert worker["input_tokens"] == 550
    assert worker["total_tokens"] == 570
    assert "token_usage" not in (await storage.get_agent(task.id, "reporter")).metadata
    append(codex, {"type": "token_usage_record", "payload": {
        "thread_token_usage": {**usage, "input_tokens": 2000, "output_tokens": 400},
    }})
    await tracker.refresh_task(task.id)
    assert (await storage.get_agent(task.id, "scout")).metadata["token_usage"]["total_tokens"] == 2400
    app = create_app(config, storage, FakeContainerRuntime())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        detail = (await client.get(f"/api/v3/tasks/{task.id}")).json()
        assert all("token_usage_sources" not in agent["metadata"] for agent in detail["agents"])


async def test_usage_retries_partial_lines_and_counts_new_sessions_separately(tmp_path):
    config, storage, task = await setup(tmp_path)
    root = Path(config.runtime.workspace_root) / str(task.id) / "scout/.codex/sessions"
    root.mkdir(parents=True)
    record = json.dumps({"type": "event_msg", "payload": {"type": "token_count", "info": {
        "total_token_usage": {"input_tokens": 10, "output_tokens": 3},
    }}})
    first = root / "one.jsonl"
    first.write_text(record[:20])
    tracker = TokenUsageTracker(config, storage)
    await tracker.refresh_task(task.id)
    assert "token_usage" not in (await storage.get_agent(task.id, "scout")).metadata
    with first.open("a") as journal:
        journal.write(record[20:] + "\n")
    (root / "two.jsonl").write_text(record + "\n")
    await tracker.refresh_task(task.id)
    assert (await storage.get_agent(task.id, "scout")).metadata["token_usage"]["total_tokens"] == 26
