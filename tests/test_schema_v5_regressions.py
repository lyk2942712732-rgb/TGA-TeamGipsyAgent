from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tga.contracts import (
    ActionEffect,
    ActionSpec,
    CtfModeConfig,
    ExecutionPolicy,
    NetworkExecutionPolicy,
    SessionInput,
    SessionRecord,
    TGATask,
)
from tga.evidence.artifacts import ArtifactStore
from tga.evidence.store import EvidenceStore
from tga.infrastructure.persistence import PersistenceBundle
from tga.runtime.completion_validators import CompletionValidationContext, TaskCompletionSubmission, validator_for
from tga.runtime.coordinator import SessionCoordinator
from tga.runtime.approvals import expire_pending_approvals
from tga.runtime.manager import Manager
from tga.runtime.tooling.requests import (
    ActionContext,
    ApprovalRequest,
    AuthorizationDecision,
    GovernedAction,
)
from tga.domain.task.spec import TaskSpec
from tga.network_policy import authorize_url
from tga.tools.mcp_manager import MCPManager


def _mcp(tmp_path: Path) -> MCPManager:
    config = tmp_path / "mcp.json"
    config.write_text('{"version":1,"servers":{}}\n', encoding="utf-8")
    return MCPManager(config_path=config, cache_path=tmp_path / "mcp-cache.json")


def _pending_governed_action(
    store: EvidenceStore, task: TGATask, action_id: str, *, expires_at: str | None = None
) -> GovernedAction:
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    action = GovernedAction(
        id=action_id,
        context=ActionContext(
            task_id=task.id,
            solver_id="agent_main",
            intent_id=None,
            orchestration_role="supervisor",
            solver_definition_id="task-supervisor",
            execution_policy_snapshot_id="execution:" + "a" * 64,
            solver_tool_policy_snapshot_id="tool:" + "b" * 64,
            attempt=1,
            created_at=now,
        ),
        provider_tool_name="tga_http_request",
        tool_call_id=f"call_{action_id}",
        tool_class="execution",
        capability="http.request",
        normalized_arguments={"method": "DELETE", "url": "https://example.test/item"},
        resolved_target="https://example.test/item",
        rationale="remove test fixture",
        risk="destructive",
        effect=ActionEffect(
            scope="target",
            persistence="persistent",
            reversibility="uncertain",
            category="resource_delete",
            description="Delete the reviewed test fixture.",
        ),
        authorization=AuthorizationDecision(
            allowed=True,
            reason="operator approval required",
            requires_approval=True,
        ),
        semantic_fingerprint="c" * 64,
        status="pending_approval",
        created_at=now,
        updated_at=now,
    )
    governance = PersistenceBundle(store).tool_governance
    governance.add_action(action)
    governance.save_approval(ApprovalRequest(
        id=f"approval_{action_id}",
        task_id=task.id,
        solver_id="agent_main",
        action_id=action_id,
        governed_action_id=action_id,
        reason="operator approval required",
        risk="destructive",
        effect=action.effect,
        expires_at=expires_at or (datetime.now(UTC) + timedelta(minutes=15)).isoformat(),
        created_at=now,
        updated_at=now,
    ))
    return action


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
    PersistenceBundle(store).tasks.save_task_spec(
        TaskSpec(task_id=task.id, objective=task.goal)
    )
    store.close()
    client = CancelDuringProvider()
    manager = Manager(run_root=tmp_path, model_client=client, mcp_manager=_mcp(tmp_path))
    client.cancel = lambda: manager.control_session(task_id=task.id, action="cancel")

    assert manager.start_session(task_id=task.id)["accepted"] is True
    snapshot = manager.run_session(task.id)

    assert snapshot["session"]["status"] == "cancelled"
    assert snapshot["session"]["turn_count"] == 0
    assert snapshot["actions"] == []
    assert any(event["type"] == "PROVIDER_RESPONSE_DISCARDED" for event in snapshot["events"])
    assert not any(event["type"] == "TOOL_EXECUTION_START" for event in snapshot["events"])


def test_approval_requires_task_owned_pending_action(tmp_path: Path) -> None:
    task = _task("approval_owner")
    store = EvidenceStore(tmp_path / task.id / "evidence.db")
    store.create_task(task)
    PersistenceBundle(store).tasks.save_task_spec(
        TaskSpec(task_id=task.id, objective=task.goal)
    )
    store.create_session(SessionRecord(task_id=task.id, schema_version=6, status="running"))
    action = _pending_governed_action(store, task, "governed_pending")
    manager = Manager(store=store, run_root=tmp_path)

    missing = manager.control_session(task_id=task.id, action="approve_action", action_id="act_missing")
    assert missing == {"accepted": False, "status": "running", "reason": "action_not_found"}
    accepted = manager.control_session(task_id=task.id, action="approve_action", action_id=action.id)
    assert accepted["accepted"] is True
    assert accepted["status"] == "running"
    assert PersistenceBundle(store).tool_governance.get_approval_for_action(action.id)["status"] == "approved"
    repeated = manager.control_session(task_id=task.id, action="approve_action", action_id=action.id)
    assert repeated["accepted"] is False
    store.close()


def test_expired_approval_rejects_persisted_action_and_resumes_session(tmp_path: Path) -> None:
    task = _task("approval_expiry")
    store = EvidenceStore(tmp_path / task.id / "evidence.db")
    store.create_task(task)
    PersistenceBundle(store).tasks.save_task_spec(
        TaskSpec(task_id=task.id, objective=task.goal)
    )
    store.create_session(SessionRecord(task_id=task.id, schema_version=6, status="running"))
    deadline = (datetime.now(UTC) - timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
    action = _pending_governed_action(store, task, "governed_expired", expires_at=deadline)

    assert expire_pending_approvals(store, task.id) == [action.id]
    assert store.get_session(task.id).status == "running"  # type: ignore[union-attr]
    expired = PersistenceBundle(store).tool_governance.get_approval_for_action(action.id)
    assert expired is not None and expired["status"] == "expired"
    event = [item for item in store.list_agent_events(task.id) if item.type == "ACTION_APPROVAL_EXPIRED"][-1]
    assert event.payload["approval_expires_at"] == deadline
    store.close()


def test_cancel_terminates_pending_approval_action(tmp_path: Path) -> None:
    task = _task("approval_cancel")
    store = EvidenceStore(tmp_path / task.id / "evidence.db")
    store.create_task(task)
    PersistenceBundle(store).tasks.save_task_spec(
        TaskSpec(task_id=task.id, objective=task.goal)
    )
    store.create_session(SessionRecord(task_id=task.id, schema_version=6, status="running"))
    action = _pending_governed_action(store, task, "governed_cancelled")

    result = Manager(store=store, run_root=tmp_path).control_session(
        task_id=task.id, action="cancel"
    )

    assert result == {"accepted": True, "status": "cancelled"}
    cancelled = PersistenceBundle(store).tool_governance.get_action(action.id)
    assert cancelled is not None and cancelled["status"] == "cancelled"
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
        submission=TaskCompletionSubmission(
            summary="first flag", flag="CTF{first}", evidence_artifact_ids=[first.id],
        ),
    )
    two = validator_for("ctf").validate(
        context=context,
        submission=TaskCompletionSubmission(
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
