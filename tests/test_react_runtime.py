from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tga.contracts import ResourceProvenance, SessionFile, SessionInput, TGATask
from tests.runtime_fixtures import configure_verified_model, task as v6_task
from tga.evidence.store import EvidenceStore
from tga.infrastructure.persistence import PersistenceBundle
from tga.models.openai_compatible import ProviderRequestError
from tga.runtime.manager import Manager, RuntimeLimits
from tga.tools.mcp_manager import MCPManager
from tga.domain.skills.models import SkillSnapshot, TaskCommonSkillSnapshot
from tga.domain.task.spec import TaskSpec
from tga.domain.retrieval import OwnerScope


@pytest.fixture(autouse=True)
def verified_model(monkeypatch):
    """A schema-v6 task carries a model_snapshot, so the provider must verify."""
    configure_verified_model(monkeypatch)


class FakeModelClient:
    """Protocol fake: decisions are scripted, tool results remain real runtime data."""

    model = "fake-tools-model"
    temperature = 0.0
    supports_vision = False

    def __init__(self, *, input_id: str, flag: str) -> None:
        self.input_id = input_id
        self.flag = flag
        self.requests: list[list[dict]] = []

    def chat_tools(self, messages: list[dict], *, tools: list[dict], temperature: float) -> dict:
        self.requests.append(json.loads(json.dumps(messages)))
        names = {item["function"]["name"] for item in tools}
        assert {"input_read", "propose_task_completion"} <= names
        assert "finish_session" not in names
        tool_results = [item for item in messages if item.get("role") == "tool"]
        if not tool_results:
            return self._call(
                "call_read",
                "input_read",
                {
                    "input_id": self.input_id,
                    "_tga": {
                        "rationale": "Read the immutable task input",
                        "expected_outcome": "Artifact-backed flag evidence",
                    },
                },
            )
        read_result = json.loads(tool_results[-1]["content"])
        artifact_id = read_result["artifact_id"]
        return self._call(
            "call_finish",
            "propose_task_completion",
            {
                "summary": "Recovered the flag from immutable input evidence.",
                "flag": self.flag,
                "evidence_artifact_ids": [artifact_id],
            },
        )

    @staticmethod
    def _call(call_id: str, name: str, arguments: dict) -> dict:
        return {
            "message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(arguments)},
                }],
            },
            "finish_reason": "tool_calls",
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            "request_id": f"request_{call_id}",
        }


class FinishRejectedModelClient(FakeModelClient):
    def chat_tools(self, messages: list[dict], *, tools: list[dict], temperature: float) -> dict:
        self.requests.append(json.loads(json.dumps(messages)))
        tool_results = [item for item in messages if item.get("role") == "tool"]
        if not tool_results:
            return self._call("call_early_finish", "propose_task_completion", {
                "summary": "Premature finish without evidence.",
                "flag": self.flag,
                "evidence_artifact_ids": [],
            })
        latest = json.loads(tool_results[-1]["content"])
        if latest.get("accepted") is False:
            assert any(
                item.get("role") == "user" and "rejected" in str(item.get("content"))
                for item in messages
            )
            return self._call("call_read_after_rejection", "input_read", {
                "input_id": self.input_id,
                "_tga": {
                    "rationale": "Collect the evidence requested by the completion gate",
                    "expected_outcome": "Task-owned Artifact containing the flag",
                },
            })
        return self._call("call_finish_after_evidence", "propose_task_completion", {
            "summary": "Recovered the flag after satisfying the evidence gate.",
            "flag": self.flag,
            "evidence_artifact_ids": [latest["artifact_id"]],
        })


class FailingModelClient:
    model = "failing-tools-model"
    temperature = 0.0
    supports_vision = False

    def chat_tools(self, messages: list[dict], *, tools: list[dict], temperature: float) -> dict:
        raise RuntimeError("controlled provider outage")


class RetryableFailingModelClient(FailingModelClient):
    def chat_tools(self, messages: list[dict], *, tools: list[dict], temperature: float) -> dict:
        raise ProviderRequestError(
            "provider request failed after 3 attempts",
            retryable=True,
            attempts=3,
        )


