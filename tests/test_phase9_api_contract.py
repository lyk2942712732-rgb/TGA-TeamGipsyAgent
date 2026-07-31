from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path

from fastapi.testclient import TestClient

from apps.api.main import app
from apps.api.routes import events as event_routes
from apps.api.routes import sessions as session_routes
from tga.contracts import SessionRecord, TGATask
from tga.domain.governance.models import ActionEffect
from tga.domain.evidence.artifacts import Artifact
from tga.evidence.store import EvidenceStore
from tga.infrastructure.persistence import PersistenceBundle
from tga.runtime.orchestration import TaskOrchestrator
from tga.runtime.tooling.governance.approvals import SolverApprovalCoordinator
from tga.runtime.tooling.requests import (
    ActionContext, ApprovalRequest, AuthorizationDecision, GovernedAction,
)


def _seed_team(tmp_path: Path, monkeypatch, task_id: str = "phase9_team") -> dict[str, str]:
    run_root = tmp_path / "runs"
    monkeypatch.setenv("TGA_RUN_ROOT", str(run_root))
    monkeypatch.setattr(session_routes, "_schedule_runtime_runner", lambda _task_id: False)
    task = TGATask(
        id=task_id,
        name="Phase 9 team",
        mode="ctf",
        goal="exercise the multi-solver API",
        schema_version=6,
        execution_budget={"max_active_workers": 2, "max_total_solvers": 8},
    )
    store = EvidenceStore(run_root / task.id / "evidence.db")
    try:
        store.create_task(task)
        store.create_session(SessionRecord(
            task_id=task.id,
            status="created",
            schema_version=6,
            workspace_path="workspace",
        ))
        repositories = PersistenceBundle(store)
        orchestrator = TaskOrchestrator(task=task, repositories=repositories)
        orchestrator.bootstrap()
        assignment = orchestrator.dispatch_next()
        assert assignment is not None
        state = orchestrator.state()
        return {
            "task_id": task.id,
            "supervisor_id": str(state.supervisor_solver_id),
            "worker_id": assignment.solver_id,
            "intent_id": assignment.intent_id,
        }
    finally:
        store.close()


def _add_approval(run_root: Path, ids: dict[str, str], suffix: str) -> str:
    store = EvidenceStore(run_root / ids["task_id"] / "evidence.db")
    try:
        action_id = f"action_{suffix}"
        governed_id = action_id
        now = "2026-07-30T00:00:00Z"
        PersistenceBundle(store).tool_governance.add_action(GovernedAction(
            id=action_id,
            context=ActionContext(
                task_id=ids["task_id"], solver_id=ids["worker_id"],
                intent_id=ids["intent_id"], orchestration_role="worker",
                solver_definition_id="recon-triage",
                execution_policy_snapshot_id="execution:" + "a" * 64,
                solver_tool_policy_snapshot_id="tool:" + "b" * 64,
                created_at=now,
            ),
            provider_tool_name="tga_workspace_write",
            tool_call_id=f"call_{suffix}",
            tool_class="execution",
            capability="workspace.write",
            resolved_target=f"workspace/{suffix}.txt",
            rationale="phase 9 approval projection",
            risk="active",
            effect=ActionEffect(
                scope="workspace",
                persistence="persistent",
                reversibility="reversible",
                category="file_write",
                description="Write a bounded workspace result.",
            ),
            authorization=AuthorizationDecision(
                allowed=True, reason="approval required", requires_approval=True,
            ),
            status="pending_approval", created_at=now, updated_at=now,
        ))
        PersistenceBundle(store).tool_governance.save_approval(ApprovalRequest(
            id=f"approval_{suffix}",
            task_id=ids["task_id"],
            solver_id=ids["worker_id"],
            intent_id=ids["intent_id"],
            action_id=action_id,
            governed_action_id=governed_id,
            reason="A persistent write requires operator approval.",
            risk="active",
            effect=ActionEffect(
                scope="workspace",
                persistence="persistent",
                reversibility="reversible",
                category="file_write",
                description="Write a bounded workspace result.",
            ),
            alternatives=("Return a dry-run preview.",),
            expires_at="2099-07-31T00:00:00Z",
            created_at=now,
            updated_at=now,
        ))
        return action_id
    finally:
        store.close()


