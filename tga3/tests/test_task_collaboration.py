import asyncio
import json
from pathlib import Path
from shutil import copytree

import httpx
import pytest

from tga3.app import create_app
from tga3.blackboard import Blackboard
from tga3.blackboard_delivery import BlackboardDelivery
from tga3.config import TGA3Config
from tga3.docker_runtime import FakeContainerRuntime
from tga3.domain import USER_ACTOR, AgentRun, PublishRequest, RunState
from tga3.errors import ContractError
from tga3.scout_intel import ScoutIntelInput, ScoutIntelMergeInput, ScoutIntelStore
from tga3.storage import InMemoryStorage
from tga3.token_usage import TokenUsageTracker


async def setup(tmp_path):
    copytree(Path(__file__).parents[1] / "config", tmp_path / "config")
    config = TGA3Config(tmp_path / "config")
    config.runtime.task_root = str(tmp_path / "tasks")
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


async def test_json_tool_is_phase_gated_and_deduplicates_concurrent_retries(tmp_path):
    config, storage, task = await setup(tmp_path)
    store = ScoutIntelStore(config, Blackboard(storage))
    request = ScoutIntelInput(task_id=task.id, claim="confirmed entry")
    with pytest.raises(ContractError, match="extraction"):
        await store.write(request)
    await storage.update_agent(task.id, "scout", metadata={"scout_phase": "extracting"})
    await asyncio.gather(*(store.write(request) for _ in range(8)))
    assert len(list(store.directory(task.id).glob("*.json"))) == 1
    assert not await storage.list_entries(task.id)
    assert "intel_drafts" not in (await storage.get_agent(task.id, "scout")).metadata
    changed = await store.write(request.model_copy(update={"claim": "changed"}))
    assert changed["id"] != (await store.write(request))["id"]
    assert len(store.documents(task.id)) == 2
    with pytest.raises(ContractError, match="Evidence"):
        await store.write(ScoutIntelInput(task_id=task.id, claim="bad", evidence_paths=["../missing"]))


async def test_json_files_survive_store_restart_and_import_in_order_without_duplicates(tmp_path):
    config, storage, task = await setup(tmp_path)
    board = Blackboard(storage)
    store = ScoutIntelStore(config, board)
    await storage.update_agent(task.id, "scout", metadata={"scout_phase": "extracting"})
    for key in ["z-first", "a-second"]:
        receipt = await store.write(ScoutIntelInput(task_id=task.id, claim=key))
        assert receipt["saved"] and receipt["path"].startswith("/intels/")
    restarted = ScoutIntelStore(config, board)
    await storage.update_agent(task.id, "scout", metadata={"scout_phase": "merging"})
    for item in restarted.documents(task.id):
        await restarted.merge(ScoutIntelMergeInput(task_id=task.id, claim=item.body.claim, merged_from_ids=[item.id]))
    await restarted.flush(task.id)
    await restarted.flush(task.id)
    assert [entry.body["claim"] for entry in await storage.list_entries(task.id)] == ["z-first", "a-second"]


async def test_distinct_concurrent_intels_share_one_atomic_collection_without_registration(tmp_path):
    config, storage, task = await setup(tmp_path)
    store = ScoutIntelStore(config, Blackboard(storage))
    await storage.update_agent(task.id, "scout", metadata={"scout_phase": "extracting"})
    receipts = await asyncio.gather(*(store.write(ScoutIntelInput(
        task_id=task.id, claim=f"fact-{index}")) for index in range(12)))
    assert len({receipt["id"] for receipt in receipts}) == 12
    files = list(store.directory(task.id).glob("*.json"))
    assert len(files) == 1 and files[0].name == "scout-intels.json"
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert len(payload["intels"]) == 12
    assert "artifact_refs" not in json.dumps(payload)
    assert not storage.artifacts


def append(path, record):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as journal:
        journal.write(json.dumps(record) + "\n")


async def test_scout_time_setting_persists_and_rejects_out_of_range_values(tmp_path):
    config, storage, _ = await setup(tmp_path)
    app = create_app(config, storage, FakeContainerRuntime())
    original_models = (tmp_path / "config/models.json").read_bytes()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.put("/api/v3/config/scout-settings", json={"timeout_seconds": 120})
        assert response.status_code == 200
        assert TGA3Config(tmp_path / "config").runtime.scout_timeout_seconds == 120
        for invalid in [0, 59, 601, 120.5]:
            rejected = await client.put("/api/v3/config/scout-settings", json={"timeout_seconds": invalid})
            assert rejected.status_code == 422
        assert (tmp_path / "config/models.json").read_bytes() == original_models


