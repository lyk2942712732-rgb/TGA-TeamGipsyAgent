import asyncio
import json
from pathlib import Path
from uuid import UUID

import httpx
from pydantic import SecretStr

from tga3.app import create_app
from tga3.config import TGA3Config
from tga3.docker_runtime import FakeContainerRuntime
from tga3.domain import SYSTEM_ACTOR, EventKind
from tga3.host_agents import DeterministicHostModel
from tga3.storage import InMemoryStorage


class CollectingRuntime(FakeContainerRuntime):
    def __init__(self, storage):
        super().__init__()
        self.storage = storage

    async def scout(self, spec, emit):
        await super().scout(spec, emit)
        if spec.scout_phase == "collecting":
            while not (await self.storage.get_task(spec.task_id)).metadata.get("collection_complete"):
                await asyncio.sleep(0.01)


async def test_create_aoe_with_separate_stage_budgets_and_reject_invalid_values(tmp_path):
    cfg = TGA3Config(Path(__file__).parents[1] / "config")
    cfg.runtime.task_root = str(tmp_path / "tasks")
    for provider in cfg.models.providers:
        provider.api_keys[0].api_key = SecretStr("fixture-key")
    storage = InMemoryStorage()
    app = create_app(cfg, storage, CollectingRuntime(storage), host_model=DeterministicHostModel())
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            payload = {"title": "batch", "prompt": "collect", "scene_id": "aoe",
                       "aoe_first_attempt_minutes": "30", "aoe_second_attempt_minutes": "15", "aoe_joint_minutes": "20"}
            response = await client.post("/api/v3/tasks", data=payload)
            assert response.status_code == 200
            assert response.json()["metadata"]["aoe_attempt_budgets"] == {"first": 1800, "second": 900, "joint": 1200}
            assert "aoe_attempt_seconds" not in response.json()["metadata"]
            overview = await client.get(f'/api/v3/tasks/{response.json()["id"]}/questions')
            assert overview.json()["attempt_budgets"] == {"first": 1800, "second": 900, "joint": 1200}
            for field in ("aoe_first_attempt_minutes", "aoe_second_attempt_minutes", "aoe_joint_minutes"):
                for value in ("0", "1441", "1.5"):
                    assert (await client.post("/api/v3/tasks", data={**payload, field: value})).status_code == 422


