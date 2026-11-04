import asyncio
import json
from pathlib import Path

import pytest
from pydantic import SecretStr

from tga3.action_logs import read_action_log
from tga3.aoe_models import AOEQuestionInput, active_attempts
from tga3.config import TGA3Config
from tga3.coordinator import TaskCoordinator
from tga3.docker_runtime import FakeContainerRuntime
from tga3.domain import SYSTEM_ACTOR, AgentRun, Artifact, ArtifactRef, PublishRequest, RunState
from tga3.errors import ConflictError, ContractError
from tga3.scout_intel import ScoutIntelMergeInput
from tga3.storage import InMemoryStorage
from tga3.workers.action_journal import ActionJournal


class ReportQueue:
    def __init__(self):
        self.questions = []

    def start_report(self, task_id):
        if task_id not in self.questions:
            self.questions.append(task_id)


async def setup(tmp_path):
    config = TGA3Config(Path(__file__).parents[1] / "config")
    config.runtime.task_root = str(tmp_path / "tasks")
    for provider in config.models.providers:
        provider.api_keys[0].api_key = SecretStr("fixture-key")
    storage, containers = InMemoryStorage(), FakeContainerRuntime()
    coordinator = TaskCoordinator(config, storage, containers)
    parent = await storage.create_task(
        "batch", "aoe", metadata={"aoe_attempt_seconds": 1200, "aoe_phase": "collecting"}
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
    coordinator.aoe.automation = ReportQueue()
    clock = [100.0]
    coordinator.aoe.clock = lambda: clock[0]
    return config, storage, containers, coordinator, parent, clock


async def prepare(tmp_path):
    cfg, storage, containers, coordinator, parent, clock = await setup(tmp_path)
    await coordinator.aoe.register_questions(
        parent.id,
        [
            AOEQuestionInput(source_key="hard", title="Hard", description="hard question", difficulty="hard"),
            AOEQuestionInput(source_key="easy", title="Easy", description="easy question", difficulty="easy"),
        ],
    )
    await coordinator.aoe.finish_collection(parent.id, 2)
    await storage.update_task(parent.id, state=RunState.RUNNING, metadata={"aoe_phase": "solo"})
    questions = {q.title: q for q in await storage.list_questions(parent.id)}
    for q in questions.values():
        await storage.update_task(q.id, state=RunState.RUNNING)
    return cfg, storage, containers, coordinator, parent, clock, questions


async def final(storage, question_id):
    artifact = await storage.register_artifact(
        Artifact(
            task_id=question_id,
            created_by=SYSTEM_ACTOR,
            name="proof",
            storage_path="fixture",
            media_type="text/plain",
            sha256="0" * 64,
            size_bytes=0,
        )
    )
    finding = await storage.publish(
        question_id,
        SYSTEM_ACTOR,
        PublishRequest(
            kind="finding",
            body={"claim": "observed verified flag"},
            artifact_refs=[ArtifactRef(artifact_id=artifact.id, locator="stdout")],
            idempotency_key="finding",
        ),
    )
    return await storage.publish(
        question_id,
        SYSTEM_ACTOR,
        PublishRequest(
            kind="final_candidate",
            body={"conclusion": "flag{verified}", "rationale": "observed", "finding_ids": [str(finding.id)]},
            idempotency_key="final",
        ),
    )


async def test_two_slots_cross_attempt_then_shared_joint_deadline_and_abandonment(tmp_path):
    cfg, storage, containers, coordinator, parent, clock, questions = await prepare(tmp_path)
    aoe = coordinator.aoe
    await asyncio.gather(aoe.tick(parent.id), aoe.tick(parent.id))
    assert len(containers.launched) == 2  # Concurrent ticks cannot double-lease.
    first, second = containers.launched
    assert first.task_id == questions["Easy"].id and first.agent.agent_id == "worker-openai"
    assert second.task_id == questions["Hard"].id and second.agent.agent_id == "worker-claude"
    assert first.fresh_session and second.fresh_session
    assert first.attempt_deadline == second.attempt_deadline == 1300
    assert first.task_id != second.task_id
    clock[0] = 200
    await final(storage, first.task_id)
    await aoe.tick(parent.id)
    assert (await storage.get_task(first.task_id)).metadata["flag"] == "flag{verified}"
    assert active_attempts(await storage.get_task(second.task_id))
    assert (second.task_id, second.agent.agent_id) not in containers.stopped
    assert len(containers.launched) == 2  # OpenAI waits; it must not preempt the peer's active question.
    await storage.update_task(first.task_id, state=RunState.COMPLETED)  # Its independent report finished.
    clock[0] = 1300
    await aoe.tick(parent.id)
    crossed = containers.launched[-1]
    assert crossed.task_id == second.task_id and crossed.agent.agent_id == "worker-openai"
    assert crossed.fresh_session and crossed.attempt_deadline == 2500
    assert (await storage.get_agent(parent.id, "worker-claude")).metadata["aoe_waiting"]
    clock[0] = 2500
    await aoe.tick(parent.id)
    joint = containers.launched[-2:]
    assert {s.agent.agent_id for s in joint} == {"worker-openai", "worker-claude"}
    assert {s.task_id for s in joint} == {second.task_id}
    assert all(not s.fresh_session and s.attempt_deadline == 3700 for s in joint)
    clock[0] = 3700
    await aoe.tick(parent.id)
    abandoned = await storage.get_task(second.task_id)
    assert abandoned.metadata["status"] == "abandoned" and abandoned.metadata["flag"] is None
    assert len(abandoned.metadata["attempts"]) == 4
    assert not active_attempts(abandoned)
    assert aoe.automation.questions == [first.task_id, second.task_id]
    await storage.update_task(second.task_id, state=RunState.COMPLETED)
    assert await aoe.tick(parent.id)
    assert (await storage.get_task(parent.id)).state == RunState.COMPLETED
    view = await aoe.snapshot(parent.id)
    hard = next(q for q in view["questions"] if q["title"] == "Hard")
    assert hard["attempted_by"] == ["worker-claude", "worker-openai"] and hard["current_agents"] == []
    saved = json.loads((cfg.task_path(second.task_id, "inputs") / "question.json").read_text(encoding="utf-8"))
    assert saved["question_id"] == str(second.task_id)


async def test_collection_files_idempotency_and_scoped_boards_and_paths(tmp_path):
    cfg, storage, _, coordinator, parent, _ = await setup(tmp_path)
    source = cfg.task_path(parent.id, "inputs") / "sample.bin"
    source.write_bytes(b"literal-original")
    spec = AOEQuestionInput(
        source_key="external-id", title="Q", description="original description", attachments=["/inputs/sample.bin"]
    )
    receipt = await coordinator.aoe.register_questions(parent.id, [spec])
    assert receipt == await coordinator.aoe.register_questions(parent.id, [spec])
    question = (await storage.list_questions(parent.id))[0]
    assert cfg.task_path(question.id) == cfg.task_path(parent.id) / str(question.id)
    assert set(p.name for p in cfg.task_path(question.id).iterdir()) == {
        "inputs",
        "workspaces",
        "logs",
        "intels",
        "writeups",
    }
    inputs = await storage.list_input_files(question.id)
    assert len(inputs) == 1 and Path(inputs[0].storage_path).read_bytes() == b"literal-original"
    assert inputs[0].model_dump(mode="json")["question_id"] == str(question.id)
    entries = await storage.list_entries(question.id)
    assert entries and all(e.question_id == question.id for e in entries)
    assert not await storage.list_entries(parent.id)
    assert [task.id for task in await storage.list_tasks()] == [parent.id]
    with pytest.raises(ConflictError):
        await coordinator.aoe.register_questions(parent.id, [spec.model_copy(update={"description": "changed"})])
    with pytest.raises(ContractError):
        await coordinator.aoe.finish_collection(parent.id, 2)
    with pytest.raises(ContractError):
        await coordinator.aoe.register_questions(
            parent.id, [spec.model_copy(update={"source_key": "escape", "attachments": ["../outside.txt"]})]
        )
    await coordinator.aoe.finish_collection(parent.id, 1)
    restored = TGA3Config(Path(__file__).parents[1] / "config")
    restored.runtime.task_root = cfg.runtime.task_root
    another = TaskCoordinator(restored, storage, FakeContainerRuntime())
    await another.aoe.restore_paths()
    assert restored.task_path(question.id) == cfg.task_path(question.id)
    await storage.delete_task(parent.id)
    assert not storage.tasks and not storage.entries and not storage.input_files


async def test_scout_intel_merge_and_registration_cannot_cross_question_boards(tmp_path):
    cfg, storage, _, coordinator, parent, _, questions = await prepare(tmp_path)
    raw = {}
    for name, question in questions.items():
        await storage.update_task(question.id, state=RunState.SCOUTING)
        await storage.update_agent(question.id, "scout", metadata={"scout_phase": "extracting"})
        workspace = cfg.task_path(question.id, "workspaces", "scout")
        workspace.mkdir(parents=True, exist_ok=True)
        journal = ActionJournal(
            cfg.task_path(question.id, "logs", "scout"),
            identity={"task_id": str(question.id), "question_id": str(question.id), "agent_id": "scout"},
            output_directory=workspace,
        )
        action = journal.record(name, "shell", result={"aggregated_output": name}, status="completed")
        assert read_action_log(journal.directory)["question_id"] == str(question.id)
        receipt = await coordinator.scout_intel.write_action(
            question.id, action["action_id"], "same-looking fact", "", "general"
        )
        raw[name] = receipt["id"]
        payload = json.loads((coordinator.scout_intel.directory(question.id) / "scout-intels.json").read_text())
        assert payload["question_id"] == str(question.id)
        await storage.update_agent(question.id, "scout", metadata={"scout_phase": "merging"})
    easy = questions["Easy"]
    with pytest.raises(ContractError):
        await coordinator.scout_intel.merge(ScoutIntelMergeInput(task_id=easy.id, merged_from_ids=[raw["Hard"]]))
    await coordinator.scout_intel.merge(ScoutIntelMergeInput(task_id=easy.id, merged_from_ids=[raw["Easy"]]))
    await coordinator.scout_intel.flush(easy.id)
    assert len([e for e in await storage.list_entries(easy.id) if e.kind == "intel"]) == 1
    assert not [e for e in await storage.list_entries(questions["Hard"].id) if e.kind == "intel"]
    assert not await storage.list_entries(parent.id)


async def test_partial_registration_retry_restores_missing_agents_and_early_realtime_prompt(tmp_path, monkeypatch):
    _, storage, _, coordinator, parent, _ = await setup(tmp_path)
    await coordinator.blackboard.publish(
        parent.id,
        SYSTEM_ACTOR,
        PublishRequest(
            kind="user_prompt",
            topic="realtime",
            body={"text": "Preserve this shared constraint"},
            idempotency_key="realtime:early",
        ),
    )
    original = storage.upsert_agent
    failed = False

    async def interrupted(run):
        nonlocal failed
        if run.task_id != parent.id and run.agent_id == "worker-openai" and not failed:
            failed = True
            raise OSError("interrupted registration")
        return await original(run)

    monkeypatch.setattr(storage, "upsert_agent", interrupted)
    spec = AOEQuestionInput(source_key="retry", title="Retry", description="original")
    with pytest.raises(OSError):
        await coordinator.aoe.register_questions(parent.id, [spec])
    await coordinator.aoe.register_questions(parent.id, [spec])
    question = (await storage.list_questions(parent.id))[0]
    assert question.metadata["registration_complete"]
    assert {a.agent_id for a in await storage.list_agents(question.id)} == set(coordinator.config.agents.agents)
    entries = await storage.list_entries(question.id)
    assert [e.body["text"] for e in entries if e.topic == "realtime"] == ["Preserve this shared constraint"]
