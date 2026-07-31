from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from apps.api.main import app
from tga.contracts import SessionRecord, TGATask
from tga.domain.governance.models import ActionEffect
from tga.domain.solver.team_runtime import TeamRuntimeState
from tga.evidence.store import EvidenceStore
from tga.infrastructure.persistence import PersistenceBundle
from tga.runtime.orchestration import TaskOrchestrator
from tga.runtime.service import TaskRuntimeService
from tga.runtime.tooling.requests import (
    ActionContext,
    ApprovalRequest,
    AuthorizationDecision,
    GovernedAction,
)


def _seed_task(
    run_root: Path, *, task_id: str, status: str,
    approval_status: str | None = None, risk: str = "active",
    capability: str = "workspace.write", expires_in_hours: int = 24,
) -> dict[str, str]:
    task = TGATask(
        id=task_id, name=f"Task {task_id}", mode="ctf",
        goal="Exercise operational queries", schema_version=6,
    )
    store = EvidenceStore(run_root / task_id / "evidence.db")
    try:
        store.create_task(task)
        store.create_session(SessionRecord(
            task_id=task_id, status="paused" if status == "awaiting_input" else status,
            turn_count=2, max_turns=20,
            schema_version=6,
        ))
        repositories = PersistenceBundle(store)
        state = TaskOrchestrator(task=task, repositories=repositories).bootstrap()
        solver_id = state.supervisor_solver_id
        assert solver_id is not None
        repositories.solvers.update_solver_status(solver_id, "running")
        store.append_agent_event(task_id, "TEST_EVENT", {"summary": "operational"})
        orchestration_status = (
            "running" if status == "awaiting_approval" else status
        )
        if orchestration_status in {
            "running", "paused", "awaiting_input", "blocked",
            "completed", "failed", "cancelled",
        }:
            now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
            current_state = repositories.orchestration.get_state(task_id)
            assert current_state is not None
            repositories.orchestration.save_state(current_state.model_copy(update={
                "status": orchestration_status, "updated_at": now,
            }))
        if approval_status is None:
            return {"task_id": task_id, "solver_id": solver_id}
        action_id = f"action_{task_id}"
        approval_id = f"approval_{task_id}"
        expires_at = (
            datetime.now(UTC) + timedelta(hours=expires_in_hours)
        ).isoformat().replace("+00:00", "Z")
        effect = ActionEffect(
            scope="workspace", persistence="persistent",
            reversibility="reversible", category="file_write",
            description="Write a bounded result into the task workspace.",
        )
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        repositories.tool_governance.add_action(GovernedAction(
            id=action_id,
            context=ActionContext(
                task_id=task_id,
                solver_id=solver_id,
                orchestration_role="supervisor",
                solver_definition_id="task-supervisor",
                execution_policy_snapshot_id="execution:" + "a" * 64,
                solver_tool_policy_snapshot_id="tool:" + "b" * 64,
                created_at=now,
            ),
            provider_tool_name=capability.replace(".", "_"),
            tool_call_id=f"call_{task_id}",
            tool_class="execution",
            capability=capability,
            resolved_target="workspace/result.txt",
            rationale="Persist the reviewed result.",
            expected_outcome="A result file exists.",
            alternative_analysis="Return a dry-run preview instead.",
            risk=risk,
            effect=effect,
            authorization=AuthorizationDecision(
                allowed=True, reason="Persistent write requires operator approval.",
                requires_approval=True,
            ),
            status="pending_approval",
            created_at=now,
            updated_at=now,
        ))
        repositories.tool_governance.save_approval(ApprovalRequest(
            id=approval_id, task_id=task_id, solver_id=solver_id,
            action_id=action_id, governed_action_id=action_id,
            reason="Persistent write requires operator approval.", risk=risk,
            effect=effect, alternatives=("Return a dry-run preview.",),
            expires_at=expires_at, created_at=now, updated_at=now,
        ))
        if approval_status != "pending":
            repositories.tool_governance.decide_approval(
                action_id, approval_status, expected_status="pending"
            )
        return {
            "task_id": task_id, "solver_id": solver_id,
            "action_id": action_id, "approval_id": approval_id,
        }
    finally:
        store.close()


