from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tga.contracts import (
    ActionResult,
    ActionSpec,
    CtfModeConfig,
    ExecutionPolicy,
    HighImpactExecutionPolicy,
    LocalComputeExecutionPolicy,
    NetworkExecutionPolicy,
    SessionInput,
    SessionRecord,
    TGATask,
)
from tga.evidence.artifacts import ArtifactStore
from tga.evidence.store import EvidenceStore
from tga.infrastructure.persistence import PersistenceBundle
from tga.runtime.completion_validators import CompletionValidationContext, FinishSubmission, validator_for
from tga.runtime.coordinator import SessionCoordinator
from tga.runtime.approvals import expire_pending_approvals
from tga.runtime.manager import Manager, RuntimeLimits
from tga.network_policy import authorize_url
from tga.tools.mcp_manager import MCPManager
from tests.runtime_fixtures import mcp_snapshot


def _mcp(tmp_path: Path) -> MCPManager:
    config = tmp_path / "mcp.json"
    config.write_text('{"version":1,"servers":{}}\n', encoding="utf-8")
    return MCPManager(config_path=config, cache_path=tmp_path / "mcp-cache.json")


def _approval_mcp(tmp_path: Path) -> MCPManager:
    fixture = Path(__file__).parent / "fixtures" / "fake_mcp_server.py"
    config = tmp_path / "approval-mcp.json"
    config.write_text(json.dumps({
        "version": 1,
        "servers": {"fixture": {
            "transport": "stdio",
            "stdio": {"source": "local_process", "command": __import__("sys").executable, "args": [str(fixture)]},
            "visibility": {"risk": "destructive"},
        }},
    }), encoding="utf-8")
    manager = MCPManager(config_path=config, cache_path=tmp_path / "approval-mcp-cache.json")
    manager.refresh()
    return manager


def _task(task_id: str, *, policy: ExecutionPolicy | None = None) -> TGATask:
    return TGATask(
        id=task_id,
        name=task_id,
        mode="ctf",
        goal="test the runtime boundary",
        session_input=SessionInput(prompt="Use the supplied task context."),
        execution_policy=policy or ExecutionPolicy(),
        schema_version=6,
    )


class CancelDuringProvider:
    model = "cancel-race"
    temperature = 0.0
    supports_vision = False

    def __init__(self) -> None:
        self.cancel = None

    def chat_tools(self, messages, *, tools, temperature):
        assert self.cancel is not None
        self.cancel()
        return {
            "message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "late_call",
                    "type": "function",
                    "function": {"name": "input_list", "arguments": "{}"},
                }],
            },
            "finish_reason": "tool_calls",
            "request_id": "late_response",
        }


def test_cancel_discards_late_provider_tool_calls(tmp_path: Path) -> None:
    task = _task("cancel_boundary")
    store = EvidenceStore(tmp_path / task.id / "evidence.db")
    store.create_task(task)
    store.close()
    client = CancelDuringProvider()
    manager = Manager(run_root=tmp_path, model_client=client, mcp_manager=_mcp(tmp_path))
    client.cancel = lambda: manager.control_session(task_id=task.id, action="cancel")

    assert manager.start_session(task_id=task.id)["accepted"] is True
    snapshot = manager.run_session(task.id)

    assert snapshot["session"]["status"] == "cancelled"
    assert snapshot["session"]["turn_count"] == 0
    assert snapshot["actions"] == []
    assert any(event["type"] == "PROVIDER_RESPONSE_DISCARDED" for event in snapshot["agent_events"])
    assert not any(event["type"] == "TOOL_EXECUTION_START" for event in snapshot["agent_events"])


