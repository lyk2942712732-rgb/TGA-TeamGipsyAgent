import asyncio
import json
from pathlib import Path

import pytest
from pydantic import SecretStr

from tga3.action_logs import read_action_log
from tga3.config import TGA3Config
from tga3.coordinator import TaskCoordinator
from tga3.docker_runtime import FakeContainerRuntime
from tga3.domain import RunState
from tga3.errors import ContractError
from tga3.scout_intel import ScoutIntelInput, ScoutIntelMergeInput
from tga3.storage import InMemoryStorage
from tga3.workers.action_journal import ActionJournal


class ThreePhaseScout(FakeContainerRuntime):
    def __init__(self, config, storage):
        super().__init__()
        self.config, self.storage = config, storage
        self.executing = asyncio.Event()
        self.extracting = asyncio.Event()
        self.merging = asyncio.Event()
        self.finish_merging = asyncio.Event()
        self.finish_merging.set()
        self.finish_execution = asyncio.Event()
        self.finish_extraction = asyncio.Event()
        self.execution_error = False

    async def launch(self, spec):
        if spec.agent.role == "worker":
            assert any(entry.kind == "intel" for entry in await self.storage.list_entries(spec.task_id))
            assert (spec.task_id, "scout") in self.stopped
        return await super().launch(spec)

    async def scout(self, spec, emit):
        if spec.scout_phase == "executing":
            await self.launch(spec)
        try:
            if spec.scout_phase == "executing":
                root = self.config.task_path(spec.task_id, "workspaces", "scout")
                root.mkdir(parents=True)
                journal = ActionJournal(self.config.task_path(spec.task_id, "logs", "scout"),
                                        identity={"task_id": str(spec.task_id), "agent_id": "scout"},
                                        output_directory=root)
                journal.record("probe", "shell", result={"aggregated_output": "verified result"}, status="completed")
                self.action = read_action_log(journal.directory)["actions"][0]
                self.executing.set()
                await self.finish_execution.wait()
                if self.execution_error:
                    raise RuntimeError("simulated execution failure")
            elif spec.scout_phase == "extracting":
                receipt = await self.coordinator.scout_intel.write(ScoutIntelInput(
                    task_id=spec.task_id, claim="verified result",
                    evidence_paths=[self.action["artifact"]["output_path"]],
                    source_action_ids=[self.action["action_id"]],
                ))
                self.raw_id = receipt["id"]
                self.extracting.set()
                await self.finish_extraction.wait()
            else:
                await self.coordinator.scout_intel.merge(ScoutIntelMergeInput(
                    task_id=spec.task_id, claim="verified result",
                    merged_from_ids=[self.raw_id],
                ))
                self.merging.set()
                await self.finish_merging.wait()
        finally:
            if spec.scout_phase == "merging":
                await self.stop(spec.task_id, "scout")


def setup(tmp_path):
    config = TGA3Config(Path(__file__).parents[1] / "config")
    for provider in config.models.providers:
        provider.api_keys[0].api_key = SecretStr("test-key")
    config.runtime.task_root = str(tmp_path / "tasks")
    storage = InMemoryStorage()
    runtime = ThreePhaseScout(config, storage)
    coordinator = TaskCoordinator(config, storage, runtime)
    runtime.coordinator = coordinator
    return config, storage, runtime, coordinator


async def test_three_phases_preserve_raw_and_keep_json_private_until_merging_finishes(tmp_path):
    _, storage, runtime, coordinator = setup(tmp_path)
    task = await coordinator.create_and_start("two phases", "inspect", "pwn")
    await runtime.executing.wait()
    assert (await storage.get_agent(task.id, "scout")).metadata["scout_phase"] == "executing"
    assert not list(coordinator.scout_intel.directory(task.id).glob("*.json"))
    await storage.update_agent(task.id, "scout", metadata={"blackboard_cursor": 5, "blackboard_pending": None})
    runtime.finish_execution.set()
    await runtime.extracting.wait()
    assert (await storage.get_agent(task.id, "scout")).metadata["scout_phase"] == "extracting"
    assert (await storage.get_agent(task.id, "scout")).metadata["blackboard_cursor"] == 5
    assert len(list(coordinator.scout_intel.directory(task.id).glob("*.json"))) == 1
    assert not any(entry.kind == "intel" for entry in await storage.list_entries(task.id))
    assert not storage.artifacts
    assert [spec.agent.agent_id for spec in runtime.launched] == ["scout"]
    raw_path = next(coordinator.scout_intel.directory(task.id).glob("*.json"))
    original = raw_path.read_bytes()
    runtime.finish_merging.clear()
    runtime.finish_extraction.set()
    await runtime.merging.wait()
    assert (await storage.get_agent(task.id, "scout")).metadata["scout_phase"] == "merging"
    assert not any(entry.kind == "intel" for entry in await storage.list_entries(task.id))
    assert raw_path.read_bytes() == original
    assert len(list(coordinator.scout_intel.directory(task.id, merged=True).glob("*.json"))) == 1
    runtime.finish_merging.set()
    await coordinator._startup_jobs[task.id]
    assert [spec.agent.agent_id for spec in runtime.launched] == ["scout", "worker-openai", "worker-claude"]
    assert (await storage.get_task(task.id)).state == RunState.RUNNING
    assert (await storage.get_agent(task.id, "scout")).metadata["intel_imported_count"] == 1


async def test_execution_timeout_still_completes_extraction_and_merging(tmp_path):
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