def test_dashboard_aggregates_real_status_without_runtime_snapshots(tmp_path, monkeypatch):
    run_root = tmp_path / "runs"
    monkeypatch.setenv("TGA_RUN_ROOT", str(run_root))
    _seed_task(run_root, task_id="running", status="running")
    _seed_task(run_root, task_id="approval", status="awaiting_approval", approval_status="pending")
    _seed_task(run_root, task_id="input", status="awaiting_input")
    _seed_task(run_root, task_id="blocked", status="blocked")

    monkeypatch.setattr(
        TaskRuntimeService, "runtime_snapshot",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Dashboard must not assemble Runtime snapshots")
        ),
    )
    response = TestClient(app).get("/api/v2/dashboard")

    assert response.status_code == 200
    payload = response.json()
    assert payload["metrics"] == {
        "running_tasks": 1, "pending_approvals": 1,
        "awaiting_user_input": 1, "blocked_tasks": 1,
        "active_solvers": 4,
    }
    assert {item["kind"] for item in payload["needs_attention"]} == {
        "approval", "user_input", "blocked",
    }
    assert "session" not in str(payload).lower()


def test_dashboard_skips_payloads_that_fail_the_current_v6_contract(tmp_path, monkeypatch):
    run_root = tmp_path / "runs"
    monkeypatch.setenv("TGA_RUN_ROOT", str(run_root))
    _seed_task(run_root, task_id="valid", status="running")
    _seed_task(run_root, task_id="invalid", status="running")
    db_path = run_root / "invalid" / "evidence.db"
    with sqlite3.connect(db_path) as connection:
        payload = json.loads(connection.execute(
            "SELECT payload_json FROM tasks WHERE id='invalid'"
        ).fetchone()[0])
        payload["target"] = "retired-field"
        connection.execute(
            "UPDATE tasks SET payload_json=? WHERE id='invalid'",
            (json.dumps(payload),),
        )

    response = TestClient(app).get("/api/v2/dashboard")

    assert response.status_code == 200
    body = response.json()
    assert [item["task_id"] for item in body["active_tasks"]] == ["valid"]
    sqlite_status = next(item for item in body["system_status"] if item["id"] == "sqlite")
    assert sqlite_status["status"] == "degraded"


def test_global_approvals_support_real_filters_and_pagination(tmp_path, monkeypatch):
    run_root = tmp_path / "runs"
    monkeypatch.setenv("TGA_RUN_ROOT", str(run_root))
    pending = _seed_task(
        run_root, task_id="pending", status="awaiting_approval",
        approval_status="pending", risk="destructive", capability="workspace.write",
    )
    _seed_task(
        run_root, task_id="approved", status="running",
        approval_status="approved", risk="active", capability="http.request",
    )
    monkeypatch.setattr(
        TaskRuntimeService, "runtime_snapshot",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Global approvals must not assemble Runtime snapshots")
        ),
    )

    client = TestClient(app)
    response = client.get("/api/v2/approvals", params={
        "status": "pending", "task_id": pending["task_id"],
        "solver_id": pending["solver_id"], "risk": "destructive",
        "capability": "workspace.write", "offset": 0, "limit": 1,
    })
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["next_offset"] is None
    assert payload["items"][0] == {
        **payload["items"][0],
        "task_id": "pending", "action_id": pending["action_id"],
        "risk": "destructive", "status": "pending",
        "decision_allowed": True,
    }
    assert "arguments" not in payload["items"][0]

    history = client.get("/api/v2/approvals", params={"status": "approved"}).json()
    assert history["total"] == 1
    assert history["items"][0]["decision_allowed"] is False


def test_elapsed_pending_approval_is_read_only_and_not_actionable(tmp_path, monkeypatch):
    run_root = tmp_path / "runs"
    monkeypatch.setenv("TGA_RUN_ROOT", str(run_root))
    _seed_task(
        run_root, task_id="elapsed", status="awaiting_approval",
        approval_status="pending", expires_in_hours=-1,
    )
    item = TestClient(app).get(
        "/api/v2/approvals", params={"status": "pending", "deadline": "overdue"}
    ).json()["items"][0]
    assert item["status"] == "pending"
    assert item["decision_allowed"] is False
    assert item["decision_block_reason"] == "审批已过期"