class IdleModelClient:
    model = "idle-tools-model"
    temperature = 0.0
    supports_vision = False

    def __init__(self) -> None:
        self.requests: list[list[dict]] = []

    def chat_tools(self, messages: list[dict], *, tools: list[dict], temperature: float) -> dict:
        self.requests.append(json.loads(json.dumps(messages)))
        return {
            "message": {"role": "assistant", "content": "I need another turn."},
            "finish_reason": "stop",
            "usage": {"prompt_tokens": 3, "completion_tokens": 2},
            "request_id": f"request_idle_{len(self.requests)}",
        }


class PolicyRejectedModelClient(FakeModelClient):
    def chat_tools(self, messages: list[dict], *, tools: list[dict], temperature: float) -> dict:
        self.requests.append(json.loads(json.dumps(messages)))
        tool_results = [item for item in messages if item.get("role") == "tool"]
        if not tool_results:
            return self._call("call_forbidden_python", "tga_workspace_python", {
                "source": "print('must not execute')",
                "_tga": {"rationale": "Attempt a process outside the current policy"},
            })
        raise AssertionError("turn limit should stop before another provider request")


class PauseAfterReadModelClient(FakeModelClient):
    def __init__(self, *, input_id: str, flag: str, pause) -> None:
        super().__init__(input_id=input_id, flag=flag)
        self.pause = pause

    def chat_tools(self, messages: list[dict], *, tools: list[dict], temperature: float) -> dict:
        self.requests.append(json.loads(json.dumps(messages)))
        self.pause()
        return self._call("call_read_before_pause", "input_read", {
            "input_id": self.input_id,
            "_tga": {"rationale": "Read once before the next turn is paused"},
        })


class ResumeFromTranscriptModelClient(FakeModelClient):
    def chat_tools(self, messages: list[dict], *, tools: list[dict], temperature: float) -> dict:
        self.requests.append(json.loads(json.dumps(messages)))
        tool_results = [item for item in messages if item.get("role") == "tool"]
        if not tool_results:
            return self._call("call_read_after_restart", "input_read", {
                "input_id": self.input_id,
                "_tga": {"rationale": "Re-read the input after the paused response was discarded"},
            })
        assert len(tool_results) == 1
        result = json.loads(tool_results[-1]["content"])
        return self._call("call_finish_after_restart", "propose_task_completion", {
            "summary": "Completed after restoring the durable transcript.",
            "flag": self.flag,
            "evidence_artifact_ids": [result["artifact_id"]],
        })


def _seed_task(tmp_path: Path, *, task_id: str, flag: str = "CTF{model_independent_runtime}") -> tuple[TGATask, SessionFile]:
    raw = f"Evidence: {flag}\n".encode()
    digest = hashlib.sha256(raw).hexdigest()
    item = SessionFile(
        id=f"asset_{'a' * 32}",
        originalName="challenge.txt",
        storedName=f"{'a' * 32}.txt",
        relativePath=f"inputs/files/{'a' * 32}.txt",
        mimeType="text/plain",
        size=len(raw),
        sha256=digest,
        kind="task_input",
        mediaKind="text",
        provenance=ResourceProvenance(source="user_upload", original_name="challenge.txt"),
    )
    task = v6_task(
        id=task_id,
        name="model-independent ReAct",
        mode="ctf",
        goal="Read the input and submit its flag with evidence.",
        flag_format=r"CTF\{[^}]+\}",
        session_input=SessionInput(files=[item]),
        schema_version=6,
    )
    task_root = tmp_path / task_id
    source = task_root / "workspace" / item.relative_path
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(raw)
    store = EvidenceStore(tmp_path / task.id / "evidence.db")
    store.create_task(task)
    bundle = PersistenceBundle(store)
    # TaskSpec is the authoritative resource source, exactly as task creation
    # projects staged inputs.
    bundle.tasks.save_task_spec(TaskSpec(
        task_id=task.id,
        objective=task.goal,
        resources=[entry.resource_ref() for entry in task.session_input.files],
    ))
    store.close()
    return task, item


