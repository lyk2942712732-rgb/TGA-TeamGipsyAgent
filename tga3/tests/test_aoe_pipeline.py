import asyncio
import json
from uuid import uuid4

import pytest
from test_aoe_runtime import setup

from tga3.aoe_models import AOEQuestionInput, active_attempts, joint_ready, next_solo_question
from tga3.docker_runtime import FakeContainerRuntime
from tga3.domain import RunState
from tga3.scout_intel import ScoutIntelMergeInput
from tga3.workers.action_journal import ActionJournal


async def registered(tmp_path):
    cfg, storage, runtime, coordinator, parent, clock = await setup(tmp_path)
    await coordinator.aoe.register_questions(
        parent.id,
        [
            AOEQuestionInput(source_key="hard", title="Hard", description="hard", difficulty="hard"),
            AOEQuestionInput(source_key="easy", title="Easy", description="easy", difficulty="easy"),
        ],
    )
    await coordinator.aoe.finish_collection(parent.id, 2)
    coordinator.aoe.automation = None
    return cfg, storage, runtime, coordinator, parent, clock


async def test_first_question_is_claimed_while_scout_prepares_next_without_duplicate_ownership(tmp_path):
    cfg, storage, _, coordinator, parent, clock = await registered(tmp_path)
    hard_started, release_hard, worker_started = asyncio.Event(), asyncio.Event(), asyncio.Event()
    phases, budgets = [], []
    actions = {}

    class PipelineRuntime(FakeContainerRuntime):
        async def launch(self, spec):
            result = await super().launch(spec)
            if spec.agent.role == "worker":
                worker_started.set()
            return result

        async def scout(self, spec, emit):
            ident = spec.scout_question_id
            question = await storage.get_task(ident)
            phases.append((question.title, spec.scout_phase))
            if spec.scout_phase == "executing":
                budgets.append(spec.phase_budget_seconds)
                if question.title == "Hard":
                    hard_started.set()
                    await release_hard.wait()
                workspace = cfg.task_path(ident, "workspaces", "scout")
                journal = ActionJournal(
                    cfg.task_path(ident, "logs", "scout"),
                    identity={"task_id": str(ident), "question_id": str(ident), "agent_id": "scout"},
                    output_directory=workspace,
                )
                actions[ident] = journal.record("probe", "shell", result=question.title, status="completed")[
                    "action_id"
                ]
                if question.title == "Easy":
                    clock[0] += cfg.runtime.scout_timeout_seconds
                    raise TimeoutError  # Using one full budget must not consume the next question's budget.
            elif spec.scout_phase == "extracting":
                await coordinator.scout_intel.write_action(ident, actions[ident], question.title, "observed", "fixture")
            elif spec.scout_phase == "merging":
                raw = coordinator.scout_intel.documents(ident)
                await coordinator.scout_intel.merge(ScoutIntelMergeInput(task_id=ident, merged_from_ids=[raw[0].id]))

    runtime = PipelineRuntime()
    coordinator.containers = coordinator.aoe.containers = runtime
    job = asyncio.create_task(coordinator.aoe.run(parent.id, {"scout": cfg.resolve_agent("scout")}))
    try:
        async with asyncio.timeout(3):
            await hard_started.wait()
            await worker_started.wait()
        questions = {q.title: q for q in await storage.list_questions(parent.id)}
        easy, hard = questions["Easy"], questions["Hard"]
        assert easy.metadata["scout_ready"] and not hard.metadata["scout_ready"]
        assert [e.kind for e in await storage.list_entries(easy.id)].count("intel") == 1
        assert not [e for e in await storage.list_entries(hard.id) if e.kind == "intel"]
        await asyncio.gather(*(coordinator.aoe.tick(parent.id) for _ in range(12)))
        assert len(runtime.launched) == 1
        assert runtime.launched[0].task_id == easy.id
        assert len(active_attempts(await storage.get_task(easy.id))) == 1
        assert not (await storage.get_task(parent.id)).metadata["scout_complete"]
        assert budgets == [300, 300]
        assert phases[:4] == [("Easy", "executing"), ("Easy", "extracting"), ("Easy", "merging"), ("Hard", "executing")]
        release_hard.set()
        async with asyncio.timeout(3):
            while len(runtime.launched) < 2:
                await asyncio.sleep(0.01)
        assert {s.task_id for s in runtime.launched} == {easy.id, hard.id}
        assert (await storage.get_task(parent.id)).metadata["scout_complete"]
    finally:
        release_hard.set()
        job.cancel()
        await asyncio.gather(job, return_exceptions=True)


async def test_pending_questions_block_joint_and_cannot_be_claimed_even_if_another_question_is_exhausted(tmp_path):
    _, storage, _, coordinator, parent, _ = await registered(tmp_path)
    easy, hard = sorted(await storage.list_questions(parent.id), key=lambda q: q.title)
    workers = coordinator.config.worker_agent_ids
    easy = await storage.update_task(
        easy.id,
        state=RunState.RUNNING,
        metadata={
            "scout_ready": True,
            "status": "queued",
            "attempts": [{"agent_id": a, "phase": "solo", "outcome": "timeout"} for a in workers],
        },
    )
    assert all(next_solo_question([easy, hard], a) is None for a in workers)
    assert not joint_ready([easy, hard], workers)
    await storage.update_task(parent.id, state=RunState.RUNNING, metadata={"aoe_phase": "solo"})
    await coordinator.aoe.tick(parent.id)
    assert (await storage.get_task(parent.id)).metadata["aoe_phase"] == "solo"
    from tga3.errors import ConflictError

    with pytest.raises(ConflictError, match="handoff"):
        await coordinator.aoe._new_attempts(parent.id, hard, (workers[0],), "solo")