async def test_aoe_collection_mcp_budget_owned_downloads_and_hierarchical_events(tmp_path):
    cfg = TGA3Config(Path(__file__).parents[1] / "config")
    cfg.runtime.task_root = str(tmp_path / "tasks")
    for provider in cfg.models.providers:
        provider.api_keys[0].api_key = SecretStr("fixture-key")
    storage = InMemoryStorage()
    runtime = CollectingRuntime(storage)
    app = create_app(cfg, storage, runtime, host_model=DeterministicHostModel())
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost:8083"
        ) as client:
            response = await client.post(
                "/api/v3/tasks",
                data={
                    "title": "two questions",
                    "prompt": "Collect both source questions",
                    "scene_id": "aoe",
                    "aoe_attempt_minutes": "7",
                },
                files={"files": ("source.txt", b"exact attachment", "text/plain")},
            )
            assert response.status_code == 200
            parent_id = UUID(response.json()["id"])
            assert response.json()["metadata"]["aoe_attempt_seconds"] == 420
            coordinator = app.state.coordinator
            for _ in range(100):
                if (await storage.get_agent(parent_id, "scout")).metadata.get("scout_phase") == "collecting":
                    break
                await asyncio.sleep(0.01)
            original = (await storage.list_input_files(parent_id))[0]
            headers = {
                "accept": "application/json, text/event-stream",
                "x-tga3-task-id": str(parent_id),
                "x-tga3-agent-id": "scout",
            }

            async def call(name, arguments, identity=headers):
                result = await client.post(
                    "/aoe-scout-mcp/",
                    headers=identity,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {"name": name, "arguments": arguments},
                    },
                )
                return result.json()["result"]

            args = {
                "questions": [
                    {
                        "source_key": "q1",
                        "title": "One",
                        "description": "Original one",
                        "difficulty": "easy",
                        "attachments": ["/inputs/" + Path(original.storage_path).name],
                        "target": {"mode": "none"},
                    },
                    {"source_key": "q2", "title": "Two", "description": "Original two", "difficulty": "hard"},
                ]
            }
            assert (await call("scout_register_questions", args, {"accept": headers["accept"]}))["isError"]
            assert (await call("scout_register_questions", args, {**headers, "x-tga3-agent-id": "worker-openai"}))[
                "isError"
            ]
            registered = await call("scout_register_questions", args)
            assert not registered.get("isError")
            receipt = json.loads(registered["content"][0]["text"])
            first, second = [UUID(item["question_id"]) for item in receipt["questions"]]
            saved = json.loads((cfg.task_path(first) / "question.json").read_text())
            assert saved["target"]["mode"] == "none"
            overview = (await client.get(f"/api/v3/tasks/{parent_id}/questions")).json()
            assert len(overview["questions"]) == 2 and overview["attempt_seconds"] == 420
            first_view = next(q for q in overview["questions"] if q["source_key"] == "q1")
            file_id = first_view["attachments"][0]["id"]
            assert (
                await client.get(f"/api/v3/tasks/{parent_id}/questions/{first}/files/{file_id}")
            ).content == b"exact attachment"
            assert (await client.get(f"/api/v3/tasks/{second}/questions/{first}/files/{file_id}")).status_code == 422
            assert (await call("scout_finish_collection", {"expected_count": 3}))["isError"]
            assert not (await call("scout_finish_collection", {"expected_count": 2})).get("isError")
            for _ in range(100):
                if (await storage.get_task(parent_id)).metadata.get("aoe_phase") == "solo":
                    break
                await asyncio.sleep(0.01)
            assert [spec.scout_phase for spec in runtime.launched if spec.agent.agent_id == "scout"] == ["collecting"]
            assert (await call("scout_register_questions", args))["isError"]
            await coordinator.events.emit(
                first,
                actor=SYSTEM_ACTOR,
                kind=EventKind.SYSTEM,
                text="one-openai-event",
                channel_agent_id="worker-openai",
            )
            await coordinator.events.emit(
                first,
                actor=SYSTEM_ACTOR,
                kind=EventKind.SYSTEM,
                text="one-claude-event",
                channel_agent_id="worker-claude",
            )
            await coordinator.events.emit(
                second, actor=SYSTEM_ACTOR, kind=EventKind.SYSTEM, text="two-event", channel_agent_id="worker-openai"
            )
            filtered = (
                await client.get(
                    f"/api/v3/tasks/{parent_id}/aoe/events",
                    params={
                        "question_id": str(first),
                        "agent_id": "worker-openai",
                    },
                )
            ).json()["events"]
            assert any(event["text"] == "one-openai-event" for event in filtered)
            assert not any(event["text"] in {"one-claude-event", "two-event"} for event in filtered)
            assert all(
                event.get("question_id", event["payload"].get("question_id")) == str(first) for event in filtered
            )
            assert len((await client.get("/api/v3/tasks")).json()) == 1
            parent_board = (await client.get(f"/api/v3/tasks/{parent_id}/blackboard")).json()
            child_board = (await client.get(f"/api/v3/tasks/{first}/blackboard")).json()
            assert all(entry["task_id"] == str(parent_id) for entry in parent_board["entries"])
            assert all(entry["question_id"] == str(first) for entry in child_board["entries"])
            stopped = await client.post(f"/api/v3/tasks/{parent_id}/stop")
            assert stopped.status_code == 204
            assert not active_children(storage, parent_id)


def active_children(storage, parent_id):
    return [
        task
        for task in storage.tasks.values()
        if task.parent_task_id == parent_id and task.state not in {"completed", "failed", "cancelled"}
    ]
