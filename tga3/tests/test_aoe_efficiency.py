"""Scheduling work scales with changes; deadlines and recovery remain authoritative."""
import asyncio
import json
import time
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from test_aoe_runtime import final, prepare, setup
from test_target_instances import contract

from tga3.aoe_models import AOEQuestionInput
from tga3.domain import InputFile, RunState
from tga3.target_instances import TargetInstances, TargetSpec


def count(monkeypatch, owner, name, *, sync=False):
    spy = (Mock if sync else AsyncMock)(wraps=getattr(owner, name))
    monkeypatch.setattr(owner, name, spy)
    return spy


@pytest.mark.parametrize("total", [40, 63])
async def test_stable_tick_reads_questions_once_and_does_no_view_work_or_updates(tmp_path, monkeypatch, total):
    cfg, storage, containers, c, parent, _ = await setup(tmp_path)
    await c.aoe.register_questions(parent.id, [AOEQuestionInput(
        source_key=str(i), title=str(i), description="fixture", difficulty="easy",
        target={"mode": "none"}) for i in range(total)])
    await c.aoe.finish_collection(parent.id, total)
    await c.aoe.tick(parent.id)
    await c.aoe.tick(parent.id)
    reads = count(monkeypatch, storage, "list_questions")
    inputs = count(monkeypatch, storage, "list_input_files")
    tasks = count(monkeypatch, storage, "update_task")
    agents = count(monkeypatch, storage, "update_agent")
    views = count(monkeypatch, c.aoe, "question_view")
    writes = count(monkeypatch, c.aoe, "_atomic_json", sync=True)
    assert not await c.aoe.tick(parent.id)
    assert reads.call_count == 1
    assert inputs.call_count == tasks.call_count == agents.call_count == 0
    assert views.call_count == writes.call_count == 0
    assert len(containers.launched) == 2
    # The periodic safety check uses one batch input query, still no per-question work.
    batch = count(monkeypatch, storage, "list_question_input_files")
    await c.aoe.tick(parent.id, reconcile=True)
    assert batch.call_count == 1 and reads.call_count == 2
    assert inputs.call_count == tasks.call_count == agents.call_count == 0
    assert views.call_count == writes.call_count == 0
    await c.close()


async def test_only_changed_question_is_built_and_cached_attachment_reads_are_reused(tmp_path, monkeypatch):
    cfg, storage, _, c, parent, _, questions = await prepare(tmp_path)
    await c.aoe.tick(parent.id)
    q = questions["Easy"]
    views = count(monkeypatch, c.aoe, "question_view")
    inputs = count(monkeypatch, storage, "list_input_files")
    await storage.update_task(q.id, metadata={"connection_info": "updated fixture endpoint"})
    await c.aoe.tick(parent.id)
    assert [call.args[0].id for call in views.call_args_list] == [q.id]
    assert inputs.call_count == 0
    path = cfg.task_path(q.id, "inputs") / "new.txt"
    path.write_text("fixture")
    item = InputFile(task_id=q.id, name=path.name, storage_path=str(path),
                     media_type="text/plain", sha256="0" * 64, size_bytes=7)
    await storage.register_input_file(item)
    await c.aoe.tick(parent.id)
    assert inputs.call_count == 1 and views.call_count == 2
    saved = json.loads((cfg.task_path(q.id) / "question.json").read_text())
    assert saved["attachments"][0]["path"] == "inputs/new.txt"
    assert saved["connection_info"] == "updated fixture endpoint"
    await c.close()


async def test_periodic_reconciliation_recovers_a_missed_input_notification(tmp_path, monkeypatch):
    cfg, storage, _, c, parent, _, questions = await prepare(tmp_path)
    await c.aoe.tick(parent.id)
    q = questions["Easy"]
    path = cfg.task_path(q.id, "inputs") / "missed.txt"
    path.write_text("fixture")
    item = InputFile(task_id=q.id, name=path.name, storage_path=str(path),
                     media_type="text/plain", sha256="0" * 64, size_bytes=7)
    storage.input_files[item.id] = item  # Simulate a write whose notification was missed.
    views = count(monkeypatch, c.aoe, "question_view")
    await c.aoe.tick(parent.id, reconcile=True)
    assert views.call_count == 1 and views.call_args.args[0].id == q.id
    saved = json.loads((cfg.task_path(q.id) / "question.json").read_text())
    assert saved["attachments"][0]["path"] == "inputs/missed.txt"
    await c.close()


