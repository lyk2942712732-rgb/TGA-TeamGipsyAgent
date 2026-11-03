import json
from pathlib import Path

import pytest

from tga3.action_logs import read_action_log
from tga3.action_materials import action_materials, file_material
from tga3.blackboard import Blackboard
from tga3.config import TGA3Config
from tga3.domain import AgentRun, RunState
from tga3.errors import ContractError
from tga3.scout_intel import ScoutIntelMergeInput, ScoutIntelStore
from tga3.storage import InMemoryStorage
from tga3.workers.action_journal import ActionJournal


async def fixture(tmp_path):
    cfg = TGA3Config(Path(__file__).parents[1] / "config")
    cfg.runtime.task_root = str(tmp_path / "tasks")
    storage = InMemoryStorage()
    task = await storage.create_task("materials", "pwn")
    await storage.update_task(task.id, state=RunState.SCOUTING)
    await storage.upsert_agent(AgentRun(task_id=task.id, agent_id="scout", sdk="codex_cli",
        provider_id="openai", model_id="test", protocol="openai_responses", metadata={"scout_phase": "extracting"}))
    root, logs = cfg.task_path(task.id, "workspaces", "scout"), cfg.task_path(task.id, "logs", "scout")
    root.mkdir(parents=True)
    journal = ActionJournal(logs, identity={"task_id": str(task.id), "agent_id": "scout"}, output_directory=root)
    journal.record("one", "shell", inputs={"command": "fixture > response.txt"})
    (root / "response.txt").write_text("HTTP 403 exact literal evidence", encoding="utf-8")
    journal.record("one", "shell", result={"aggregated_output": "", "exit_code": 0}, status="completed")
    action = read_action_log(logs)["actions"][0]
    return cfg, storage, task, root, logs, action


async def test_materials_include_redirected_attachment_and_exact_empty_stdout(tmp_path):
    _, _, task, root, logs, action = await fixture(tmp_path)
    result = action_materials(logs, root, task.id, [action["action_id"]])
    material = result["materials"][0]
    assert material["output"]["text"] == "" and not material["output"]["truncated"]
    assert material["attachments"][0]["text"] == "HTTP 403 exact literal evidence"
    assert json.loads(material["action_json"]) == action["action"]
    assert not action_materials(logs, root, task.id, [])["materials"]


def test_materials_mark_truncation_binary_and_missing_without_fabricated_text(tmp_path):
    (tmp_path / "large.txt").write_text("中" * 100, encoding="utf-8")
    value = file_material(tmp_path, "large.txt", 20)
    assert value["truncated"] and value["text"] in "中" * 100
    (tmp_path / "data.bin").write_bytes(b"\xff\xfe")
    assert file_material(tmp_path, "data.bin", 20)["reason"] == "not_utf8"
    assert not file_material(tmp_path, "missing.txt", 20)["text_available"]
    assert not file_material(tmp_path, "../other", 20)["text_available"]


async def test_default_sources_are_exact_and_singleton_is_a_verbatim_copy(tmp_path):
    cfg, storage, task, _, _, action = await fixture(tmp_path)
    store = ScoutIntelStore(cfg, Blackboard(storage))
    receipt = await store.write_action(task.id, action["action_id"], "literal fact", "literal detail", "scope")
    original = store.documents(task.id)[0]
    assert original.source_action_ids == [action["action_id"]]
    assert set(original.evidence_paths) == {action["artifact"]["output_path"], "response.txt"}
    assert not storage.artifacts
    with pytest.raises(ContractError):
        await store.write_action(task.id, action["action_id"], "fact", "", "scope", ["other.txt"])
    await storage.update_agent(task.id, "scout", metadata={"scout_phase": "merging"})
    request = ScoutIntelMergeInput(task_id=task.id, merged_from_ids=[receipt["id"]])
    saved = await store.merge(request)
    final = store.documents(task.id, merged=True)[0]
    assert final.body == original.body and final.topic == original.topic
    assert final.evidence_paths == original.evidence_paths
    assert (await store.merge(request))["id"] == saved["id"]
    with pytest.raises(ContractError, match="verbatim"):
        await store.merge(request.model_copy(update={"claim": "rewritten"}))


async def test_scout_http_connection_binds_both_phases_and_rejects_other_agents(tmp_path):
    import httpx

    from tga3.app import create_app
    from tga3.docker_runtime import FakeContainerRuntime

    cfg, storage, task, _, _, action = await fixture(tmp_path)
    app = create_app(cfg, storage, FakeContainerRuntime())
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://localhost:8083") as client:
            headers = {"accept": "application/json, text/event-stream", "x-tga3-task-id": str(task.id),
                       "x-tga3-agent-id": "scout"}

            async def call(name, args, identity=headers):
                return (await client.post("/scout-mcp/", headers=identity, json={
                    "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                    "params": {"name": name, "arguments": args},
                })).json()["result"]

            args = {"action_id": action["action_id"], "claim": "observed fact", "detail": "observed scope"}
            assert (await call("scout_write_intel", args, {"accept": headers["accept"]}))["isError"]
            assert (await call("scout_write_intel", args, {**headers, "x-tga3-agent-id": "worker-openai"}))["isError"]
            result = await call("scout_write_intel", args)
            assert not result.get("isError")
            receipt = json.loads(result["content"][0]["text"])
            await storage.update_agent(task.id, "scout", metadata={"scout_phase": "merging"})
            result = await call("scout_merge_intel", {"merged_from_ids": [receipt["id"]]})
            assert not result.get("isError")
            store = app.state.coordinator.scout_intel
            assert store.documents(task.id, merged=True)[0].body == store.documents(task.id)[0].body
            assert not storage.artifacts