async def test_scout_runner_gives_each_question_full_budget_in_one_adapter(monkeypatch, tmp_path):
    from tga3.workers import aoe_scout

    batch, first, second = uuid4(), uuid4(), uuid4()
    root, workspace = tmp_path / "batch", tmp_path / "batch/workspaces/scout"
    workspace.mkdir(parents=True)
    (root / "inputs").mkdir()
    (root / "inputs/questions.json").write_text(
        json.dumps(
            {
                "collection_complete": True,
                "questions": [{"question_id": str(ident), "title": str(ident)} for ident in (first, second)],
            }
        )
    )
    for key, value in {
        "TGA3_AOE_PARENT_ID": str(batch),
        "TGA3_TASK_ID": str(batch),
        "TGA3_AOE_ROOT": str(root),
        "TGA3_WORKSPACE": str(workspace),
        "TGA3_SYSTEM_PROMPT": "fixture",
    }.items():
        monkeypatch.setenv(key, value)
    for key in (
        "TGA3_CLI_HOME",
        "TGA3_SCOUT_PHASE",
        "TGA3_PHASE_BUDGET",
        "TGA3_QUESTION_ID",
        "TGA3_INPUTS",
        "TGA3_LOGS",
        "TGA3_INTELS",
    ):
        monkeypatch.setenv(key, "")  # Restore every environment variable mutated by the real runner.
    instances, budgets = [], []

    class Adapter:
        runtime, api_key = "codex_cli", "fixture-key"

        def __init__(self):
            instances.append(self)
            self.journal = ActionJournal(root / "logs/scout", identity={"agent_id": "scout"})

        async def run_cycle(self, prompt, emit):
            import os

            budgets.append((os.environ["TGA3_QUESTION_ID"], float(os.environ["TGA3_PHASE_BUDGET"])))
            await asyncio.sleep(10)

    monkeypatch.setattr(aoe_scout, "CodexAdapter", Adapter)
    runner = asyncio.create_task(aoe_scout.scout())
    try:
        for ident in (first, second):
            request_id = uuid4().hex
            state = workspace / ".tga3"
            state.mkdir(exist_ok=True)
            (state / "aoe-scout-request.json").write_text(
                json.dumps({"id": request_id, "phase": "executing", "question_id": str(ident), "budget_seconds": 0.05})
            )
            result = state / "aoe-scout-results" / (request_id + ".json")
            async with asyncio.timeout(2):
                while not result.exists():
                    await asyncio.sleep(0.01)
            assert json.loads(result.read_text())["finish_reason"] == "timeout"
        assert len(instances) == 1 and budgets == [(str(first), 0.05), (str(second), 0.05)]
        assert instances[0].timeout == 5.05
    finally:
        runner.cancel()
        await asyncio.gather(runner, return_exceptions=True)


@pytest.mark.parametrize("remaining", [0, 40])
async def test_resume_retains_current_question_deadline_and_does_not_replay_prepared_questions(tmp_path, remaining):
    cfg, storage, _, coordinator, parent, clock = await registered(tmp_path)
    easy, hard = sorted(await storage.list_questions(parent.id), key=lambda q: q.title)
    await storage.update_task(easy.id, metadata={"scout_execution_deadline": clock[0] + remaining})
    requests = []

    class ResumeRuntime(FakeContainerRuntime):
        async def scout(self, spec, emit):
            requests.append((spec.scout_question_id, spec.scout_phase, spec.phase_budget_seconds))

    runtime = ResumeRuntime()
    coordinator.aoe.containers = runtime
    await coordinator.aoe._scout(parent.id, {"scout": cfg.resolve_agent("scout")})
    executions = [(ident, budget) for ident, phase, budget in requests if phase == "executing"]
    assert executions == ([(easy.id, 40)] if remaining else []) + [(hard.id, 300)]
    assert all(budget is None for _, phase, budget in requests if phase in {"extracting", "merging"})
    assert (await storage.get_task(easy.id)).metadata["scout_execution_deadline"] == clock[0] + remaining
    requests.clear()
    await coordinator.aoe._scout(parent.id, {"scout": cfg.resolve_agent("scout")})
    assert not requests  # Completed question phases and boards are not re-opened.


def test_interrupted_scout_action_can_be_extracted_after_process_restart_without_new_action(tmp_path):
    from tga3.action_logs import read_action_log
    from tga3.workers.aoe_scout import recover_unfinished

    journal = ActionJournal(tmp_path / "logs", identity={"agent_id": "scout"}, output_directory=tmp_path / "workspace")
    original = journal.record("native-probe", "shell", inputs={"command": "unfinished probe"})
    resumed = ActionJournal(
        journal.directory, identity=journal.identity, output_directory=journal.output_directory, enabled=False
    )
    recover_unfinished(resumed)
    actions = read_action_log(journal.directory)["actions"]
    assert len(actions) == 1 and actions[0]["action_id"] == original["action_id"]
    assert actions[0]["status"] == "interrupted" and not resumed.enabled
    body = json.loads((journal.output_directory / actions[0]["artifact"]["output_path"]).read_text())
    assert not body["result_received"]