def _mcp_manager(tmp_path: Path) -> MCPManager:
    config_path = tmp_path / "mcp.json"
    config_path.write_text('{"version":1,"servers":{}}\n', encoding="utf-8")
    return MCPManager(config_path=config_path, cache_path=tmp_path / "mcp-cache.json")


def _manager(tmp_path: Path, client, *, max_turns: int = 48) -> Manager:
    manager = Manager(run_root=tmp_path, model_client=client, mcp_manager=_mcp_manager(tmp_path))
    manager.limits = RuntimeLimits(max_turns=max_turns)
    return manager


def _resolve_initial_intent(tmp_path: Path, task: TGATask) -> None:
    """Make completion fixtures explicit: task-level completion needs no active work."""
    store = EvidenceStore(tmp_path / task.id / "evidence.db")
    try:
        plan = PersistenceBundle(store).plans.get_global_plan(task.id)
        assert plan is not None and len(plan.intents) == 1
        intent = plan.intents[0]
        PersistenceBundle(store).plans.update_intent_status(
            intent.id, "completed", expected_status=intent.status
        )
    finally:
        store.close()


def test_fake_model_drives_real_react_tool_feedback_and_completion(tmp_path: Path) -> None:
    task, item = _seed_task(tmp_path, task_id="react_complete")
    flag = "CTF{model_independent_runtime}"

    client = FakeModelClient(input_id=item.id, flag=flag)
    manager = _manager(tmp_path, client)
    assert manager.start_session(task_id=task.id)["accepted"] is True
    _resolve_initial_intent(tmp_path, task)
    snapshot = manager.run_session(task.id)

    assert snapshot["session"]["status"] == "completed"
    assert snapshot["session"]["stop_reason"] == "finish_accepted"
    assert len(client.requests) == 2
    second_request_tools = [item for item in client.requests[1] if item.get("role") == "tool"]
    assert len(second_request_tools) == 1
    returned = json.loads(second_request_tools[0]["content"])
    artifact_id = returned["artifact_id"]
    assert returned["ok"] is True

    store = EvidenceStore(tmp_path / task.id / "evidence.db")
    try:
        artifact = store.get_artifact(artifact_id)
        assert artifact is not None and artifact.task_id == task.id
        actions = PersistenceBundle(store).tool_governance.list_actions(task.id)
        assert [item["capability"] for item in actions] == [
            "input.read", "propose_task_completion",
        ]
        read_action = actions[0]
        assert read_action["status"] == "succeeded"
        assert read_action["result"]["artifact_ids"] == [artifact_id]
        governed = read_action["payload"]
        context = governed["context"]
        assert context["intent_id"] is None
        assert context["local_plan_step_id"] is None
        assert context["execution_policy_snapshot_id"].startswith("execution:")
        assert context["solver_capability_snapshot_id"].startswith("capabilities:")
        assert governed["id"].startswith("governed_")
        event_types = [event.type for event in store.list_agent_events(task.id)]
        assert "ARTIFACT_SAVED" in event_types
        assert "TOOL_EXECUTION_END" in event_types
        finish_index = event_types.index("TASK_COMPLETION_ACCEPTED")
        assert event_types.index("SESSION_STOPPED") > finish_index
        assert event_types.count("TASK_COMPLETION_ACCEPTED") == 1
        assert store.list_flags(task.id)[0]["evidence_artifact_id"] == artifact_id
        durable_solvers = PersistenceBundle(store).solvers.list_solvers(task.id)
        assert len(durable_solvers) == 1
        assert durable_solvers[0].status == "completed"
        assert durable_solvers[0].timestamps.started_at is not None
        assert durable_solvers[0].timestamps.finished_at is not None
        bundle = PersistenceBundle(store)
        claims = bundle.evidence.list_evidence_claims(task.id)
        assert len(claims) == 1
        assert claims[0].status == "confirmed"
        assert claims[0].artifact_id == artifact_id
        verified = [
            item for item in bundle.knowledge.list_knowledge(task.id)
            if item.status == "verified"
        ]
        assert len(verified) == 1
        assert verified[0].evidence_claim_ids == [claims[0].id]
        retrieval_sources = bundle.retrieval.list_sources()
        assert any(
            item.channel == "task_artifact"
            and item.metadata.get("artifact_id") == artifact_id
            for item in retrieval_sources
        )
        snapshots = bundle.retrieval.list_snapshots()
        assert snapshots and any(
            bundle.retrieval.get_chunk(chunk_id).metadata.get("artifact_id") == artifact_id
            for chunk_id in snapshots[-1].chunk_ids
        )
        binding = bundle.retrieval.get_snapshot_binding(
            OwnerScope(scope="task", task_id=task.id), "context"
        )
        assert binding and binding.index_snapshot_id == snapshots[-1].id
    finally:
        store.close()