async def test_extraction_ignores_legacy_budget_and_waits_for_complete_coverage(tmp_path):
    config, storage, runtime, coordinator = setup(tmp_path)
    config.runtime.scout_extract_timeout_seconds = 0.02
    runtime.finish_execution.set()
    task = await coordinator.create_and_start("no extraction deadline", "inspect", "pwn")
    await runtime.extracting.wait()
    await asyncio.sleep(0.08)
    assert not coordinator._startup_jobs[task.id].done()
    assert not any(entry.kind == "intel" for entry in await storage.list_entries(task.id))
    runtime.finish_extraction.set()
    await coordinator._startup_jobs[task.id]
    scout = await storage.get_agent(task.id, "scout")
    assert scout.metadata["scout_extracting_result"]["budget_seconds"] is None
    assert scout.metadata["scout_merging_result"]["budget_seconds"] is None
    assert scout.metadata["action_uncovered"] == []
    assert scout.metadata["intel_raw_uncovered"] == []


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


async def test_cancel_in_second_phase_retains_raw_json_without_publishing_or_starting_workers(tmp_path):
    _, storage, runtime, coordinator = setup(tmp_path)
    runtime.finish_execution.set()
    task = await coordinator.create_and_start("cancel extraction", "inspect", "pwn")
    await runtime.extracting.wait()
    await coordinator.stop_task(task.id)
    assert [spec.agent.agent_id for spec in runtime.launched] == ["scout"]
    assert not any(entry.kind == "intel" for entry in await storage.list_entries(task.id))
    assert coordinator.scout_intel.documents(task.id)
    assert (await storage.get_task(task.id)).state == RunState.CANCELLED


async def test_merge_unions_provenance_preserves_raw_and_deduplicates_retries(tmp_path):
    _, storage, runtime, coordinator = setup(tmp_path)
    runtime.finish_execution.set()
    task = await coordinator.create_and_start("merge provenance", "inspect", "pwn")
    await runtime.extracting.wait()
    store = coordinator.scout_intel
    # A second original observation can share evidence with the first.
    second = await store.write(ScoutIntelInput(task_id=task.id, claim="additional detail",
                                    source_action_ids=[runtime.action["action_id"]],
                                    evidence_paths=[runtime.action["artifact"]["output_path"]]))
    raw = {p.name: p.read_bytes() for p in store.directory(task.id).glob("*.json")}
    await storage.update_agent(task.id, "scout", metadata={"scout_phase": "merging"})
    request = ScoutIntelMergeInput(task_id=task.id, claim="combined verified detail",
                                  merged_from_ids=[second["id"], runtime.raw_id])
    first, retry = await asyncio.gather(store.merge(request), store.merge(request))
    assert first == retry and first["path"].startswith("/intels/merged/")
    assert {p.name: p.read_bytes() for p in store.directory(task.id).glob("*.json")} == raw
    merged = store.documents(task.id, merged=True)[0]
    assert merged.id.startswith("scout-merged-intel-")
    assert merged.merged_from_ids == sorted([runtime.raw_id, second["id"]])
    assert merged.source_action_ids == [runtime.action["action_id"]]
    assert len(merged.evidence_paths) == 1
    assert not storage.artifacts
    with pytest.raises(ContractError, match="extraction"):
        await store.write(ScoutIntelInput(task_id=task.id, claim="late"))
    with pytest.raises(Exception, match="already merged"):
        await store.merge(request.model_copy(update={"claim": "changed combined fact"}))
    with pytest.raises(ContractError, match="Unknown"):
        await store.merge(request.model_copy(update={"merged_from_ids": ["scout-raw-intel-" + "0" * 32]}))
    assert await store.flush(task.id) == 1
    assert await store.flush(task.id) == 1
    entries = [entry for entry in await storage.list_entries(task.id) if entry.kind == "intel"]
    assert len(entries) == 1 and entries[0].body["claim"] == request.claim
    assert entries[0].source_action_ids == merged.source_action_ids
    # Stop the still-waiting fixture without starting any Workers.
    await coordinator.stop_task(task.id)


async def test_incomplete_merge_cannot_publish_even_saved_final_files(tmp_path):
    _, storage, runtime, coordinator = setup(tmp_path)
    runtime.finish_execution.set()
    task = await coordinator.create_and_start("partial merge", "inspect", "pwn")
    await runtime.extracting.wait()
    store = coordinator.scout_intel
    await store.write(ScoutIntelInput(task_id=task.id, claim="separate fact",
                                    source_action_ids=[runtime.action["action_id"]]))
    await storage.update_agent(task.id, "scout", metadata={"scout_phase": "merging"})
    await store.merge(ScoutIntelMergeInput(task_id=task.id, claim="verified result",
                                         merged_from_ids=[runtime.raw_id]))
    with pytest.raises(ContractError, match="exactly once"):
        await store.flush(task.id)
    assert not any(entry.kind == "intel" for entry in await storage.list_entries(task.id))
    await coordinator.stop_task(task.id)


async def test_coordinator_rejects_false_extraction_completion(tmp_path):
    _, storage, runtime, coordinator = setup(tmp_path)
    runtime.finish_execution.set()
    task = await coordinator.create_and_start("coverage gate", "inspect", "pwn")
    await runtime.extracting.wait()
    path = next(coordinator.scout_intel.directory(task.id).glob("*.json"))
    document = json.loads(path.read_text())
    document["intels"][0]["source_action_ids"] = []
    path.write_text(json.dumps(document))
    runtime.finish_extraction.set()
    await coordinator._startup_jobs[task.id]
    assert (await storage.get_task(task.id)).state == RunState.FAILED
    assert not runtime.merging.is_set()
    assert [spec.agent.agent_id for spec in runtime.launched] == ["scout"]
    assert not any(entry.kind == "intel" for entry in await storage.list_entries(task.id))
