from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

from scripts.migrate_schema_v5_to_v6 import migrate_database
from tga.contracts import SessionRecord, TGATask
from tga.evidence.store import EvidenceStore
from tga.infrastructure.persistence.legacy_v5 import LegacyV5TaskReader
from tga.infrastructure.persistence.legacy_v5 import (
    LegacyEvidenceProjection,
    LegacyMemoryProjection,
    LegacyStrategyProjection,
)
from tga.domain.evidence.legacy_models import ArtifactRecord, Finding as LegacyFinding
from tga.domain.solver.legacy_models import MemoryEntry, StrategyCard
from tga.runtime.service import TaskRuntimeService, UnsupportedTaskSchemaError
import pytest
from fastapi.testclient import TestClient

from apps.api.main import app


def _create_v5_database(path) -> None:
    store = EvidenceStore(path)
    try:
        task = TGATask(id="legacy_1", name="Legacy", mode="ctf", goal="Solve", schema_version=5)
        store.create_task(task)
        store.create_session(SessionRecord(task_id=task.id, schema_version=5))
        store.append_agent_event(task.id, "LEGACY_EVENT", {"value": 1})
    finally:
        store.close()


def test_legacy_v5_reader_is_read_only_and_supports_replay(tmp_path) -> None:
    path = tmp_path / "evidence.db"
    _create_v5_database(path)
    before = hashlib.sha256(path.read_bytes()).hexdigest()

    reader = LegacyV5TaskReader(path)
    try:
        snapshot = reader.snapshot("legacy_1")
        events = reader.replay("legacy_1", after_seq=0, limit=10)
    finally:
        reader.close()

    assert snapshot["task"]["schema_version"] == 5
    assert [event.type for event in events] == ["LEGACY_EVENT"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_v5_to_v6_migration_is_explicit_backed_up_and_idempotent(tmp_path) -> None:
    path = tmp_path / "evidence.db"
    _create_v5_database(path)
    original = path.read_bytes()

    dry_run = migrate_database(path)

    assert dry_run["dry_run"] is True
    assert dry_run["would_migrate"] is True
    assert dry_run["migrated"] is False
    assert dry_run["backup"] is None
    assert path.read_bytes() == original
    assert json.loads(Path(dry_run["report"]).read_text(encoding="utf-8"))["status"] == "planned"

    first = migrate_database(path, apply=True)
    second = migrate_database(path, apply=True)

    assert first["migrated"] is True
    assert first["dry_run"] is False
    assert first["source_schema"] == 5 and first["target_schema"] == 6
    assert first["backup"] and open(first["backup"], "rb").read() == original
    task_backup = Path(first["task_json_backup"])
    assert task_backup.is_file()
    assert json.loads(task_backup.read_text(encoding="utf-8"))["schema_version"] == 5
    applied_report = json.loads(Path(first["report"]).read_text(encoding="utf-8"))
    assert applied_report["status"] == "applied"
    assert applied_report["backup"] == first["backup"]
    assert second["migrated"] is False and second["backup"] is None
    connection = sqlite3.connect(path)
    try:
        payload = connection.execute("SELECT payload_json FROM tasks").fetchone()[0]
        assert '"schema_version":6' in payload
        assert connection.execute("SELECT COUNT(*) FROM task_specs").fetchone()[0] == 1
    finally:
        connection.close()


def test_migration_cli_defaults_to_dry_run_and_requires_apply_to_publish(tmp_path) -> None:
    path = tmp_path / "evidence.db"
    report = tmp_path / "migration-report.json"
    _create_v5_database(path)
    script = Path(__file__).parents[1] / "scripts" / "migrate_schema_v5_to_v6.py"

    planned = subprocess.run(
        [sys.executable, str(script), "--db", str(path), "--report", str(report)],
        cwd=script.parents[1], capture_output=True, text=True, check=False,
    )
    assert planned.returncode == 0
    assert "DRY RUN" in planned.stdout
    connection = sqlite3.connect(path)
    try:
        assert '"schema_version":5' in connection.execute(
            "SELECT payload_json FROM tasks"
        ).fetchone()[0]
    finally:
        connection.close()

    applied = subprocess.run(
        [sys.executable, str(script), "--db", str(path), "--report", str(report), "--apply"],
        cwd=script.parents[1], capture_output=True, text=True, check=False,
    )
    assert applied.returncode == 0
    assert "migration complete" in applied.stdout
    assert json.loads(report.read_text(encoding="utf-8"))["status"] == "applied"


def test_migration_failure_keeps_source_and_recovery_backups(tmp_path) -> None:
    path = tmp_path / "evidence.db"
    _create_v5_database(path)
    original = path.read_bytes()

    def fail(point: str) -> None:
        if point == "after_backup":
            raise RuntimeError("injected failure")

    with pytest.raises(RuntimeError, match="injected failure"):
        migrate_database(path, apply=True, fault_hook=fail)

    assert path.read_bytes() == original
    assert len(list(tmp_path.glob("evidence.db.v5-backup-*"))) == 1
    assert len(list(tmp_path.glob("evidence.db.task-json-v5-backup-*.json"))) == 1
    assert not list(tmp_path.glob(".evidence.db.schema-v6-*.tmp"))


def test_runtime_service_reads_v5_snapshot_but_refuses_v5_commands_without_mutation(tmp_path) -> None:
    path = tmp_path / "runs" / "legacy_1" / "evidence.db"
    path.parent.mkdir(parents=True)
    _create_v5_database(path)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    service = TaskRuntimeService(run_root=tmp_path / "runs")

    assert service.snapshot("legacy_1")["task"]["schema_version"] == 5
    assert service.events("legacy_1")[0]["type"] == "LEGACY_EVENT"
    with pytest.raises(UnsupportedTaskSchemaError):
        service.command("pause_session", "legacy_1")
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_phase9_api_replays_v5_but_rejects_v6_solver_and_intent_commands(
    tmp_path, monkeypatch
) -> None:
    run_root = tmp_path / "runs"
    path = run_root / "legacy_1" / "evidence.db"
    path.parent.mkdir(parents=True)
    _create_v5_database(path)
    monkeypatch.setenv("TGA_RUN_ROOT", str(run_root))
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    client = TestClient(app)

    snapshot = client.get("/api/v2/tasks/legacy_1/session")
    events = client.get("/api/v2/tasks/legacy_1/events?after_seq=0")
    solver_control = client.post(
        "/api/v2/tasks/legacy_1/solvers/legacy_solver/control",
        json={"action": "pause"},
    )
    intent_retry = client.post(
        "/api/v2/tasks/legacy_1/intents/legacy_intent/retry", json={}
    )

    assert snapshot.status_code == 200
    assert snapshot.json()["schema_version"] == 5
    assert events.status_code == 200
    assert events.json()["events"][0]["type"] == "LEGACY_EVENT"
    for response in (solver_control, intent_retry):
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "SCHEMA_VERSION_UNSUPPORTED"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_legacy_projections_never_infer_confirmation() -> None:
    memory = MemoryEntry(
        id="memory_1", task_id="legacy_1", kind="evidence", content="Unverified note",
        artifact_ids=["artifact_1"], source="legacy", created_at="old", updated_at="old",
    )
    projected = LegacyMemoryProjection().project_knowledge(
        memory, created_by_solver_id="legacy_solver"
    )
    artifact = ArtifactRecord(
        id="artifact_1", task_id="legacy_1", kind="tool_output",
        path="artifact.txt", sha256="a" * 64, created_at="old",
    )
    legacy_finding = LegacyFinding(
        id="finding_1", task_id="legacy_1", title="Possible issue", target="target",
        severity="medium", status="confirmed", evidence_artifact_id="artifact_1",
    )
    evidence = LegacyEvidenceProjection()
    claim, finding = evidence.project_finding(legacy_finding)
    plan, local = LegacyStrategyProjection().project(
        StrategyCard(
            id="strategy_1", task_id="legacy_1", title="Legacy idea",
            created_at="old", updated_at="old",
        ),
        solver_id="legacy_solver", intent_id="legacy_intent",
    )

    assert projected.status == "candidate" and projected.evidence_claim_ids == []
    assert evidence.project_artifact(artifact).legacy_import is True
    assert claim is not None and claim.status == "candidate"
    assert claim.locator.kind == "legacy_whole_artifact"
    assert finding.status == "candidate"
    assert plan.status == local.status == "draft" and plan.legacy_import is True
