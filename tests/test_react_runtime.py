from __future__ import annotations

import hashlib
import json
from pathlib import Path

from tga.contracts import ResourceProvenance, SessionFile, SessionInput, TGATask
from tga.evidence.store import EvidenceStore
from tga.runtime.manager import Manager, RuntimeLimits
from tga.tools.mcp_manager import MCPManager
from tga.skills.models import SkillBundleSnapshot, SkillSnapshot


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
        assert {"input_read", "finish_session"} <= names
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
            "finish_session",
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
            return self._call("call_early_finish", "finish_session", {
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
        return self._call("call_finish_after_evidence", "finish_session", {
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
        return self._call("call_finish_after_restart", "finish_session", {
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
    task = TGATask(
        id=task_id,
        name="model-independent ReAct",
        mode="ctf",
        goal="Read the input and submit its flag with evidence.",
        flag_format=r"CTF\{[^}]+\}",
        session_input=SessionInput(files=[item]),
        schema_version=5,
    )
    task_root = tmp_path / task_id
    source = task_root / "workspace" / item.relative_path
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(raw)
    store = EvidenceStore(tmp_path / task.id / "evidence.db")
    store.create_task(task)
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


def test_fake_model_drives_real_react_tool_feedback_and_completion(tmp_path: Path) -> None:
    task, item = _seed_task(tmp_path, task_id="react_complete")
    flag = "CTF{model_independent_runtime}"

    client = FakeModelClient(input_id=item.id, flag=flag)
    manager = _manager(tmp_path, client)
    assert manager.start_session(task_id=task.id)["accepted"] is True
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
        actions = store.list_actions(task.id)
        assert len(actions) == 1
        assert actions[0]["status"] == "succeeded"
        assert actions[0]["result"]["artifact_ids"] == [artifact_id]
        event_types = [event.type for event in store.list_agent_events(task.id)]
        assert "ARTIFACT_SAVED" in event_types
        assert "TOOL_EXECUTION_END" in event_types
        assert event_types[-3:] == ["FINISH_ACCEPTED", "AGENT_FINISHED", "SESSION_STOPPED"]
        assert store.task_snapshot(task.id)["flags"][0]["evidence_artifact_id"] == artifact_id
    finally:
        store.close()


def test_first_provider_request_contains_frozen_skill_body_in_system_message(tmp_path: Path) -> None:
    task, item = _seed_task(tmp_path, task_id="react_skill_prompt")
    marker = "FROZEN_SKILL_BODY_IN_PROVIDER_REQUEST"
    task.skill_bundle_snapshot = SkillBundleSnapshot(
        selector="integration-test-selector",
        skills=[SkillSnapshot(
            name="integration-skill",
            version="1",
            origin="custom",
            modes=["ctf"],
            body=marker,
            content_sha256="b" * 64,
            score=100,
            selection_reasons=["integration test"],
        )],
        total_chars=len(marker),
    )
    store = EvidenceStore(tmp_path / task.id / "evidence.db")
    try:
        store.update_task(task)
    finally:
        store.close()
    client = FakeModelClient(input_id=item.id, flag="CTF{model_independent_runtime}")
    manager = _manager(tmp_path, client)

    manager.start_session(task_id=task.id)
    manager.run_session(task.id)

    assert client.requests
    assert client.requests[0][0]["role"] == "system"
    assert marker in client.requests[0][0]["content"]


def test_finish_rejection_continues_to_real_evidence_and_completion(tmp_path: Path) -> None:
    flag = "CTF{finish_rejection_continues}"
    task, item = _seed_task(tmp_path, task_id="react_finish_rejected", flag=flag)
    client = FinishRejectedModelClient(input_id=item.id, flag=flag)
    manager = _manager(tmp_path, client)

    manager.start_session(task_id=task.id)
    snapshot = manager.run_session(task.id)

    assert snapshot["session"]["status"] == "completed"
    assert len(client.requests) == 3
    events = snapshot["agent_events"]
    types = [event["type"] for event in events]
    assert types.index("FINISH_REJECTED") < types.index("CONTINUATION_TRIGGERED") < types.index("ARTIFACT_SAVED")
    assert types[-3:] == ["FINISH_ACCEPTED", "AGENT_FINISHED", "SESSION_STOPPED"]


def test_provider_failure_blocks_with_observable_reason(tmp_path: Path) -> None:
    task, _ = _seed_task(tmp_path, task_id="react_provider_failure")
    manager = _manager(tmp_path, FailingModelClient())

    manager.start_session(task_id=task.id)
    snapshot = manager.run_session(task.id)

    assert snapshot["session"]["status"] == "blocked"
    assert snapshot["session"]["stop_reason"] == "model_request_failed"
    error = next(event for event in snapshot["agent_events"] if event["type"] == "AGENT_ERROR")
    assert error["payload"]["phase"] == "model_turn"
    assert "controlled provider outage" in error["payload"]["message"]


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
    assert sum(event["type"] == "CONTINUATION_TRIGGERED" for event in snapshot["agent_events"]) == 2


def test_policy_rejection_is_returned_as_tool_message_without_execution(tmp_path: Path) -> None:
    task, item = _seed_task(tmp_path, task_id="react_policy_rejection")
    client = PolicyRejectedModelClient(input_id=item.id, flag="unused")
    manager = _manager(tmp_path, client, max_turns=1)

    manager.start_session(task_id=task.id)
    snapshot = manager.run_session(task.id)

    assert snapshot["session"]["status"] == "blocked"
    assert snapshot["session"]["stop_reason"] == "session_turn_limit"
    assert len(snapshot["actions"]) == 1
    assert snapshot["actions"][0]["status"] == "blocked"
    assert snapshot["actions"][0]["result"]["error"]["code"] == "LOCAL_COMPUTE_DISABLED"
    transcript_path = tmp_path / task.id / "solvers" / snapshot["session"]["active_solver_id"] / "session" / "messages.json"
    transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
    tool_result = json.loads(next(item["content"] for item in transcript if item.get("role") == "tool"))
    assert tool_result["status"] == "blocked"
    assert tool_result["error"]["code"] == "LOCAL_COMPUTE_DISABLED"
    assert not any(event["type"] == "TOOL_EXECUTION_START" for event in snapshot["agent_events"])


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
    assert any(event["type"] == "PROVIDER_RESPONSE_DISCARDED" for event in paused["agent_events"])

    second_client = ResumeFromTranscriptModelClient(input_id=item.id, flag=flag)
    second_manager = _manager(tmp_path, second_client)
    assert second_manager.control_session(task_id=task.id, action="resume")["accepted"] is True
    completed = second_manager.run_session(task.id)

    assert completed["session"]["status"] == "completed"
    assert len(completed["actions"]) == 1
    assert completed["flags"][0]["evidence_artifact_id"] == completed["actions"][0]["result"]["artifact_ids"][0]
    assert len(second_client.requests) == 2