def test_v6_runtime_snapshot_is_task_level_bounded_and_multi_solver(
    tmp_path: Path, monkeypatch
) -> None:
    ids = _seed_team(tmp_path, monkeypatch)
    store = EvidenceStore(tmp_path / "runs" / ids["task_id"] / "evidence.db")
    try:
        PersistenceBundle(store).evidence.add_artifact(Artifact(
            id="artifact_large_projection",
            task_id=ids["task_id"],
            intent_id=ids["intent_id"],
            kind="tool_output",
            path="large-secret-body.txt",
            sha256="a" * 64,
            created_at="2026-07-30T00:00:00Z",
            provenance={"large_body": "X" * 8_000},
        ))
        for index in range(130):
            store.append_agent_event(
                ids["task_id"],
                "PLAN_UPDATED",
                {
                    "operation": "test_projection",
                    "old_version": index + 1,
                    "new_version": index + 2,
                },
                solver_id=ids["supervisor_id"],
            )
    finally:
        store.close()

    payload = TestClient(app).get(
        f"/api/v2/tasks/{ids['task_id']}/session"
    ).json()

    assert payload["schema_version"] == 6
    assert set(payload) >= {
        "task", "session", "team", "solvers", "intents", "worker_results",
        "global_plan", "knowledge", "artifacts", "evidence_claims", "findings",
        "actions", "approvals", "retrieval_runs", "events", "events_page",
        "latest_seq",
    }
    assert payload["session"]["supervisor_solver_id"] == ids["supervisor_id"]
    assert payload["session"]["active_solver_count"] >= 1
    assert payload["session"]["max_active_workers"] == 2
    assert {item["orchestration_role"] for item in payload["solvers"]} == {
        "supervisor", "worker"
    }
    assert all("definition_id" in item and "budget_usage" in item for item in payload["solvers"])
    assert len(payload["events"]) <= 100
    assert payload["events_page"]["has_more"] is True
    event_seqs = [item["seq"] for item in payload["events"]]
    assert event_seqs == sorted(event_seqs)
    assert len(event_seqs) == len(set(event_seqs))
    assert event_seqs[-1] == payload["latest_seq"]
    assert "active_solver_id" not in payload["session"]
    encoded = json.dumps(payload)
    assert "large-secret-body.txt" not in encoded
    assert "X" * 1_000 not in encoded
    assert payload["artifacts"][0]["artifact_id"] == "artifact_large_projection"


def test_phase9_queries_are_paginated_and_enforce_task_ownership(
    tmp_path: Path, monkeypatch
) -> None:
    ids = _seed_team(tmp_path, monkeypatch, "phase9_queries")
    other = _seed_team(tmp_path, monkeypatch, "phase9_other")
    store = EvidenceStore(tmp_path / "runs" / ids["task_id"] / "evidence.db")
    try:
        for index in range(3):
            PersistenceBundle(store).evidence.add_artifact(Artifact(
                id=f"page_artifact_{index}",
                task_id=ids["task_id"],
                intent_id=ids["intent_id"],
                kind="tool_output",
                path=f"private/result-{index}.txt",
                sha256=str(index) * 64,
                created_at=f"2026-07-30T00:00:0{index}Z",
            ))
    finally:
        store.close()
    client = TestClient(app)

    team = client.get(f"/api/v2/tasks/{ids['task_id']}/team")
    solver = client.get(
        f"/api/v2/tasks/{ids['task_id']}/solvers/{ids['worker_id']}"
    )
    intents = client.get(
        f"/api/v2/tasks/{ids['task_id']}/intents?offset=0&limit=1"
    )
    evidence = client.get(
        f"/api/v2/tasks/{ids['task_id']}/evidence?offset=0&limit=1"
    )
    foreign = client.get(
        f"/api/v2/tasks/{ids['task_id']}/solvers/{other['worker_id']}"
    )

    assert team.status_code == 200 and team.json()["team"]["supervisor_solver_id"] == ids["supervisor_id"]
    assert solver.status_code == 200 and solver.json()["solver"]["solver_id"] == ids["worker_id"]
    assert intents.status_code == 200 and intents.json()["total"] >= 1
    assert intents.json()["limit"] == 1
    assert evidence.status_code == 200
    assert set(evidence.json()) >= {"artifacts", "evidence_claims", "findings"}
    artifact_page = evidence.json()["artifacts"]
    assert artifact_page["total"] == 3
    assert artifact_page["next_offset"] == 1
    assert len(artifact_page["items"]) == 1
    assert "path" not in artifact_page["items"][0]
    assert foreign.status_code == 404
    foreign_intervention = client.post(
        f"/api/v2/tasks/{ids['task_id']}/interventions",
        json={
            "kind": "hint",
            "content": "Cross-task targeting must fail.",
            "scope": "solver",
            "target_id": other["worker_id"],
        },
    )
    assert foreign_intervention.status_code == 404


