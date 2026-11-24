"""Tests for unified AOE Scout zero-token sentinel watch, incremental collection, and finish-confirm gate."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from tga3.aoe_models import AOEQuestionInput
from tga3.aoe_watch import (
    DEFAULT_PROBE_INTERVAL_SECONDS,
    MAX_CONSECUTIVE_PROBE_FAILURES,
    WATCH_PROBE_FILENAME,
    parse_probe_output,
)
from tga3.app import create_app
from tga3.config import TGA3Config
from tga3.coordinator import TaskCoordinator
from tga3.docker_runtime import FakeContainerRuntime
from tga3.domain import AgentRun, AgentState, RunState
from tga3.errors import ConflictError
from tga3.storage import InMemoryStorage


class _ReportQueue:
    def __init__(self) -> None:
        self.questions = []
        self.cancelled = []

    def start_report(self, task_id) -> None:
        if task_id not in self.questions:
            self.questions.append(task_id)

    async def cancel(self, task_id) -> None:
        self.cancelled.append(task_id)


async def _setup_batch(tmp_path: Path, *, finish_confirm_enabled: bool = True, four_workers: bool = False):
    config = TGA3Config(Path(__file__).parents[1] / "config")
    if not four_workers:
        config.agents.agents.pop("worker-opencode", None)
        config.agents.agents.pop("worker-qwen", None)
    config.runtime.task_root = str(tmp_path / "tasks")
    for provider in config.models.providers:
        provider.api_keys[0].api_key = SecretStr("fixture-key")
    storage = InMemoryStorage()
    containers = FakeContainerRuntime()
    coordinator = TaskCoordinator(config, storage, containers)
    parent = await storage.create_task(
        "continuous-ctf",
        "aoe",
        metadata={
            "aoe_attempt_seconds": 1200,
            "aoe_phase": "collecting",
            "aoe_scout_mode": "collection_only",
            "aoe_finish_confirm_enabled": finish_confirm_enabled,
        },
    )
    config.ensure_task_directories(parent.id)
    for agent_id, definition in config.agents.agents.items():
        await storage.upsert_agent(
            AgentRun(
                task_id=parent.id,
                agent_id=agent_id,
                sdk=definition.runtime,
                provider_id=definition.provider_id,
                model_id=definition.model_id,
                protocol=definition.protocol,
                metadata={"scout_phase": "collecting"} if agent_id == "scout" else {},
            )
        )
    await storage.update_task(parent.id, state=RunState.SCOUTING)
    coordinator.aoe.automation = _ReportQueue()
    clock = [1000.0]
    coordinator.aoe.clock = lambda: clock[0]
    return config, storage, containers, coordinator, parent, clock


def _write_probe_script(config: TGA3Config, parent_id, content: str = "print('{}')\n") -> Path:
    path = config.task_path(parent_id, "workspaces", "scout") / WATCH_PROBE_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_parse_probe_output_handles_clean_and_noisy_stdout():
    parsed = parse_probe_output('debug line\n{"source_keys": ["q1", "q2", "q1"], "solved_keys": ["q1"]}\n')
    assert parsed == {"source_keys": ["q1", "q2"], "solved_keys": ["q1"], "notes": ""}
    with pytest.raises(ValueError):
        parse_probe_output("not json")
    with pytest.raises(ValueError):
        parse_probe_output('{"solved_keys": []}')


async def test_initial_collection_defaults_to_zero_token_sentinel_only_when_script_exists(tmp_path):
    cfg, storage, containers, coordinator, parent, clock = await _setup_batch(tmp_path)
    aoe = coordinator.aoe
    bindings = {
        run.agent_id: cfg.resolve_agent(run.agent_id, run.provider_id, run.model_id, run.protocol)
        for run in await storage.list_agents(parent.id)
    }

    # Case 1: With watch_probe.py -> auto-enables zero-token sentinel.
    _write_probe_script(cfg, parent.id)
    await aoe.register_questions(
        parent.id, [AOEQuestionInput(source_key="q1", title="Q1", description="first")]
    )
    await aoe.finish_collection(parent.id, 1)
    await aoe.scout_runner.run(parent.id, bindings)
    snap = await aoe.snapshot(parent.id)
    watch = snap["scout_watch"]
    assert watch["mode"] == "sentinel"
    assert watch["enabled"] is True
    assert watch["state"] == "watching"
    assert watch["probe_script_present"] is True
    assert watch["next_check_at"] == pytest.approx(clock[0] + DEFAULT_PROBE_INTERVAL_SECONDS)
    scout = await storage.get_agent(parent.id, "scout")
    assert scout.actual_state == AgentState.IDLE
    assert scout.metadata["scout_phase"] == "watching"
    await coordinator.close()

    # Case 2: Without watch_probe.py -> stays manual/idle (NEVER auto-enables LLM polling).
    cfg2, storage2, _, coordinator2, parent2, _ = await _setup_batch(tmp_path / "no-script")
    bindings2 = {
        run.agent_id: cfg2.resolve_agent(run.agent_id, run.provider_id, run.model_id, run.protocol)
        for run in await storage2.list_agents(parent2.id)
    }
    await coordinator2.aoe.register_questions(
        parent2.id, [AOEQuestionInput(source_key="q1", title="Q1", description="first")]
    )
    await coordinator2.aoe.finish_collection(parent2.id, 1)
    await coordinator2.aoe.scout_runner.run(parent2.id, bindings2)
    snap2 = await coordinator2.aoe.snapshot(parent2.id)
    watch2 = snap2["scout_watch"]
    assert watch2["mode"] == "manual"
    assert watch2["enabled"] is False
    assert watch2["state"] == "idle"
    assert watch2["probe_script_present"] is False
    assert watch2["next_check_at"] is None
    await coordinator2.close()


async def test_sentinel_probe_zero_token_no_change_solved_sync_new_keys_and_circuit_breaker(tmp_path):
    cfg, storage, containers, coordinator, parent, clock = await _setup_batch(tmp_path)
    aoe = coordinator.aoe
    _write_probe_script(cfg, parent.id)
    await aoe.register_questions(
        parent.id,
        [
            AOEQuestionInput(source_key="q1", title="Q1", description="one"),
            AOEQuestionInput(source_key="q2", title="Q2", description="two"),
        ],
    )
    await aoe.finish_collection(parent.id, 2)
    await aoe.watch.on_initial_collection_complete(parent.id)
    launched_before = len(containers.launched)

    # 1) No new keys, no solved keys -> 0 LLM calls.
    containers.probe_handler = lambda _tid, _to: (0, '{"source_keys": ["q1", "q2"], "solved_keys": []}', "")
    watch = await aoe.watch.run_sentinel_probe(parent.id)
    assert watch["sentinel_checks_total"] == 1
    assert watch["state"] == "watching"
    assert len(containers.launched) == launched_before

    # 2) Platform solved q1 -> q1 settled as solved without waking LLM!
    containers.probe_handler = lambda _tid, _to: (0, '{"source_keys": ["q1", "q2"], "solved_keys": ["q1"]}', "")
    watch = await aoe.watch.run_sentinel_probe(parent.id)
    q1 = next(q for q in await storage.list_questions(parent.id) if q.metadata["source_key"] == "q1")
    assert q1.metadata["status"] == "solved"
    assert q1.metadata["finish_reason"] == "platform_solved"
    assert watch["last_solved_sync_count"] == 1
    assert len(containers.launched) == launched_before

    # 3) Consecutive failures -> backoff without LLM, then circuit breaker at 3rd failure!
    containers.probe_handler = lambda _tid, _to: (1, "", "401 Unauthorized")
    for idx in range(1, MAX_CONSECUTIVE_PROBE_FAILURES):
        watch = await aoe.watch.run_sentinel_probe(parent.id)
        assert watch["consecutive_failures"] == idx
        assert watch["enabled"] is True
        assert watch["state"] == "watching"
        assert len(containers.launched) == launched_before
    watch = await aoe.watch.run_sentinel_probe(parent.id)
    assert watch["consecutive_failures"] == MAX_CONSECUTIVE_PROBE_FAILURES
    assert watch["enabled"] is False
    assert watch["state"] == "error_paused"
    assert watch["next_check_at"] is None
    assert len(containers.launched) == launched_before

    # 4) Re-enable sentinel and detect new key q3 -> immediately wakes incremental LLM Scout!
    await aoe.configure_scout_watch(parent.id, action="enable_sentinel", interval_seconds=30)
    containers.probe_handler = lambda _tid, _to: (
        0,
        '{"source_keys": ["q1", "q2", "q3"], "solved_keys": ["q1"]}',
        "",
    )
    await aoe.watch.run_sentinel_probe(parent.id)
    job = aoe._scout_jobs.get(parent.id)
    assert job is not None
    await job
    scout_launches = [spec for spec in containers.launched if spec.agent.agent_id == "scout"]
    assert len(scout_launches) == 1
    assert scout_launches[0].aoe_incremental is True
    assert scout_launches[0].aoe_detected_new_keys == ("q3",)
    await coordinator.close()


async def test_incremental_scan_updates_hints_without_conflict_and_allows_zero_new_questions(tmp_path):
    cfg, storage, containers, coordinator, parent, clock = await _setup_batch(tmp_path)
    aoe = coordinator.aoe
    await aoe.register_questions(
        parent.id, [AOEQuestionInput(source_key="q1", title="Q1", description="initial desc")]
    )
    await aoe.finish_collection(parent.id, 1)
    await aoe.watch.on_initial_collection_complete(parent.id)

    # Simulate an active incremental round where Scout pushes an updated hint for existing q1 AND 0 new questions.
    await storage.update_task(
        parent.id, metadata={"scout_collecting": True, "collection_round": 2, "collection_round_complete": False}
    )
    await storage.update_agent(parent.id, "scout", metadata={"scout_phase": "collecting"})
    outbox = cfg.task_path(parent.id, "workspaces", "scout") / "outbox"
    outbox.mkdir(parents=True, exist_ok=True)
    (outbox / "q1.json").write_text(
        json.dumps(
            {
                "source_key": "q1",
                "title": "Q1",
                "description": "initial desc",
                "collection_notes": "Official Hint: check /admin/debug",
            }
        ),
        encoding="utf-8",
    )
    receipt = await aoe.collection.import_outbox(parent.id)
    assert receipt["errors"] == []
    q1 = (await storage.list_questions(parent.id))[0]
    entries = await storage.list_entries(q1.id)
    assert any(
        e.kind == "user_prompt" and e.topic == "realtime" and "/admin/debug" in e.body.get("text", "")
        for e in entries
    )

    # Finish incremental collection with 0 new questions -> succeeds cleanly!
    fin = await aoe.finish_collection(parent.id)
    assert fin["saved"] is True
    assert fin["round_complete"] is True
    await coordinator.close()


async def test_finish_confirm_gate_standby_without_extra_llm_probe_and_wave2_auto_dismiss(tmp_path):
    cfg, storage, containers, coordinator, parent, clock = await _setup_batch(tmp_path)
    aoe = coordinator.aoe
    _write_probe_script(cfg, parent.id)
    await aoe.register_questions(
        parent.id, [AOEQuestionInput(source_key="q1", title="Q1", description="wave 1")]
    )
    await aoe.finish_collection(parent.id, 1)
    await storage.update_task(parent.id, state=RunState.RUNNING, metadata={"scout_complete": True, "aoe_phase": "solo"})
    await aoe.watch.on_initial_collection_complete(parent.id)
    q1 = (await storage.list_questions(parent.id))[0]
    await storage.update_task(q1.id, state=RunState.COMPLETED, metadata={"status": "solved", "flag": "flag{1}"})

    # All wave-1 questions done -> enters awaiting_finish_confirm, does NOT complete parent!
    done = await aoe.tick(parent.id)
    assert done is False
    snap = await aoe.snapshot(parent.id)
    assert snap["phase"] == "awaiting_finish_confirm"
    assert snap["finish_confirm"]["status"] == "pending"
    assert snap["finish_confirm"]["solved_count"] == 1
    assert (await storage.get_task(parent.id)).state == RunState.RUNNING

    # Choose continue_standby -> keeps zero-token sentinel, does NOT launch immediate LLM scan!
    launched_before = len(containers.launched)
    res = await aoe.apply_finish_decision(parent.id, decision="continue_standby", auto_standby_future=False)
    assert res["phase"] == "standby"
    assert res["scout_watch"]["mode"] == "sentinel"
    assert res["scout_watch"]["enabled"] is True
    assert len(containers.launched) == launched_before
    assert await aoe.tick(parent.id) is False
    assert (await aoe.snapshot(parent.id))["phase"] == "standby"

    # Wave 2 question arrives -> automatically dismisses standby/confirm and resumes solving!
    await storage.update_task(
        parent.id, metadata={"scout_collecting": True, "collection_round": 2, "collection_round_complete": False}
    )
    await storage.update_agent(parent.id, "scout", metadata={"scout_phase": "collecting"})
    await aoe.register_questions(
        parent.id, [AOEQuestionInput(source_key="q2", title="Q2", description="wave 2")]
    )
    await aoe.finish_collection(parent.id)
    assert await aoe.tick(parent.id) is False
    snap2 = await aoe.snapshot(parent.id)
    assert snap2["phase"] == "solo"
    assert snap2["finish_confirm"] is None

    # Finish wave 2 -> prompts again -> user chooses finish_now -> completes immediately!
    q2 = next(q for q in await storage.list_questions(parent.id) if q.metadata["source_key"] == "q2")
    await storage.update_task(q2.id, state=RunState.REPORTING, metadata={"status": "abandoned"})
    assert await aoe.tick(parent.id) is False
    assert (await aoe.snapshot(parent.id))["phase"] == "awaiting_finish_confirm"

    await aoe.apply_finish_decision(parent.id, decision="finish_now")
    assert await aoe.tick(parent.id) is True
    assert (await storage.get_task(parent.id)).state == RunState.COMPLETED
    await coordinator.close()


async def test_continue_standby_without_script_does_not_enable_llm_poll(tmp_path):
    cfg, storage, containers, coordinator, parent, clock = await _setup_batch(tmp_path)
    aoe = coordinator.aoe
    await aoe.register_questions(
        parent.id, [AOEQuestionInput(source_key="q1", title="Q1", description="wave 1")]
    )
    await aoe.finish_collection(parent.id, 1)
    await storage.update_task(parent.id, state=RunState.RUNNING, metadata={"scout_complete": True, "aoe_phase": "solo"})
    await aoe.watch.on_initial_collection_complete(parent.id)
    q1 = (await storage.list_questions(parent.id))[0]
    await storage.update_task(q1.id, state=RunState.COMPLETED, metadata={"status": "solved"})

    assert await aoe.tick(parent.id) is False
    res = await aoe.apply_finish_decision(parent.id, decision="continue_standby")
    assert res["scout_watch"]["mode"] == "manual"
    assert res["scout_watch"]["enabled"] is False
    assert res["scout_watch"]["next_check_at"] is None
    assert len(containers.launched) == 0

    # Enabling sentinel without script must fail with clear conflict.
    with pytest.raises(ConflictError):
        await aoe.configure_scout_watch(parent.id, action="enable_sentinel")
    await coordinator.close()


async def test_http_routes_for_scout_watch_wake_stop_and_finish_decision(tmp_path):
    cfg = TGA3Config(Path(__file__).parents[1] / "config")
    cfg.runtime.task_root = str(tmp_path / "tasks")
    for provider in cfg.models.providers:
        provider.api_keys[0].api_key = SecretStr("fixture-key")
    storage, containers = InMemoryStorage(), FakeContainerRuntime()
    app = create_app(cfg, storage=storage, containers=containers)
    coordinator: TaskCoordinator = app.state.services.coordinator
    parent = await storage.create_task(
        "http-aoe",
        "aoe",
        metadata={
            "aoe_attempt_seconds": 1200,
            "aoe_phase": "collecting",
            "aoe_scout_mode": "collection_only",
            "aoe_finish_confirm_enabled": True,
        },
    )
    cfg.ensure_task_directories(parent.id)
    for agent_id, definition in cfg.agents.agents.items():
        await storage.upsert_agent(
            AgentRun(
                task_id=parent.id,
                agent_id=agent_id,
                sdk=definition.runtime,
                provider_id=definition.provider_id,
                model_id=definition.model_id,
                protocol=definition.protocol,
                metadata={"scout_phase": "collecting"} if agent_id == "scout" else {},
            )
        )
    await storage.update_task(parent.id, state=RunState.SCOUTING)
    _write_probe_script(cfg, parent.id)
    await coordinator.aoe.register_questions(
        parent.id, [AOEQuestionInput(source_key="q1", title="Q1", description="q1")]
    )
    await coordinator.aoe.finish_collection(parent.id, 1)
    await storage.update_task(parent.id, state=RunState.RUNNING, metadata={"scout_complete": True, "aoe_phase": "solo"})
    await coordinator.aoe.watch.on_initial_collection_complete(parent.id)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Pause watch via stop
        resp = await client.post(f"/api/v3/tasks/{parent.id}/scout/stop")
        assert resp.status_code == 200
        assert resp.json()["scout_watch"]["enabled"] is False

        # Re-enable sentinel
        resp = await client.post(
            f"/api/v3/tasks/{parent.id}/scout/watch",
            json={"action": "enable_sentinel", "interval_seconds": 60},
        )
        assert resp.status_code == 200
        assert resp.json()["scout_watch"]["mode"] == "sentinel"
        assert resp.json()["scout_watch"]["probe_interval_seconds"] == 60

        # Manual wake
        resp = await client.post(f"/api/v3/tasks/{parent.id}/scout/wake")
        assert resp.status_code == 200
        assert resp.json()["started"] is True
        if job := coordinator.aoe._scout_jobs.get(parent.id):
            await job

        # Solve q1 -> awaiting_finish_confirm -> continue_standby via HTTP
        q1 = (await storage.list_questions(parent.id))[0]
        await storage.update_task(q1.id, state=RunState.COMPLETED, metadata={"status": "solved"})
        await coordinator.aoe.tick(parent.id)
        snap = (await client.get(f"/api/v3/tasks/{parent.id}/questions")).json()
        assert snap["phase"] == "awaiting_finish_confirm"
        assert snap["finish_confirm"]["status"] == "pending"

        resp = await client.post(
            f"/api/v3/tasks/{parent.id}/aoe/finish-decision",
            json={"decision": "continue_standby", "auto_standby_future": True},
        )
        assert resp.status_code == 200
        assert resp.json()["phase"] == "standby"
        assert resp.json()["scout_watch"]["mode"] == "sentinel"
    await coordinator.close()