def test_approval_requires_task_owned_pending_action(tmp_path: Path) -> None:
    task = _task("approval_owner")
    store = EvidenceStore(tmp_path / task.id / "evidence.db")
    store.create_task(task)
    store.create_session(SessionRecord(task_id=task.id, schema_version=6, status="running"))
    action = ActionSpec(
        id="act_pending",
        task_id=task.id,
        solver_id="agent_main",
        kind="http",
        capability="http.request",
        target="https://example.test",
        arguments={"method": "DELETE", "url": "https://example.test/item"},
        rationale="delete test data",
        risk="active",
    )
    store.add_action(action, status="pending_approval")
    SessionCoordinator(store).await_approval(task_id=task.id, action_id=action.id)
    manager = Manager(store=store, run_root=tmp_path)

    missing = manager.control_session(task_id=task.id, action="approve_action", action_id="act_missing")
    assert missing == {"accepted": False, "status": "awaiting_approval", "reason": "action_not_found"}
    accepted = manager.control_session(task_id=task.id, action="approve_action", action_id=action.id)
    assert accepted["accepted"] is True
    assert accepted["status"] == "running"
    assert store.get_action(task.id, action.id)["status"] == "approved"
    repeated = manager.control_session(task_id=task.id, action="approve_action", action_id=action.id)
    assert repeated["accepted"] is False
    store.close()