def test_interventions_and_solver_intent_commands(
    tmp_path: Path, monkeypatch
) -> None:
    ids = _seed_team(tmp_path, monkeypatch, "phase9_commands")
    client = TestClient(app)

    intervention = client.post(
        f"/api/v2/tasks/{ids['task_id']}/interventions",
        json={"kind": "instruction", "content": "Prioritize the supplied binary.", "scope": "task"},
    )
    hint = client.post(
        f"/api/v2/tasks/{ids['task_id']}/hints",
        json={"content": "The parser output may contain a useful offset."},
    )
    paused = client.post(
        f"/api/v2/tasks/{ids['task_id']}/solvers/{ids['worker_id']}/control",
        json={"action": "pause"},
    )
    resumed = client.post(
        f"/api/v2/tasks/{ids['task_id']}/solvers/{ids['worker_id']}/control",
        json={"action": "resume"},
    )

    assert intervention.status_code == 200
    assert intervention.json()["intervention"]["kind"] == "instruction"
    assert hint.status_code == 404
    assert paused.json()["status"] == "paused"
    assert resumed.json()["status"] in {"queued", "ready"}

    store = EvidenceStore(tmp_path / "runs" / ids["task_id"] / "evidence.db")
    try:
        repositories = PersistenceBundle(store)
        repositories.solvers.update_solver_status(ids["worker_id"], "failed")
        repositories.plans.update_intent_status(
            ids["intent_id"], "failed", expected_status="running"
        )
    finally:
        store.close()
    retried = client.post(
        f"/api/v2/tasks/{ids['task_id']}/intents/{ids['intent_id']}/retry",
        json={},
    )
    assert retried.status_code == 200
    assert retried.json()["assignment"]["attempt"] == 2


def test_multiple_approvals_are_projected_and_one_decision_is_scoped(
    tmp_path: Path, monkeypatch
) -> None:
    ids = _seed_team(tmp_path, monkeypatch, "phase9_approvals")
    first = _add_approval(tmp_path / "runs", ids, "one")
    second = _add_approval(tmp_path / "runs", ids, "two")
    scoped = EvidenceStore(tmp_path / "runs" / ids["task_id"] / "evidence.db")
    try:
        SolverApprovalCoordinator(scoped).await_approval(
            solver_id=ids["worker_id"], intent_id=ids["intent_id"]
        )
    finally:
        scoped.close()
    client = TestClient(app)

    queue = client.get(f"/api/v2/tasks/{ids['task_id']}/approvals")
    assert queue.status_code == 200
    assert {item["action_id"] for item in queue.json()["items"]} == {first, second}
    assert all(set(item) >= {
        "solver_id", "intent_id", "action", "risk", "effect", "reason",
        "alternatives", "deadline",
    } for item in queue.json()["items"])

    decision = client.post(
        f"/api/v2/tasks/{ids['task_id']}/approvals/{first}/decision",
        json={"decision": "reject"},
    )
    assert decision.status_code == 200 and decision.json()["accepted"] is True
    remaining = client.get(
        f"/api/v2/tasks/{ids['task_id']}/approvals?status=pending"
    ).json()["items"]
    assert [item["action_id"] for item in remaining] == [second]
    scoped = EvidenceStore(tmp_path / "runs" / ids["task_id"] / "evidence.db")
    try:
        assert str(PersistenceBundle(scoped).solvers.get_solver(ids["worker_id"]).status) == "awaiting_approval"
    finally:
        scoped.close()


