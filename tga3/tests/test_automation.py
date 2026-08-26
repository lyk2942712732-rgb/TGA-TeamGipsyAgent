from pathlib import Path

import pytest

from tga3.blackboard import Blackboard
from tga3.config import TGA3Config
from tga3.dialogue import SolverDialogue
from tga3.domain import Actor, AgentRun, AgentState, Artifact, ArtifactRef, EntryKind, PublishRequest, RunState
from tga3.host_agents import Automation, DeterministicHostModel
from tga3.storage import InMemoryStorage


@pytest.mark.asyncio
async def test_final_candidate_freezes_snapshot_and_generates_writeup(tmp_path: Path):
    config = TGA3Config(Path(__file__).parents[1] / "config")
    config.runtime.writeup_root = str(tmp_path)
    config.runtime.cadence.finalization_grace_seconds = 0
    config.runtime.cadence.supervisor_debounce_seconds = 0
    storage = InMemoryStorage()
    task = await storage.create_task("report", "penetration_test")
    for agent_id in ("supervisor", "reporter"):
        configured_agent = config.agents.agents[agent_id]
        state = AgentState.IDLE if agent_id == "supervisor" else AgentState.CREATED
        await storage.upsert_agent(
            AgentRun(
                task_id=task.id,
                agent_id=agent_id,
                sdk=configured_agent.runtime,
                desired_state=state,
                actual_state=state,
                    provider_id=configured_agent.provider_id,
                    model_id=configured_agent.model_id,
                    protocol=configured_agent.protocol,
            )
        )
    board = Blackboard(storage)
    dialogue = SolverDialogue(storage)
    automation = Automation(config, storage, board, dialogue, DeterministicHostModel())
    actor = Actor(agent_id="worker-openai", display_name="OpenAI Worker", role="worker")
    proof = await storage.register_artifact(
        Artifact(
            task_id=task.id,
            created_by=actor,
            name="proof.txt",
            storage_path="/artifacts/proof.txt",
            media_type="text/plain",
            sha256="b" * 64,
            size_bytes=4,
        )
    )
    finding = await board.publish(
        task.id,
        actor,
        PublishRequest(
            kind=EntryKind.FINDING,
            body={"claim": "flag verified"},
            artifact_refs=[ArtifactRef(artifact_id=proof.id, locator="line 1")],
            idempotency_key="finding",
        ),
    )
    final = await board.publish(
        task.id,
        actor,
        PublishRequest(
            kind=EntryKind.FINAL_CANDIDATE,
            body={"conclusion": "TGA{ok}", "rationale": "verified", "finding_ids": [finding.id]},
            idempotency_key="final",
        ),
    )

    await automation.changed(task.id, final.seq)
    await automation._reporter_jobs[task.id]
    if task.id in automation._supervisor_jobs:
        await automation._supervisor_jobs[task.id]
    completed = await storage.get_task(task.id)
    assert completed.state == RunState.COMPLETED
    assert completed.final_snapshot_seq == 2
    assert (tmp_path / str(task.id) / "writeup.md").is_file()
    writeup = await storage.latest_writeup(task.id)
    assert writeup.task_id == task.id
    assert writeup.snapshot_seq == 2


@pytest.mark.asyncio
async def test_reporter_failure_stops_workers_and_finishes_host_states(tmp_path: Path):
    class FailingReporter(DeterministicHostModel):
        async def report(self, snapshot: str, binding=None) -> str:
            raise RuntimeError("Max turns (2) exceeded")

    config = TGA3Config(Path(__file__).parents[1] / "config")
    config.runtime.writeup_root = str(tmp_path)
    config.runtime.cadence.finalization_grace_seconds = 0
    storage = InMemoryStorage()
    task = await storage.create_task("report failure", "penetration_test")
    for agent_id in ("supervisor", "reporter"):
        configured_agent = config.agents.agents[agent_id]
        state = AgentState.IDLE if agent_id == "supervisor" else AgentState.CREATED
        await storage.upsert_agent(
            AgentRun(
                task_id=task.id,
                agent_id=agent_id,
                sdk=configured_agent.runtime,
                desired_state=state,
                actual_state=state,
                provider_id=configured_agent.provider_id,
                model_id=configured_agent.model_id,
                protocol=configured_agent.protocol,
            )
        )
    finished: list = []

    async def finish_workers(task_id):
        finished.append(task_id)

    automation = Automation(
        config,
        storage,
        Blackboard(storage),
        SolverDialogue(storage),
        FailingReporter(),
        finish_workers,
    )

    await automation._report(task.id)

    assert finished == [task.id]
    assert (await storage.get_task(task.id)).state == RunState.FAILED
    reporter = await storage.get_agent(task.id, "reporter")
    assert reporter.desired_state == AgentState.FAILED
    assert reporter.actual_state == AgentState.FAILED
    assert reporter.last_error == "Max turns (2) exceeded"
    supervisor = await storage.get_agent(task.id, "supervisor")
    assert supervisor.desired_state == AgentState.STOPPED
    assert supervisor.actual_state == AgentState.STOPPED