def test_expired_approval_rejects_persisted_action_and_resumes_session(tmp_path: Path) -> None:
    task = _task("approval_expiry")
    store = EvidenceStore(tmp_path / task.id / "evidence.db")
    store.create_task(task)
    store.create_session(SessionRecord(task_id=task.id, schema_version=6, status="running"))
    action = ActionSpec(
        id="act_expired",
        task_id=task.id,
        solver_id="agent_main",
        kind="http",
        capability="http.request",
        target="https://example.test",
        arguments={"method": "DELETE", "url": "https://example.test/item"},
        rationale="remove expired fixture",
        risk="active",
    )
    deadline = (datetime.now(UTC) - timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
    store.add_action(action, status="pending_approval", approval_expires_at=deadline)
    SessionCoordinator(store).await_approval(task_id=task.id, action_id=action.id)

    assert expire_pending_approvals(store, task.id) == [action.id]
    assert store.get_session(task.id).status == "running"  # type: ignore[union-attr]
    expired = store.get_action(task.id, action.id)
    assert expired is not None and expired["status"] == "rejected"
    assert expired["result"]["error"]["code"] == "ACTION_APPROVAL_EXPIRED"
    event = [item for item in store.list_agent_events(task.id) if item.type == "ACTION_APPROVAL_EXPIRED"][-1]
    assert event.payload["approval_expires_at"] == deadline
    store.close()


def test_cancel_terminates_pending_approval_action(tmp_path: Path) -> None:
    task = _task("approval_cancel")
    store = EvidenceStore(tmp_path / task.id / "evidence.db")
    store.create_task(task)
    store.create_session(SessionRecord(task_id=task.id, schema_version=6, status="running"))
    action = ActionSpec(
        id="act_cancelled", task_id=task.id, solver_id="agent_main",
        kind="http", capability="http.request", target="https://example.test",
        arguments={"method": "DELETE", "url": "https://example.test/item"},
        rationale="remove fixture", risk="active",
    )
    store.add_action(action, status="pending_approval")
    SessionCoordinator(store).await_approval(task_id=task.id, action_id=action.id)

    result = Manager(store=store, run_root=tmp_path).control_session(
        task_id=task.id, action="cancel"
    )

    assert result == {"accepted": True, "status": "cancelled"}
    cancelled = store.get_action(task.id, action.id)
    assert cancelled is not None and cancelled["status"] == "cancelled"
    assert cancelled["result"]["error"]["code"] == "ACTION_CANCELLED"
    assert any(event.type == "ACTION_CANCELLED" for event in store.list_agent_events(task.id))
    store.close()


def test_multiple_flags_are_confirmed_across_finish_attempts(tmp_path: Path) -> None:
    task = _task("multi_flag").model_copy(update={
        "flag_format": r"CTF\{[^}]+\}",
        "mode_config": CtfModeConfig(flag_format=r"CTF\{[^}]+\}", expected_flag_count=2),
    })
    root = tmp_path / task.id
    store = EvidenceStore(root / "evidence.db")
    store.create_task(task)
    artifacts = ArtifactStore(root / "artifacts")
    first = artifacts.save_text(
        task_id=task.id, intent_id=None, kind="tool_output", text="CTF{first}",
    )
    second = artifacts.save_text(
        task_id=task.id, intent_id=None, kind="tool_output", text="CTF{second}",
    )
    store.add_artifact(first)
    store.add_artifact(second)
    context = CompletionValidationContext(
        task=task,
        solver_id="agent_main",
        store=store,
        artifact_text=lambda _task_id, artifact: artifacts.read_text(artifact.id),
    )

    one = validator_for("ctf").validate(
        context=context,
        submission=FinishSubmission(
            summary="first flag", flag="CTF{first}", evidence_artifact_ids=[first.id],
        ),
    )
    two = validator_for("ctf").validate(
        context=context,
        submission=FinishSubmission(
            summary="second flag", flag="CTF{second}", evidence_artifact_ids=[second.id],
        ),
    )

    assert one.code == "CTF_EXPECTED_FLAGS_MISSING"
    assert two.accepted is True
    assert {item["value"] for item in store.list_flags(task.id)} == {"CTF{first}", "CTF{second}"}
    assert sum(event.type == "FLAG_CONFIRMED" for event in store.list_agent_events(task.id)) == 2
    store.close()


def test_custom_network_rules_authorize_origin_domain_and_cidr() -> None:
    exact = NetworkExecutionPolicy(
        access="custom",
        custom_origins=["https://api.example.test"],
    )
    authorize_url("https://api.example.test/v1", exact, resolve_dns=False)

    wildcard = NetworkExecutionPolicy(
        access="custom",
        custom_domains=["*.example.test"],
    )
    authorize_url("https://sub.example.test/path", wildcard, resolve_dns=False)

    cidr = NetworkExecutionPolicy(
        access="custom",
        custom_cidrs=["203.0.113.0/24"],
        deny_private_networks=False,
    )
    authorize_url("http://203.0.113.7/resource", cidr, resolve_dns=False)

    try:
        authorize_url("https://example.test", wildcard, resolve_dns=False)
    except PermissionError as exc:
        assert str(exc) == "NETWORK_TARGET_NOT_IN_CUSTOM_ALLOWLIST"
    else:
        raise AssertionError("wildcard must not authorize the apex domain")


def test_network_authorization_does_not_filter_resolved_ip_address_classes() -> None:
    policy = NetworkExecutionPolicy(access="public_internet")

    assert authorize_url("http://127.0.0.1:8080/", policy) == ["127.0.0.1"]
    assert authorize_url("http://169.254.169.254/latest/meta-data/", policy) == ["169.254.169.254"]
    assert authorize_url("http://198.18.0.8/", policy) == ["198.18.0.8"]


class ApprovalProvider:
    model = "approval-model"
    temperature = 0.0
    supports_vision = False

    def __init__(self) -> None:
        self.requests = []

    def chat_tools(self, messages, *, tools, temperature):
        self.requests.append(json.loads(json.dumps(messages)))
        results = [item for item in messages if item.get("role") == "tool"]
        if not results:
            return {
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{
                        "id": "delete_call",
                        "type": "function",
                        "function": {
                            "name": "tga_http_request",
                            "arguments": json.dumps({
                                "method": "DELETE",
                                "url": "https://example.test/item",
                                "_tga": {
                                    "rationale": "remove authorized test fixture",
                                    "effect": {
                                        "scope": "target",
                                        "persistence": "persistent",
                                        "reversibility": "irreversible",
                                        "category": "resource_delete",
                                        "description": "the fixture is removed",
                                    },
                                    "alternative_analysis": "a GET cannot validate deletion behavior",
                                },
                            }),
                        },
                    }],
                },
                "finish_reason": "tool_calls",
            }
        return {"message": {"role": "assistant", "content": "approval result observed"}, "finish_reason": "stop"}