def test_event_envelope_has_intent_and_sse_reconnect_uses_db_then_event_bus(
    tmp_path: Path, monkeypatch
) -> None:
    ids = _seed_team(tmp_path, monkeypatch, "phase9_events")
    store = EvidenceStore(tmp_path / "runs" / ids["task_id"] / "evidence.db")
    try:
        first = store.append_agent_event(
            ids["task_id"],
            "INTENT_COMPLETED",
            {"intent_id": ids["intent_id"], "status": "completed"},
            solver_id=ids["worker_id"],
            intent_id=ids["intent_id"],
        )
    finally:
        store.close()

    page = TestClient(app).get(
        f"/api/v2/tasks/{ids['task_id']}/events?after_seq={first.seq - 1}&limit=1"
    ).json()
    timeline = TestClient(app).get(
        f"/api/v2/tasks/{ids['task_id']}/timeline?after_seq={first.seq - 1}&limit=1"
    ).json()
    event = page["events"][0]
    assert event["schema_version"] == 6
    assert event["intent_id"] == ids["intent_id"]
    assert event["payload"]["schema_version"] == 1
    assert page["next_after_seq"] == event["seq"]
    assert timeline == page

    class ConnectedRequest:
        async def is_disconnected(self) -> bool:
            return False

    async def receive_live_event() -> str:
        stream = event_routes._event_stream(
            ids["task_id"], ConnectedRequest(), cursor=first.seq
        )
        pending = asyncio.create_task(stream.__anext__())
        await asyncio.sleep(0.05)
        writer = EvidenceStore(tmp_path / "runs" / ids["task_id"] / "evidence.db")
        try:
            writer.append_agent_event(
                ids["task_id"], "SOLVER_PAUSED",
                {"solver_id": ids["worker_id"], "reason": "operator"},
                solver_id=ids["worker_id"],
            )
        finally:
            writer.close()
        try:
            return await asyncio.wait_for(pending, timeout=2)
        finally:
            await stream.aclose()

    chunk = asyncio.run(receive_live_event())
    assert "SOLVER_PAUSED" in chunk


def test_phase9_openapi_models_and_route_layer_has_no_database_dependency() -> None:
    schema = app.openapi()
    components = schema["components"]["schemas"]
    assert {
        "RuntimeSnapshotResponse", "InterventionRequest", "ApprovalDecisionRequest",
        "SolverControlRequest", "IntentRetryRequest", "EventEnvelope",
    }.issubset(components)

    route_root = Path(__file__).parents[1] / "apps" / "api" / "routes"
    forbidden = {"sqlite3", "tga.evidence.store", "tga.evidence.database"}
    violations: list[str] = []
    for path in route_root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            module = ""
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in forbidden:
                        violations.append(f"{path.name}:{alias.name}")
            if module in forbidden:
                violations.append(f"{path.name}:{module}")
    assert violations == []

    names = [
        "RuntimeSnapshotResponse", "InterventionRequest",
        "ApprovalDecisionRequest", "SolverControlRequest",
        "IntentRetryRequest", "EventEnvelope", "EventPage", "TeamResponse",
        "SolverResponse", "IntentPage", "EvidencePageResponse", "ApprovalPage",
    ]
    paths = [
        "/api/v2/tasks",
        "/api/v2/tasks/{task_id}/control",
        "/api/v2/tasks/{task_id}/interventions",
        "/api/v2/tasks/{task_id}/approvals/{action_id}/decision",
        "/api/v2/tasks/{task_id}/solvers/{solver_id}/control",
        "/api/v2/tasks/{task_id}/intents/{intent_id}/retry",
        "/api/v2/tasks/{task_id}/session",
        "/api/v2/tasks/{task_id}/team",
        "/api/v2/tasks/{task_id}/solvers/{solver_id}",
        "/api/v2/tasks/{task_id}/intents",
        "/api/v2/tasks/{task_id}/evidence",
        "/api/v2/tasks/{task_id}/approvals",
        "/api/v2/tasks/{task_id}/events",
        "/api/v2/tasks/{task_id}/events/stream",
        "/api/v2/tasks/{task_id}/timeline",
    ]
    contract = {
        "paths": {path: sorted(schema["paths"][path]) for path in paths},
        "schemas": {
            name: {
                "required": components[name].get("required", []),
                "properties": sorted(components[name].get("properties", {})),
                "additionalProperties": components[name].get("additionalProperties"),
            }
            for name in names
        },
    }
    expected = json.loads(
        (Path(__file__).parent / "snapshots" / "phase9_openapi_contract.json")
        .read_text(encoding="utf-8")
    )
    assert contract == expected