def test_first_provider_request_contains_frozen_skill_body_in_system_message(tmp_path: Path) -> None:
    task, item = _seed_task(tmp_path, task_id="react_skill_prompt")
    marker = "FROZEN_SKILL_BODY_IN_PROVIDER_REQUEST"
    common = TaskCommonSkillSnapshot(
        task_id=task.id,
        selector="integration-test-selector",
        skills=(SkillSnapshot(
            name="integration-skill",
            version="1",
            origin="custom",
            modes=("ctf",),
            body=marker,
            content_sha256=hashlib.sha256(marker.encode()).hexdigest(),
            selection_reasons=("integration test",),
        ),),
        total_chars=len(marker),
        created_at="2026-07-30T00:00:00Z",
    )
    store = EvidenceStore(tmp_path / task.id / "evidence.db")
    try:
        PersistenceBundle(store).tasks.save_task_common_skill_snapshot(common)
    finally:
        store.close()
    client = FakeModelClient(input_id=item.id, flag="CTF{model_independent_runtime}")
    manager = _manager(tmp_path, client)

    manager.start_session(task_id=task.id)
    manager.run_session(task.id)

    assert client.requests
    assert client.requests[0][0]["role"] == "system"
    system_prompt = client.requests[0][0]["content"]
    assert "## TASK COMMON SKILLS" in system_prompt
    assert "## SOLVER SPECIALIZED SKILLS" in system_prompt
    assert "cannot grant tools" in system_prompt
    assert marker in system_prompt


def test_finish_rejection_continues_to_real_evidence_and_completion(tmp_path: Path) -> None:
    flag = "CTF{finish_rejection_continues}"
    task, item = _seed_task(tmp_path, task_id="react_finish_rejected", flag=flag)
    client = FinishRejectedModelClient(input_id=item.id, flag=flag)
    manager = _manager(tmp_path, client)

    manager.start_session(task_id=task.id)
    _resolve_initial_intent(tmp_path, task)
    snapshot = manager.run_session(task.id)

    assert snapshot["session"]["status"] == "completed"
    assert len(client.requests) == 3
    events = snapshot["events"]
    types = [event["type"] for event in events]
    assert types.index("FINISH_REJECTED") < types.index("CONTINUATION_TRIGGERED") < types.index("ARTIFACT_SAVED")
    finish_index = types.index("TASK_COMPLETION_ACCEPTED")
    assert types.index("SESSION_STOPPED") > finish_index
    assert types.count("TASK_COMPLETION_ACCEPTED") == 1


def test_provider_failure_blocks_with_observable_reason(tmp_path: Path) -> None:
    task, _ = _seed_task(tmp_path, task_id="react_provider_failure")
    manager = _manager(tmp_path, FailingModelClient())

    manager.start_session(task_id=task.id)
    snapshot = manager.run_session(task.id)

    assert snapshot["session"]["status"] == "blocked"
    assert snapshot["session"]["stop_reason"] == "model_request_failed"
    error = next(event for event in snapshot["events"] if event["type"] == "AGENT_ERROR")
    assert error["payload"]["phase"] == "model_turn"
    assert "controlled provider outage" in error["payload"]["message"]


