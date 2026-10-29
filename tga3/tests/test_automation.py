import json
from pathlib import Path

import pytest

from tga3.blackboard import Blackboard
from tga3.config import TGA3Config
from tga3.domain import Actor, AgentRun, AgentState, Artifact, ArtifactRef, EntryKind, PublishRequest, RunState
from tga3.events import TaskEvents
from tga3.host_agents import Automation, DeterministicHostModel
from tga3.storage import InMemoryStorage


@pytest.mark.asyncio
async def test_final_candidate_freezes_snapshot_and_generates_writeup(tmp_path: Path):
    config = TGA3Config(Path(__file__).parents[1] / "config")
    config.runtime.writeup_root = str(tmp_path)
    config.runtime.cadence.finalization_grace_seconds = 0
    storage = InMemoryStorage()
    task = await storage.create_task("report", "penetration_test")
    for agent_id in ("reporter",):
        configured_agent = config.agents.agents[agent_id]
        state = AgentState.CREATED
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
    events = TaskEvents(storage)
    automation = Automation(config, storage, board, events, DeterministicHostModel())
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
    for agent_id in ("reporter",):
        configured_agent = config.agents.agents[agent_id]
        state = AgentState.CREATED
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
        TaskEvents(storage),
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


@pytest.mark.asyncio
async def test_cli_reporter_starts_after_both_workers_stop(tmp_path):
    from pydantic import SecretStr

    from tga3.coordinator import TaskCoordinator
    from tga3.docker_runtime import FakeContainerRuntime
    from tga3.host_agents import CliHostModel

    config = TGA3Config(Path(__file__).parents[1] / "config")
    config.runtime.writeup_root = str(tmp_path / "reports")
    config.runtime.cadence.finalization_grace_seconds = 0
    for provider in config.models.providers:
        provider.api_keys[0].api_key = SecretStr("test-key")

    class Containers(FakeContainerRuntime):
        async def report(self, spec, emit):
            assert set(self.stopped) == {(spec.task_id, name) for name in config.worker_agent_ids}
            assert json.loads(spec.report_input)[-1]["kind"] == "final_candidate"
            await emit("agent.session", {"cli_session_id": "reporter-session", "runtime": "codex_cli"})
            return await super().report(spec, emit)

    storage, containers = InMemoryStorage(), Containers()
    coordinator = TaskCoordinator(config, storage, containers)
    model = CliHostModel(config, containers, storage, coordinator.events)
    automation = Automation(
        config, storage, coordinator.blackboard, coordinator.events, model, coordinator.finish_task_workers
    )
    task = await coordinator.create_and_start("CLI report", "analyze", "cryptography")
    actor = coordinator.actor_for(await storage.get_agent(task.id, "worker-openai"))
    artifact = await storage.register_artifact(
        Artifact(
            task_id=task.id,
            created_by=actor,
            name="proof",
            storage_path="/artifacts/proof.txt",
            media_type="text/plain",
            sha256="a" * 64,
            size_bytes=2,
        )
    )
    finding = await coordinator.blackboard.publish(
        task.id,
        actor,
        PublishRequest(
            kind="finding",
            body={"claim": "verified"},
            artifact_refs=[ArtifactRef(artifact_id=artifact.id, locator="line 1")],
            idempotency_key="finding",
        ),
    )
    final = await coordinator.blackboard.publish(
        task.id,
        actor,
        PublishRequest(
            kind="final_candidate",
            body={"conclusion": "OK", "rationale": "verified", "finding_ids": [finding.id]},
            idempotency_key="final",
        ),
    )
    await automation.changed(task.id, final.seq)
    await automation._reporter_jobs[task.id]
    assert (await storage.get_task(task.id)).state == RunState.COMPLETED, (
        await storage.get_agent(task.id, "reporter")
    ).last_error
    report = await storage.get_agent(task.id, "reporter")
    assert report.metadata["cli_session_id"] == "reporter-session"
    assert report.container_id == "fake-" + str(task.id) + "-reporter"
    assert len(containers.launched) == 3
    assert len(containers.stopped) == 3