class RecordingExecutor:
    def __init__(self) -> None:
        self.actions = []

    def execute(self, *, task, action, workspace):
        self.actions.append(action)
        return ActionResult(
            action_id=action.id,
            task_id=task.id,
            solver_id=action.solver_id,
            status="succeeded",
            summary="approved action executed",
        )


def test_approved_action_executes_persisted_spec_once(tmp_path: Path) -> None:
    policy = ExecutionPolicy(
        preset="custom",
        network=NetworkExecutionPolicy(
            access="task_sources",
            interaction="interact",
            seed_origins=["https://example.test"],
        ),
        high_impact=HighImpactExecutionPolicy(mode="approval_required"),
    )
    task = _task("approval_execute", policy=policy).model_copy(
        update={"task_entry_url": "https://example.test/"}
    )
    store = EvidenceStore(tmp_path / task.id / "evidence.db")
    store.create_task(task)
    store.close()
    client = ApprovalProvider()
    executor = RecordingExecutor()
    manager = Manager(
        run_root=tmp_path,
        model_client=client,
        executor=executor,
        mcp_manager=_mcp(tmp_path),
    )
    manager.limits = RuntimeLimits(max_turns=2)

    manager.start_session(task_id=task.id)
    pending = manager.run_session(task.id)
    assert pending["session"]["status"] == "running"
    scoped = PersistenceBundle.open(tmp_path / task.id / "evidence.db")
    try:
        assert scoped.solvers.list_solvers(task.id)[0].status == "awaiting_approval"
        assert scoped.plans.get_global_plan(task.id).intents[0].status == "awaiting_approval"
    finally:
        scoped.close()
    action_id = pending["actions"][0]["id"]
    assert pending["actions"][0]["status"] == "pending_approval"
    assert executor.actions == []

    assert manager.control_session(
        task_id=task.id, action="approve_action", action_id=action_id
    )["accepted"] is True
    final = manager.run_session(task.id)

    assert len(executor.actions) == 1
    assert executor.actions[0].id == action_id
    assert executor.actions[0].arguments["method"] == "DELETE"
    assert final["actions"][0]["status"] == "succeeded"
    tool_results = [item for item in client.requests[-1] if item.get("role") == "tool"]
    assert len(tool_results) == 1
    assert tool_results[0]["tool_call_id"] == "delete_call"


class MCPApprovalProvider:
    model = "mcp-approval-model"
    temperature = 0.0
    supports_vision = False

    def __init__(self) -> None:
        self.requests: list[list[dict]] = []

    def chat_tools(self, messages, *, tools, temperature):
        self.requests.append(json.loads(json.dumps(messages)))
        results = [item for item in messages if item.get("role") == "tool"]
        if not results:
            return {
                "message": {"role": "assistant", "content": "", "tool_calls": [{
                    "id": "mcp_high_impact_call",
                    "type": "function",
                    "function": {
                        "name": "mcp__fixture__echo",
                        "arguments": json.dumps({
                            "text": "persisted approval arguments",
                            "_tga": {
                                "effect": {
                                    "scope": "target",
                                    "persistence": "persistent",
                                    "reversibility": "uncertain",
                                    "category": "resource_modify",
                                    "description": "Modify the explicitly reviewed fixture resource.",
                                },
                                "alternative_analysis": "A passive read cannot validate this destructive MCP behavior.",
                            },
                        }),
                    },
                }]},
                "finish_reason": "tool_calls",
            }
        return {"message": {"role": "assistant", "content": "approval result observed"}, "finish_reason": "stop"}


def _mcp_approval_task(task_id: str, manager: MCPManager) -> TGATask:
    return _task(task_id, policy=ExecutionPolicy(
        preset="custom",
        local_compute=LocalComputeExecutionPolicy(mode="isolated"),
        high_impact=HighImpactExecutionPolicy(mode="approval_required"),
    )).model_copy(update={"mcp_capabilities": mcp_snapshot(manager.snapshot, "fixture")})