async def test_unchanged_collection_is_not_rewritten_and_missing_manifests_recover(tmp_path, monkeypatch):
    cfg, _, _, c, parent, _, questions = await prepare(tmp_path)
    await c.aoe.tick(parent.id)
    writes = count(monkeypatch, c.aoe, "_atomic_json", sync=True)
    await c.aoe.finish_collection(parent.id, 2)
    assert writes.call_count == 0
    batch = cfg.task_path(parent.id) / "questions.json"
    single = cfg.task_path(questions["Easy"].id) / "question.json"
    batch.unlink()
    single.unlink()
    await c.aoe.tick(parent.id, reconcile=True)
    assert writes.call_count == 2
    assert json.loads(batch.read_text())["collection_complete"] is True
    assert json.loads(single.read_text())["question_id"] == str(questions["Easy"].id)
    await c.close()


async def wait_until(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(0.005)


async def run_prepared(c, storage, parent, monkeypatch, *, reconcile_seconds=60):
    c.aoe.clock = time.time
    c.aoe.reconcile_seconds = reconcile_seconds
    await storage.update_task(parent.id, metadata={"scout_complete": True})
    ticks = count(monkeypatch, c.aoe, "tick")
    job = asyncio.create_task(c.aoe.run(parent.id, {}))
    await wait_until(lambda: ticks.call_count >= 2)
    await asyncio.sleep(0.02)
    return job, ticks


async def test_event_wakeup_claims_new_question_and_final_candidate_without_polling(tmp_path, monkeypatch):
    cfg, storage, containers, c, parent, _ = await setup(tmp_path)
    await c.aoe.register_questions(parent.id, [AOEQuestionInput(
        source_key="first", title="first", description="fixture", difficulty="easy")])
    job, _ = await run_prepared(c, storage, parent, monkeypatch)
    try:
        before = c.aoe.tick.call_count
        await asyncio.sleep(0.06)
        assert c.aoe.tick.call_count == before  # Sleeping, rather than a busy event loop.
        # Scout is still collecting; registration should wake an idle Worker immediately.
        await c.aoe.register_questions(parent.id, [AOEQuestionInput(
            source_key="second", title="second", description="fixture", difficulty="easy")])
        await wait_until(lambda: len(containers.launched) == 2)
        first = containers.launched[0]
        await final(storage, first.task_id)
        await wait_until(lambda: storage.tasks[first.task_id].metadata["status"] == "solved")
        assert c.aoe.tick.call_count > before
    finally:
        job.cancel()
        await asyncio.gather(job, return_exceptions=True)
        await c.close()


async def test_deadline_timer_wakes_before_reconciliation(tmp_path, monkeypatch):
    _, storage, containers, c, parent, _, _ = await prepare(tmp_path)
    job, _ = await run_prepared(c, storage, parent, monkeypatch)
    try:
        launch = containers.launched[0]
        await c.aoe.action_started(launch.task_id, launch.agent.agent_id, launch.attempt_id)
        deadline = time.time() + 0.1
        q = storage.tasks[launch.task_id]
        await storage.update_task(q.id, metadata={"attempts": [
            {**a, "deadline": deadline} if a["id"] == launch.attempt_id else a
            for a in q.metadata["attempts"]]})
        await wait_until(lambda: any(a["id"] == launch.attempt_id and a["outcome"] == "timeout"
                                    for a in storage.tasks[q.id].metadata["attempts"]))
        assert time.time() >= deadline
    finally:
        job.cancel()
        await asyncio.gather(job, return_exceptions=True)
        await c.close()


async def test_report_completion_wakes_scheduler_and_does_not_churn_reporting_state(tmp_path, monkeypatch):
    _, storage, _, c, parent, _, questions = await prepare(tmp_path)
    for q in questions.values():
        await storage.update_task(q.id, state=RunState.REPORTING, metadata={"status": "solved"})
    job, _ = await run_prepared(c, storage, parent, monkeypatch)
    try:
        writes = count(monkeypatch, storage, "update_task")
        agents = count(monkeypatch, storage, "update_agent")
        await c.aoe.tick(parent.id)
        assert writes.call_count == agents.call_count == 0
        for q in questions.values():
            await storage.update_task(q.id, state=RunState.COMPLETED)
        await asyncio.wait_for(job, timeout=2)
        assert storage.tasks[parent.id].state == RunState.COMPLETED
        assert not c.aoe._wakeups and not c.aoe._input_files and not c.aoe._manifest_sources
    finally:
        job.cancel()
        await asyncio.gather(job, return_exceptions=True)
        await c.close()


async def test_managed_contract_survives_view_updates_and_restart(tmp_path):
    cfg, storage, _, c, parent, _ = await setup(tmp_path)
    await c.aoe.register_questions(parent.id, [AOEQuestionInput(
        source_key="managed", title="managed", description="fixture", target=contract())])
    q = (await storage.list_questions(parent.id))[0]
    await storage.update_task(q.id, metadata={"connection_info": "changed"})
    await c.aoe._save_question(await storage.get_task(q.id))
    fresh = TargetInstances(cfg)
    assert "target" not in storage.tasks[q.id].metadata
    assert fresh.spec(await storage.get_task(q.id)) == TargetSpec.model_validate(contract())
    manifest = json.loads((cfg.task_path(parent.id) / "questions.json").read_text())
    assert not manifest["collection_complete"] and len(manifest["questions"]) == 1
    await c.close()


async def test_target_ready_wakes_waiting_worker_before_periodic_check(tmp_path, monkeypatch):
    _, storage, containers, c, parent, _ = await setup(tmp_path)
    started, release = asyncio.Event(), asyncio.Event()

    async def handler(request):
        started.set()
        await release.wait()
        return httpx.Response(200, json={"container_addr": ["127.0.0.1:1234"]})

    c.aoe.targets.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    await c.aoe.register_questions(parent.id, [AOEQuestionInput(
        source_key="managed", title="managed", description="fixture", difficulty="easy", target=contract())])
    job, _ = await run_prepared(c, storage, parent, monkeypatch)
    try:
        assert started.is_set() and not containers.launched
        release.set()
        await wait_until(lambda: len(containers.launched) == 1)
        assert containers.launched[0].target_addresses == ("127.0.0.1:1234",)
    finally:
        release.set()
        job.cancel()
        await asyncio.gather(job, return_exceptions=True)
        await c.close()


async def test_resume_wakes_idle_worker_and_accounting_does_not_wake_scheduler(tmp_path, monkeypatch):
    _, storage, containers, c, parent, _, _ = await prepare(tmp_path)
    for agent in c.config.worker_agent_ids:
        await storage.update_agent(parent.id, agent, metadata={"aoe_paused": True})
    job, ticks = await run_prepared(c, storage, parent, monkeypatch)
    try:
        assert not containers.launched
        agent = c.config.worker_agent_ids[0]
        await storage.update_agent(parent.id, agent, metadata={"aoe_paused": False})
        await wait_until(lambda: len(containers.launched) == 1)
        await asyncio.sleep(0.04)
        before = ticks.call_count
        await storage.update_agent(parent.id, agent, metadata={"token_usage": {"total": 100}})
        await asyncio.sleep(0.04)
        assert ticks.call_count == before
    finally:
        job.cancel()
        await asyncio.gather(job, return_exceptions=True)
        await c.close()


async def test_run_reconciles_missed_state_notification(tmp_path, monkeypatch):
    cfg, storage, containers, c, parent, _, _ = await prepare(tmp_path)
    job, _ = await run_prepared(c, storage, parent, monkeypatch, reconcile_seconds=0.08)
    try:
        qid = containers.launched[0].task_id
        q = storage.tasks[qid]
        storage.tasks[qid] = q.model_copy(update={"metadata": {**q.metadata, "flag": "missed-update"}})
        path = cfg.task_path(qid) / "question.json"
        await wait_until(lambda: json.loads(path.read_text())["flag"] == "missed-update")
    finally:
        job.cancel()
        await asyncio.gather(job, return_exceptions=True)
        await c.close()


async def test_scout_failure_wakes_scheduler_while_it_is_waiting(tmp_path, monkeypatch):
    _, storage, _, c, parent, _ = await setup(tmp_path)
    release = asyncio.Event()

    async def scout(*args):
        await release.wait()
        raise RuntimeError("fixture Scout error")

    monkeypatch.setattr(c.aoe, "_scout", scout)
    c.aoe.reconcile_seconds = 60
    tick = count(monkeypatch, c.aoe, "tick")
    job = asyncio.create_task(c.aoe.run(parent.id, {}))
    try:
        await wait_until(lambda: tick.call_count >= 2)
        release.set()
        await asyncio.wait_for(job, timeout=2)
        assert storage.tasks[parent.id].state == RunState.FAILED
    finally:
        job.cancel()
        await asyncio.gather(job, return_exceptions=True)
        await c.close()