async def test_both_scout_contexts_contribute_to_one_cumulative_usage(tmp_path):
    config, storage, task = await setup(tmp_path)
    root = config.task_path(task.id, "workspaces") / "scout"
    for home, inputs in [(".codex", 100), (".codex-extract", 200)]:
        append(root / home / "sessions/same-name.jsonl", {"type": "event_msg", "payload": {
            "type": "token_count", "info": {"total_token_usage": {"input_tokens": inputs, "output_tokens": 20}},
        }})
    await TokenUsageTracker(config, storage).refresh_task(task.id)
    assert (await storage.get_agent(task.id, "scout")).metadata["token_usage"]["total_tokens"] == 340


async def test_native_usage_includes_cached_inputs_and_survives_polling_and_tracker_restart(tmp_path):
    config, storage, task = await setup(tmp_path)
    base = config.task_path(task.id, "workspaces")
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
    root = config.task_path(task.id, "workspaces") / "scout/.codex/sessions"
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


async def test_intel_actions_and_workspace_paths_are_registered_and_linked_by_server(tmp_path):
    from tga3.action_logs import read_action_log
    from tga3.workers.action_journal import ActionJournal

    config, storage, task = await setup(tmp_path)
    board = Blackboard(storage)
    store = ScoutIntelStore(config, board)
    root = config.task_path(task.id, "workspaces", "scout")
    logs = config.task_path(task.id, "logs", "scout")
    journal = ActionJournal(logs, identity={"task_id": str(task.id), "agent_id": "scout"}, output_directory=root)
    journal.record("probe", "shell", result={"aggregated_output": "observed"}, status="completed")
    action = read_action_log(logs)["actions"][0]
    await storage.update_agent(task.id, "scout", metadata={"scout_phase": "extracting"})
    for key in ("first", "second"):
        await store.write(ScoutIntelInput(task_id=task.id, claim=key,
                                         source_action_ids=[action["action_id"]],
                                         evidence_paths=[action["artifact"]["output_path"]]))
    assert not storage.artifacts
    assert not await storage.list_entries(task.id)
    await storage.update_agent(task.id, "scout", metadata={"scout_phase": "merging"})
    for item in store.documents(task.id):
        await store.merge(ScoutIntelMergeInput(task_id=task.id, claim=item.body.claim, merged_from_ids=[item.id]))
    await store.flush(task.id)
    entries = await storage.list_entries(task.id)
    assert len(entries) == 2
    assert entries[0].source_action_ids == entries[1].source_action_ids == [action["action_id"]]
    saved = json.loads(next(store.directory(task.id).glob("*.json")).read_text(encoding="utf-8"))
    assert saved["intels"][0]["source_action_ids"] == [action["action_id"]]
    assert saved["intels"][0]["body"]["claim"] in {"first", "second"}
    assert "artifact_refs" not in json.dumps(saved)
    assert len(saved["intels"]) == 2
    assert "input" not in saved and "publication" not in saved
    assert storage.links[entries[0].id] == storage.links[entries[1].id]
    assert Path(next(iter(storage.artifacts.values())).storage_path) == root / action["artifact"]["output_path"]
    assert (await storage.get_agent(task.id, "scout")).metadata["action_covered_count"] == 1


async def test_scout_coverage_counts_actions_inside_one_prefixed_manifest(tmp_path):
    from tga3.action_logs import read_action_log
    from tga3.workers.action_journal import ActionJournal

    config, storage, task = await setup(tmp_path)
    board = Blackboard(storage)
    store = ScoutIntelStore(config, board)
    logs = config.task_path(task.id, "logs", "scout")
    root = config.task_path(task.id, "workspaces", "scout")
    journal = ActionJournal(logs, identity={"task_id": str(task.id), "agent_id": "scout"}, output_directory=root)
    for index in range(2):
        journal.record("item_0", "shell", result={"aggregated_output": str(index)}, status="completed")
    actions = read_action_log(logs)["actions"]
    assert len(list(logs.glob("*.json"))) == 1
    assert all(action["action_id"].startswith("scout-action-") for action in actions)
    await storage.update_agent(task.id, "scout", metadata={"scout_phase": "extracting"})
    for index, action in enumerate(actions):
        await store.write(ScoutIntelInput(task_id=task.id, claim=str(index),
                                         source_action_ids=[action["action_id"]],
                                         evidence_paths=[action["artifact"]["output_path"]]))
        if index == 0:
            with pytest.raises(ContractError, match="coverage mismatch"):
                store.validate_extraction(task.id)
    assert store.validate_extraction(task.id)["action_count"] == 2
    await storage.update_agent(task.id, "scout", metadata={"scout_phase": "merging"})
    await store.merge(ScoutIntelMergeInput(task_id=task.id, claim="both observations",
                                         merged_from_ids=[item.id for item in store.documents(task.id)]))
    await store.flush(task.id)
    metadata = (await storage.get_agent(task.id, "scout")).metadata
    assert metadata["action_count"] == metadata["action_covered_count"] == 2
    assert metadata["action_uncovered"] == []
    merged = store.documents(task.id, merged=True)[0]
    assert set(merged.source_action_ids) == {action["action_id"] for action in actions}
    assert len(merged.evidence_paths) == len(storage.artifacts) == 2
