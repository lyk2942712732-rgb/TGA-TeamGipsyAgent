import asyncio
import json
from uuid import uuid4

import pytest
from test_aoe_runtime import final, setup

from tga3.aoe_models import AOEQuestionInput, active_attempts
from tga3.docker_runtime import FakeContainerRuntime
from tga3.domain import RunState


async def test_workers_claim_during_collection_scout_has_no_later_rounds(tmp_path):
    cfg, storage, _, coordinator, parent, _ = await setup(tmp_path)
    registered, worker_started, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    phases = []

    class Runtime(FakeContainerRuntime):
        async def launch(self, spec):
            result = await super().launch(spec)
            worker_started.set()
            return result

        async def scout(self, spec, emit):
            phases.append(spec.scout_phase)
            assert spec.scout_phase == "collecting" and spec.scout_question_id is None
            await coordinator.aoe.register_questions(parent.id, [AOEQuestionInput(
                source_key="easy", title="Easy", description="first", difficulty="easy")])
            registered.set()
            await release.wait()
            await coordinator.aoe.register_questions(parent.id, [AOEQuestionInput(
                source_key="hard", title="Hard", description="second", difficulty="hard")])
            await coordinator.aoe.finish_collection(parent.id, 2)

    runtime = Runtime()
    coordinator.containers = coordinator.aoe.containers = runtime
    job = asyncio.create_task(coordinator.aoe.run(parent.id, {"scout": cfg.resolve_agent("scout")}))
    try:
        async with asyncio.timeout(3):
            await registered.wait()
            await worker_started.wait()
        assert not (await storage.get_task(parent.id)).metadata.get("collection_complete")
        q = (await storage.list_questions(parent.id))[0]
        assert runtime.launched[0].task_id == q.id
        assert [e.kind for e in await storage.list_entries(q.id)] == ["user_prompt", "user_prompt"]
        await asyncio.gather(*(coordinator.aoe.tick(parent.id) for _ in range(10)))
        assert len(active_attempts(await storage.get_task(q.id))) == 1
        assert len(runtime.launched) == 1
        release.set()
        async with asyncio.timeout(3):
            while not (await storage.get_task(parent.id)).metadata.get("scout_complete"):
                await asyncio.sleep(0.01)
        await coordinator.aoe.tick(parent.id)
        assert phases == ["collecting"] and len(runtime.launched) == 2
        assert (parent.id, "scout") in runtime.stopped
        assert not list(cfg.task_path(q.id, "intels").rglob("*.json"))
    finally:
        release.set(); job.cancel()
        await asyncio.gather(job, return_exceptions=True)


async def test_collection_gate_ends_at_finish_not_when_easy_solved(tmp_path):
    _, storage, runtime, c, parent, _ = await setup(tmp_path)
    await c.aoe.register_questions(parent.id, [AOEQuestionInput(
        source_key="hard", title="Hard", description="h", difficulty="hard")])
    await c.aoe.tick(parent.id)
    assert not runtime.launched
    await c.aoe.register_questions(parent.id, [AOEQuestionInput(
        source_key="easy", title="Easy", description="e", difficulty="easy")])
    await c.aoe.tick(parent.id)
    assert len(runtime.launched) == 1
    easy = next(q for q in await storage.list_questions(parent.id) if q.title == "Easy")
    assert runtime.launched[0].task_id == easy.id
    await c.aoe.finish_collection(parent.id, 2)
    await c.aoe.tick(parent.id)
    assert len(runtime.launched) == 2
    assert (await storage.get_task(runtime.launched[1].task_id)).title == "Hard"
    assert active_attempts(await storage.get_task(easy.id))


async def test_unrated_stream_and_retry_do_not_complete_batch_early(tmp_path):
    _, storage, runtime, c, parent, clock = await setup(tmp_path)
    specs = [AOEQuestionInput(source_key=str(i), title=str(i), description="unrated") for i in range(2)]
    await c.aoe.register_questions(parent.id, specs)
    await c.aoe.tick(parent.id)
    assert len(runtime.launched) == 2 and len({s.task_id for s in runtime.launched}) == 2
    first = await storage.get_task(runtime.launched[0].task_id)
    stamp = first.metadata["registered_at"]
    clock[0] += 10
    await c.aoe.register_questions(parent.id, specs)
    assert (await storage.get_task(first.id)).metadata["registered_at"] == stamp
    for spec in runtime.launched:
        await final(storage, spec.task_id)
    await c.aoe.tick(parent.id)
    for spec in runtime.launched:
        await storage.update_task(spec.task_id, state=RunState.COMPLETED)
    assert not await c.aoe.tick(parent.id)
    assert (await storage.get_task(parent.id)).state != RunState.COMPLETED
    await c.aoe.register_questions(parent.id, [AOEQuestionInput(source_key="later", title="Later", description="new")])
    await c.aoe.tick(parent.id)
    assert len(runtime.launched) == 3


async def test_overview_solved_first_newest_solved_then_newest_registered(tmp_path):
    _, storage, _, c, parent, clock = await setup(tmp_path)
    ids = []
    for i in range(4):
        clock[0] += 10
        await c.aoe.register_questions(parent.id, [AOEQuestionInput(source_key=str(i), title=str(i), description="q")])
        ids.append(next(q.id for q in await storage.list_questions(parent.id) if q.title == str(i)))
    await storage.update_task(ids[0], metadata={"status": "solved", "solved_at": 200})
    await storage.update_task(ids[1], metadata={"status": "solved", "solved_at": 210})
    await storage.update_task(ids[0], state=RunState.COMPLETED)
    view = await c.aoe.snapshot(parent.id)
    assert [q["title"] for q in view["questions"]] == ["1", "0", "3", "2"]


async def test_collection_runner_uses_only_one_collecting_session(monkeypatch, tmp_path):
    from tga3.workers import aoe_scout
    from tga3.workers.action_journal import ActionJournal

    workspace, batch = tmp_path / "workspace", tmp_path / "batch"
    workspace.mkdir(); (batch / "inputs").mkdir(parents=True)
    for key, value in {"TGA3_AOE_PARENT_ID": str(uuid4()), "TGA3_WORKSPACE": str(workspace),
                       "TGA3_AOE_ROOT": str(batch), "TGA3_SYSTEM_PROMPT": "collect", "TGA3_CLI_HOME": "",
                       "TGA3_SCOUT_PHASE": "collecting"}.items():
        monkeypatch.setenv(key, value)
    instances = []

    class Adapter:
        api_key = "secret"

        def __init__(self):
            instances.append(self)
            self.journal = ActionJournal(tmp_path / "logs", identity={"agent_id": "scout"})

        async def run_cycle(self, prompt, emit):
            assert "NO later Scout rounds" in prompt and self.timeout is None
            (batch / "inputs/questions.json").write_text(json.dumps({"collection_complete": True, "questions": []}))
            return "collected"

    monkeypatch.setattr(aoe_scout, "CodexAdapter", Adapter)
    state = workspace / ".tga3"; state.mkdir()
    ident = uuid4().hex
    (state / "aoe-scout-request.json").write_text(json.dumps({"id": ident, "phase": "collecting"}))
    job = asyncio.create_task(aoe_scout.scout())
    try:
        result = state / "aoe-scout-results" / (ident + ".json")
        async with asyncio.timeout(2):
            while not result.exists():
                await asyncio.sleep(0.01)
        assert json.loads(result.read_text())["finish_reason"] == "completed" and len(instances) == 1
    finally:
        job.cancel(); await asyncio.gather(job, return_exceptions=True)
