import asyncio
import json

import httpx
import pytest
from test_aoe_runtime import setup

from tga3.aoe_models import active_attempts, solo_attempts
from tga3.app import create_app
from tga3.domain import USER_ACTOR, AgentState, EntryKind, PublishRequest, RunState
from tga3.errors import ConflictError
from tga3.host_agents import DeterministicHostModel


async def test_extend_question_requires_started_timer_updates_all_active_workers_and_enforces_cap(tmp_path):
    cfg, storage, containers, c, parent, clock = await setup(tmp_path)
    outbox = cfg.task_path(parent.id, "workspaces", "scout") / "outbox"
    outbox.mkdir(parents=True, exist_ok=True)
    (outbox / "q1.json").write_text(
        json.dumps(
            {
                "source_key": "q1",
                "title": "Crypto Challenge",
                "description": "Break RSA",
                "difficulty": "easy",
                "target": {"mode": "none"},
            }
        ),
        encoding="utf-8",
    )
    await c.aoe.collection.import_outbox(parent.id)
    await c.aoe.finish_collection(parent.id, expected_count=1)
    await c.aoe.tick(parent.id)
    q = (await storage.list_questions(parent.id))[0]
    assert q.metadata["status"] == "running"

    # Before first tool action (deadline is None), extension must be rejected.
    with pytest.raises(ConflictError, match="尚未开始倒计时"):
        await c.aoe.extend_question_attempt(parent.id, q.id, minutes=5)

    # Start countdown and capture live gateway notifications.
    notified = []

    async def fake_notify(task_id, agent_id, method, params=None):
        notified.append((task_id, agent_id, method, params))

    c.gateway.notify = fake_notify
    active = active_attempts(q)
    await c.aoe.action_started(q.id, active[0]["agent_id"], active[0]["id"])
    started_q = await storage.get_task(q.id)
    original_deadline = active_attempts(started_q)[0]["deadline"]
    assert original_deadline is not None

    notified.clear()
    res = await c.aoe.extend_question_attempt(parent.id, q.id, minutes=12)
    assert res["added_minutes"] == 12
    assert res["deadline"] == original_deadline + 12 * 60
    assert len(notified) == len(active_attempts(started_q))
    assert all(m == "session.deadline" and p["deadline"] == res["deadline"] for _, _, m, p in notified)

    refreshed = await storage.get_task(q.id)
    updated_active = active_attempts(refreshed)
    assert all(a["deadline"] == original_deadline + 12 * 60 for a in updated_active)
    assert all(a["manual_extension_seconds"] == 12 * 60 for a in updated_active)
    assert all(a["manual_extension_count"] == 1 for a in updated_active)
    if refreshed.metadata.get("swarm"):
        assert refreshed.metadata["swarm"]["deadline"] == original_deadline + 12 * 60

    # Cumulative extension cap (240 minutes) is enforced.
    with pytest.raises(ConflictError, match="240"):
        await c.aoe.extend_question_attempt(parent.id, q.id, minutes=230)