def test_approved_mcp_action_executes_persisted_route_and_arguments_once(tmp_path: Path) -> None:
    mcp = _approval_mcp(tmp_path)
    task = _mcp_approval_task("mcp_approval_execute", mcp)
    store = EvidenceStore(tmp_path / task.id / "evidence.db")
    store.create_task(task)
    store.close()
    client = MCPApprovalProvider()
    manager = Manager(run_root=tmp_path, model_client=client, mcp_manager=mcp)
    manager.limits = RuntimeLimits(max_turns=2)

    manager.start_session(task_id=task.id)
    pending = manager.run_session(task.id)
    action = pending["actions"][0]
    assert pending["session"]["status"] == "running"
    assert action["status"] == "pending_approval"
    assert action["arguments"] == {"text": "persisted approval arguments"}

    assert manager.control_session(task_id=task.id, action="approve_action", action_id=action["id"])["accepted"] is True
    completed = manager.run_session(task.id)
    assert completed["actions"][0]["status"] == "succeeded"
    assert len([event for event in completed["agent_events"] if event["type"] == "TOOL_EXECUTION_START"]) == 1
    transcript = json.loads((tmp_path / task.id / "solvers" / completed["session"]["active_solver_id"] / "session" / "messages.json").read_text(encoding="utf-8"))
    result = next(item for item in transcript if item.get("role") == "tool")
    assert result["tool_call_id"] == "mcp_high_impact_call"
    assert json.loads(result["content"])["status"] == "succeeded"


def test_approved_mcp_action_fails_closed_when_catalog_changes(tmp_path: Path) -> None:
    mcp = _approval_mcp(tmp_path)
    task = _mcp_approval_task("mcp_approval_stale", mcp)
    store = EvidenceStore(tmp_path / task.id / "evidence.db")
    store.create_task(task)
    store.close()
    manager = Manager(run_root=tmp_path, model_client=MCPApprovalProvider(), mcp_manager=mcp)
    manager.limits = RuntimeLimits(max_turns=2)
    manager.start_session(task_id=task.id)
    pending = manager.run_session(task.id)
    action_id = pending["actions"][0]["id"]

    raw = json.loads(mcp.config_path.read_text(encoding="utf-8"))
    raw["servers"]["fixture"]["visibility"]["risk"] = "passive"
    mcp.config_path.write_text(json.dumps(raw), encoding="utf-8")
    mcp.refresh()
    assert manager.control_session(task_id=task.id, action="approve_action", action_id=action_id)["accepted"] is True
    final = manager.run_session(task.id)
    action = final["actions"][0]
    assert action["status"] == "failed"
    assert action["result"]["error"]["code"] == "APPROVED_MCP_ROUTE_STALE"
    assert not any(event["type"] == "TOOL_EXECUTION_START" for event in final["agent_events"])


def test_rejected_mcp_action_returns_result_to_original_tool_call(tmp_path: Path) -> None:
    mcp = _approval_mcp(tmp_path)
    task = _mcp_approval_task("mcp_approval_reject", mcp)
    store = EvidenceStore(tmp_path / task.id / "evidence.db")
    store.create_task(task)
    store.close()
    client = MCPApprovalProvider()
    manager = Manager(run_root=tmp_path, model_client=client, mcp_manager=mcp)
    manager.limits = RuntimeLimits(max_turns=2)
    manager.start_session(task_id=task.id)
    pending = manager.run_session(task.id)
    action_id = pending["actions"][0]["id"]

    assert manager.control_session(task_id=task.id, action="reject_action", action_id=action_id)["accepted"] is True
    manager.run_session(task.id)
    transcript = json.loads((tmp_path / task.id / "solvers" / pending["session"]["active_solver_id"] / "session" / "messages.json").read_text(encoding="utf-8"))
    result = next(item for item in transcript if item.get("role") == "tool")
    assert result["tool_call_id"] == "mcp_high_impact_call"
    assert json.loads(result["content"])["error"]["code"] == "ACTION_REJECTED_BY_USER"
