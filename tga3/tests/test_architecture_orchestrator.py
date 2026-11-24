import asyncio
from uuid import UUID

import httpx
from test_aoe_runtime import setup

from tga3.app import create_app
from tga3.config import SceneConfig
from tga3.domain import RunState
from tga3.host_agents import DeterministicHostModel
from tga3.orchestrator import BaseTaskOrchestrator, TaskCreateOptions, TaskRestartOptions
from tga3.task_metadata import AOEParentMeta, AOEQuestionMeta, clear_question_round_state


class CustomArenaOrchestrator(BaseTaskOrchestrator):
    """Proof that a 3rd execution mode plugs in without touching coordinator or routes."""

    mode_id = "custom_arena"
    auto_report_on_blackboard_change = False

    def __init__(self, coordinator):
        super().__init__(coordinator)
        self.events: list[tuple[str, str]] = []

    def build_initial_metadata(self, options: TaskCreateOptions) -> dict:
        return {**super().build_initial_metadata(options), "arena_round": 1}

    async def run_startup(self, task_id, bindings) -> None:
        self.events.append(("start", str(task_id)))
        await self.coordinator.storage.update_task(task_id, state=RunState.RUNNING)

    async def on_stop(self, task) -> None:
        self.events.append(("stop", str(task.id)))

    async def restart(
        self, task, *, existing_runs, resolved_agents, options: TaskRestartOptions, restart_count, fallback_models
    ):
        self.events.append(("restart", str(task.id)))
        refreshed = await self.coordinator.storage.update_task(
            task.id, state=RunState.RUNNING, metadata={"restart_count": restart_count, "arena_round": 2}
        )
        return {
            "task": refreshed.model_dump(mode="json"),
            "resumed_from": "workers",
            "restart_count": restart_count,
            "fallback_models": fallback_models,
        }

    async def before_delete(self, task) -> None:
        self.events.append(("before_delete", str(task.id)))

    def after_delete(self, task) -> None:
        self.events.append(("after_delete", str(task.id)))


async def test_pluggable_third_mode_orchestrator_works_end_to_end_via_http_api(tmp_path):
    cfg, storage, containers, _, _, _ = await setup(tmp_path)
    cfg.scenes.scenes.append(
        SceneConfig(
            id="custom_arena",
            name="Custom Arena",
            description="Third execution mode fixture",
            system_prompt="Arena mode system prompt.",
        )
    )
    app = create_app(cfg, storage, containers, host_model=DeterministicHostModel())
    arena = CustomArenaOrchestrator(app.state.coordinator)
    app.state.coordinator.orchestrators.register(arena)

    # Automation is formally attached to coordinator and proxied cleanly to aoe.
    assert app.state.coordinator.automation is app.state.automation
    assert app.state.coordinator.aoe.automation is app.state.automation

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v3/tasks",
            data={"title": "Arena Run", "prompt": "Fight!", "scene_id": "custom_arena"},
        )
        assert resp.status_code == 200, resp.text
        created = resp.json()
        task_id = created["id"]
        assert created["metadata"]["arena_round"] == 1
        await asyncio.sleep(0.05)
        assert ("start", task_id) in arena.events

        # Automation respects the custom orchestrator's auto_report_on_blackboard_change flag.
        stored = await storage.get_task(UUID(task_id))
        assert app.state.automation.should_auto_report(stored) is False

        stop_resp = await client.post(f"/api/v3/tasks/{task_id}/stop")
        assert stop_resp.status_code == 204
        assert ("stop", task_id) in arena.events

        restart_resp = await client.post(f"/api/v3/tasks/{task_id}/restart", json={})
        assert restart_resp.status_code == 200, restart_resp.text
        assert restart_resp.json()["task"]["metadata"]["arena_round"] == 2
        assert ("restart", task_id) in arena.events

        del_resp = await client.delete(f"/api/v3/tasks/{task_id}")
        assert del_resp.status_code == 204
        assert ("before_delete", task_id) in arena.events
        assert ("after_delete", task_id) in arena.events


async def test_typed_task_metadata_and_round_reset_helpers(tmp_path):
    _, storage, _, _, parent, _ = await setup(tmp_path)
    parent_meta = AOEParentMeta.from_task(parent)
    assert set(parent_meta.budgets()) == {"first", "second", "joint"}

    raw = {
        "source_key": "q1",
        "description": "Solve me",
        "status": "abandoned",
        "swarm": {"entered_at": 10.0, "started_at": 10.0, "deadline": 20.0},
        "rescue": {"state": "seeking", "requested_at": 11.0, "threshold_at": 10.0},
        "reinforcement": {"agent_id": "worker-claude", "joined_at": 12.0, "first_attempt_id": "a1"},
        "finish_reason": "joint_failed",
        "attempts": [{"id": "a1", "agent_id": "worker-openai", "phase": "solo", "outcome": "timeout"}],
    }
    cleaned = clear_question_round_state(raw, supersede_all=True)
    for key in ("swarm", "rescue", "reinforcement", "finish_reason"):
        assert key not in cleaned
    assert cleaned["status"] == "queued"
    assert cleaned["attempts"][0]["superseded"] is True

    q = await storage.create_task("Q1", "aoe", parent_task_id=parent.id, metadata=cleaned)
    q_meta = AOEQuestionMeta.from_task(q)
    assert q_meta.source_key == "q1"
    assert q_meta.report_state(RunState.RUNNING) == "pending"