async def test_aoe_restart_resumes_from_checkpoint_preserves_solved_and_optionally_retries_abandoned(tmp_path):
    cfg, storage, containers, c, parent, clock = await setup(tmp_path)
    c.aoe.automation = None
    outbox = cfg.task_path(parent.id, "workspaces", "scout") / "outbox"
    outbox.mkdir(parents=True, exist_ok=True)
    items = [
        ("q-solved", "Solved One"),
        ("q-interrupted", "Interrupted One"),
        ("q-abandoned", "Abandoned One"),
    ]
    for key, title in items:
        (outbox / f"{key}.json").write_text(
            json.dumps(
                {
                    "source_key": key,
                    "title": title,
                    "description": f"Desc {key}",
                    "difficulty": "easy",
                    "target": {"mode": "none"},
                }
            ),
            encoding="utf-8",
        )
    await c.aoe.collection.import_outbox(parent.id)
    await c.aoe.finish_collection(parent.id, expected_count=3)
    await storage.update_task(parent.id, metadata={"scout_complete": True})

    by_key = {q.metadata["source_key"]: q for q in await storage.list_questions(parent.id)}
    # 1) Mark q-solved as already solved & completed.
    await storage.update_task(
        by_key["q-solved"].id,
        state=RunState.COMPLETED,
        metadata={"status": "solved", "flag": "flag{ok}", "solved_at": 100.0},
    )
    # 2) Mark q-abandoned as abandoned after swarm.
    await storage.update_task(
        by_key["q-abandoned"].id,
        state=RunState.CANCELLED,
        metadata={
            "status": "abandoned",
            "finish_reason": "joint_failed",
            "swarm": {"entered_at": 10, "started_at": 10, "deadline": 20},
            "attempts": [{"id": "a-old", "agent_id": "worker-openai", "phase": "solo", "outcome": "timeout"}],
        },
    )
    # 3) Mark q-interrupted as running, then stop_task() to simulate user cancellation mid-attempt.
    await storage.update_task(
        by_key["q-interrupted"].id,
        state=RunState.RUNNING,
        metadata={
            "status": "running",
            "attempts": [
                {
                    "id": "a-cut",
                    "agent_id": "worker-openai",
                    "phase": "solo",
                    "started_at": 50.0,
                    "deadline": 1200.0,
                    "outcome": None,
                }
            ],
        },
    )
    await c.stop_task(parent.id)
    assert (await storage.get_task(parent.id)).state == RunState.CANCELLED

    # In-place restart (default: skip Scout, keep solved, resume interrupted, do NOT touch abandoned).
    res = await c.restart_task(parent.id)
    try:
        assert res["resumed_from"] == "workers"
        assert res["restart_count"] == 1
        assert res["summary"]["resumed_questions"] == 1
        assert res["summary"]["requeued_abandoned"] == 0

        q_solved = await storage.get_task(by_key["q-solved"].id)
        q_int = await storage.get_task(by_key["q-interrupted"].id)
        q_ab = await storage.get_task(by_key["q-abandoned"].id)
        assert q_solved.metadata["status"] == "solved" and q_solved.metadata["flag"] == "flag{ok}"
        assert q_int.metadata["status"] == "queued" and q_int.state == RunState.RUNNING
        # Interrupted attempt is superseded so it does not consume the 2 solo attempts quota!
        assert solo_attempts(q_int) == []
        assert q_ab.metadata["status"] == "abandoned"
    finally:
        await c.stop_task(parent.id)

    # Now mark q-interrupted solved as well so only q-abandoned remains.
    await storage.update_task(
        by_key["q-interrupted"].id,
        state=RunState.COMPLETED,
        metadata={"status": "solved", "flag": "flag{two}"},
    )
    # Without retry_abandoned or rerun_scout, restart rejects cleanly with guidance.
    with pytest.raises(ConflictError, match="重新挑战已放弃题目"):
        await c.restart_task(parent.id)

    # With retry_abandoned=True and updated budgets, q-abandoned returns to queued with clean swarm/rescue state.
    res2 = await c.restart_task(
        parent.id,
        retry_abandoned=True,
        aoe_first_attempt_minutes=25,
        aoe_second_attempt_minutes=12,
        aoe_joint_minutes=35,
    )
    try:
        assert res2["summary"]["requeued_abandoned"] == 1
        q_ab2 = await storage.get_task(by_key["q-abandoned"].id)
        assert q_ab2.metadata["status"] == "queued"
        assert "swarm" not in q_ab2.metadata
        assert "finish_reason" not in q_ab2.metadata
        assert solo_attempts(q_ab2) == []
        refreshed_parent = await storage.get_task(parent.id)
        assert refreshed_parent.metadata["aoe_attempt_budgets"] == {"first": 1500, "second": 720, "joint": 2100}
    finally:
        await c.stop_task(parent.id)


async def test_normal_task_restart_skips_completed_scout_and_clone_config_returns_full_template(tmp_path):
    cfg, storage, containers, c, _, _ = await setup(tmp_path)
    app = create_app(cfg, storage, containers, host_model=DeterministicHostModel())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Create a normal single-question task and stop it after Scout completed.
        task = await storage.create_task(
            "Single Web Task",
            "penetration_test",
            metadata={"knowledge_source": "online", "produce_report": True},
        )
        cfg.ensure_task_directories(task.id)
        for agent_id in cfg.agents.agents:
            resolved = cfg.resolve_agent(agent_id)
            from tga3.domain import AgentRun

            await storage.upsert_agent(
                AgentRun(
                    task_id=task.id,
                    agent_id=agent_id,
                    sdk=resolved.runtime,
                    provider_id=resolved.provider.id,
                    model_id=resolved.model.id,
                    protocol=resolved.protocol,
                    actual_state=AgentState.COMPLETED if agent_id == "scout" else AgentState.STOPPED,
                    metadata={"scout_phase": "completed"} if agent_id == "scout" else {},
                )
            )
        await app.state.coordinator.blackboard.publish(
            task.id,
            USER_ACTOR,
            PublishRequest(
                kind=EntryKind.USER_PROMPT,
                topic="task",
                body={"text": "Target http://10.0.0.1:8000 find admin flag"},
                idempotency_key="initial-user-prompt",
            ),
        )
        await storage.update_task(task.id, state=RunState.CANCELLED)

        # Clone-config endpoint returns exact prefill template.
        clone_resp = await client.get(f"/api/v3/tasks/{task.id}/clone-config")
        assert clone_resp.status_code == 200
        clone = clone_resp.json()
        assert clone["title"] == "Single Web Task"
        assert clone["scene_id"] == "penetration_test"
        assert "http://10.0.0.1:8000" in clone["prompt"]
        assert "scout" in clone["agent_models"]

        # Restart endpoint resumes straight at Workers since Scout already completed.
        containers.launched.clear()
        restart_resp = await client.post(f"/api/v3/tasks/{task.id}/restart", json={"rerun_scout": False})
        assert restart_resp.status_code == 200, restart_resp.text
        payload = restart_resp.json()
        assert payload["resumed_from"] == "workers"
        assert payload["restart_count"] == 1
        await asyncio.sleep(0.05)
        assert len(containers.launched) == len(cfg.worker_agent_ids)
        assert all(spec.fresh_session is True for spec in containers.launched)
        await app.state.coordinator.stop_task(task.id)
