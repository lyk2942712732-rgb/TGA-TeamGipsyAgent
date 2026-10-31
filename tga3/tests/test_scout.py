import asyncio
from pathlib import Path

from pydantic import SecretStr

from tga3.config import TGA3Config
from tga3.coordinator import TaskCoordinator
from tga3.docker_runtime import FakeContainerRuntime
from tga3.domain import RunState
from tga3.scout_intel import ScoutIntelInput
from tga3.storage import InMemoryStorage


class TwoPhaseScout(FakeContainerRuntime):
    def __init__(self, config, storage):
        super().__init__()
        self.config, self.storage = config, storage
        self.executing = asyncio.Event()
        self.extracting = asyncio.Event()
        self.finish_execution = asyncio.Event()
        self.finish_extraction = asyncio.Event()
        self.execution_error = False

    async def launch(self, spec):
        if spec.agent.role == "worker":
            assert any(entry.kind == "intel" for entry in await self.storage.list_entries(spec.task_id))
            assert (spec.task_id, "scout") in self.stopped
        return await super().launch(spec)

    async def scout(self, spec, emit):
        await self.launch(spec)
        try:
            if spec.scout_phase == "executing":
                root = self.config.resolve_path(self.config.runtime.artifact_root) / str(spec.task_id) / "scout"
                root.mkdir(parents=True)
                (root / "probe.txt").write_text("verified result")
                self.executing.set()
                await self.finish_execution.wait()
                if self.execution_error:
                    raise RuntimeError("simulated execution failure")
            else:
                await self.coordinator.scout_intel.write(ScoutIntelInput(
                    task_id=spec.task_id, key="first", claim="verified result", evidence_paths=["probe.txt"],
                ))
                self.extracting.set()
                await self.finish_extraction.wait()
        finally:
            await self.stop(spec.task_id, "scout")


def setup(tmp_path):
    config = TGA3Config(Path(__file__).parents[1] / "config")
    for provider in config.models.providers:
        provider.api_keys[0].api_key = SecretStr("test-key")
    config.runtime.artifact_root = str(tmp_path / "artifacts")
    config.runtime.intel_root = str(tmp_path / "intels")
    storage = InMemoryStorage()
    runtime = TwoPhaseScout(config, storage)
    coordinator = TaskCoordinator(config, storage, runtime)
    runtime.coordinator = coordinator
    return config, storage, runtime, coordinator


async def test_two_phases_keep_json_private_until_extraction_finishes(tmp_path):
    _, storage, runtime, coordinator = setup(tmp_path)
    task = await coordinator.create_and_start("two phases", "inspect", "pwn")
    await runtime.executing.wait()
    assert (await storage.get_agent(task.id, "scout")).metadata["scout_phase"] == "executing"
    assert not list(coordinator.scout_intel.directory(task.id).glob("*.json"))
    await storage.update_agent(task.id, "scout", metadata={"blackboard_cursor": 5, "blackboard_pending": None})
    runtime.finish_execution.set()
    await runtime.extracting.wait()
    assert (await storage.get_agent(task.id, "scout")).metadata["scout_phase"] == "extracting"
    assert "blackboard_cursor" not in (await storage.get_agent(task.id, "scout")).metadata
    assert len(list(coordinator.scout_intel.directory(task.id).glob("*.json"))) == 1
    assert not any(entry.kind == "intel" for entry in await storage.list_entries(task.id))
    assert [spec.agent.agent_id for spec in runtime.launched] == ["scout", "scout"]
    runtime.finish_extraction.set()
    await coordinator._startup_jobs[task.id]
    assert [spec.agent.agent_id for spec in runtime.launched] == ["scout", "scout", "worker-openai", "worker-claude"]
    assert (await storage.get_task(task.id)).state == RunState.RUNNING
    assert (await storage.get_agent(task.id, "scout")).metadata["intel_imported_count"] == 1


async def test_execution_timeout_starts_extraction_with_an_independent_budget(tmp_path):
    config, storage, runtime, coordinator = setup(tmp_path)
    config.runtime.scout_timeout_seconds = 0.02
    runtime.finish_extraction.set()
    task = await coordinator.create_and_start("timeout", "inspect", "pwn")
    await coordinator._startup_jobs[task.id]
    scout = await storage.get_agent(task.id, "scout")
    assert scout.metadata["scout_executing_result"]["finish_reason"] == "timeout"
    assert scout.metadata["scout_extracting_result"]["finish_reason"] == "completed"
    assert scout.metadata["intel_count"] == 1
    assert (await storage.get_task(task.id)).state == RunState.RUNNING


async def test_extraction_timeout_imports_already_saved_json(tmp_path):
    config, storage, runtime, coordinator = setup(tmp_path)
    config.runtime.scout_extract_timeout_seconds = 0.02
    runtime.finish_execution.set()
    task = await coordinator.create_and_start("extract timeout", "inspect", "pwn")
    await coordinator._startup_jobs[task.id]
    scout = await storage.get_agent(task.id, "scout")
    assert scout.metadata["scout_extracting_result"]["finish_reason"] == "timeout"
    assert scout.metadata["intel_count"] == 1


async def test_execution_failure_still_extracts_saved_artifacts(tmp_path):
    _, storage, runtime, coordinator = setup(tmp_path)
    runtime.execution_error = True
    runtime.finish_execution.set()
    runtime.finish_extraction.set()
    task = await coordinator.create_and_start("failed execution", "inspect", "pwn")
    await coordinator._startup_jobs[task.id]
    scout = await storage.get_agent(task.id, "scout")
    assert scout.metadata["scout_executing_result"]["finish_reason"] == "error"
    assert scout.metadata["intel_count"] == 1


async def test_cancel_in_first_phase_does_not_start_extraction_or_workers(tmp_path):
    _, storage, runtime, coordinator = setup(tmp_path)
    task = await coordinator.create_and_start("cancel", "inspect", "pwn")
    await runtime.executing.wait()
    await coordinator.stop_task(task.id)
    assert len(runtime.launched) == 1
    assert (await storage.get_task(task.id)).state == RunState.CANCELLED


async def test_cancel_in_second_phase_imports_json_without_starting_workers(tmp_path):
    _, storage, runtime, coordinator = setup(tmp_path)
    runtime.finish_execution.set()
    task = await coordinator.create_and_start("cancel extraction", "inspect", "pwn")
    await runtime.extracting.wait()
    await coordinator.stop_task(task.id)
    assert [spec.agent.agent_id for spec in runtime.launched] == ["scout", "scout"]
    assert any(entry.kind == "intel" for entry in await storage.list_entries(task.id))
    assert (await storage.get_task(task.id)).state == RunState.CANCELLED