def test_retryable_provider_failure_projects_attempts_for_recovery(tmp_path: Path) -> None:
    task, _ = _seed_task(tmp_path, task_id="react_retryable_provider_failure")
    manager = _manager(tmp_path, RetryableFailingModelClient())

    manager.start_session(task_id=task.id)
    snapshot = manager.run_session(task.id)

    error = next(event for event in snapshot["events"] if event["type"] == "AGENT_ERROR")
    stopped = next(event for event in snapshot["events"] if event["type"] == "SESSION_STOPPED")
    assert error["payload"]["retryable"] is True
    assert error["payload"]["attempts"] == 3
    assert stopped["payload"]["error"]["retryable"] is True
    assert stopped["payload"]["error"]["attempts"] == 3


def test_max_turns_blocks_after_incremental_continuations(tmp_path: Path) -> None:
    task, _ = _seed_task(tmp_path, task_id="react_turn_limit")
    client = IdleModelClient()
    manager = _manager(tmp_path, client, max_turns=2)

    manager.start_session(task_id=task.id)
    snapshot = manager.run_session(task.id)

    assert snapshot["session"]["status"] == "blocked"
    assert snapshot["session"]["stop_reason"] == "session_turn_limit"
    assert snapshot["session"]["turn_count"] == 2
    assert len(client.requests) == 2
    assert sum(event["type"] == "CONTINUATION_TRIGGERED" for event in snapshot["events"]) == 2


def test_policy_rejection_is_returned_as_tool_message_without_execution(tmp_path: Path) -> None:
    task, item = _seed_task(tmp_path, task_id="react_policy_rejection")
    client = PolicyRejectedModelClient(input_id=item.id, flag="unused")
    manager = _manager(tmp_path, client, max_turns=1)

    manager.start_session(task_id=task.id)
    snapshot = manager.run_session(task.id)

    assert snapshot["session"]["status"] == "blocked"
    assert snapshot["session"]["stop_reason"] == "session_turn_limit"
    # The manifest is the first authorization boundary: a disabled execution
    # tool is not advertised and a forged call never reaches the execution
    # adapter or governed Action table.
    assert snapshot["actions"] == []
    store = EvidenceStore(tmp_path / task.id / "evidence.db")
    try:
        transcript = PersistenceBundle(store).transcripts.list_messages(
            task.id, snapshot["session"]["supervisor_solver_id"]
        )
    finally:
        store.close()
    tool_result = json.loads(next(item["content"] for item in transcript if item.get("role") == "tool"))
    assert tool_result["status"] == "blocked"
    assert tool_result["error"]["code"] == "TOOL_NOT_IN_MANIFEST"
    assert not any(event["type"] == "TOOL_EXECUTION_START" for event in snapshot["events"])


def test_pause_resume_recovers_sqlite_and_transcript_without_duplicate_action(tmp_path: Path) -> None:
    flag = "CTF{durable_restart_resume}"
    task, item = _seed_task(tmp_path, task_id="react_restart_resume", flag=flag)
    first_manager: Manager

    def pause() -> None:
        result = first_manager.control_session(task_id=task.id, action="pause")
        assert result == {"accepted": True, "status": "paused"}

    first_client = PauseAfterReadModelClient(input_id=item.id, flag=flag, pause=pause)
    first_manager = _manager(tmp_path, first_client)
    first_manager.start_session(task_id=task.id)
    paused = first_manager.run_session(task.id)
    assert paused["session"]["status"] == "paused"
    assert paused["actions"] == []
    assert any(event["type"] == "PROVIDER_RESPONSE_DISCARDED" for event in paused["events"])

    second_client = ResumeFromTranscriptModelClient(input_id=item.id, flag=flag)
    second_manager = _manager(tmp_path, second_client)
    assert second_manager.control_session(task_id=task.id, action="resume")["accepted"] is True
    _resolve_initial_intent(tmp_path, task)
    completed = second_manager.run_session(task.id)

    assert completed["session"]["status"] == "completed"
    assert [item["capability"] for item in completed["actions"]] == [
        "input.read", "propose_task_completion",
    ]
    read_action = completed["actions"][0]
    assert completed["flags"][0]["evidence_artifact_id"] == read_action["artifact_ids"][0]
    assert len(second_client.requests) == 2
