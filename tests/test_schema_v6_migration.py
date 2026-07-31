from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

from scripts.migrate_schema_v5_to_v6 import migrate_database, verify_database
from tga.cli.main import main as cli_main
from tga.contracts import SessionRecord, TGATask
from tga.evidence.store import EvidenceStore
from tga.migrations.legacy_v5 import LegacyV5TaskReader
from tga.migrations.legacy_v5 import (
    LegacyEvidenceProjection,
    LegacyMemoryProjection,
    LegacyStrategyProjection,
)
from tga.migrations.evidence_models import LegacyArtifactRecord, LegacyFinding
from tga.migrations.legacy_models import MemoryEntry, StrategyCard
from tga.runtime.service import TaskRuntimeService, UnsupportedTaskSchemaError
import pytest
from fastapi.testclient import TestClient

from apps.api.main import app


def _create_v5_database(path) -> None:
    store = EvidenceStore(path)
    try:
        task = TGATask(id="legacy_1", name="Legacy", mode="ctf", goal="Solve")
        store.create_task(task)
        store.create_session(SessionRecord(task_id=task.id, schema_version=6))
        store.append_agent_event(task.id, "LEGACY_EVENT", {"value": 1})
    finally:
        store.close()
    legacy_task = task.model_copy(update={"schema_version": 5})
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE tasks SET payload_json=? WHERE id=?",
            (legacy_task.model_dump_json(), task.id),
        )
        connection.execute(
            "UPDATE sessions SET schema_version=5 WHERE task_id=?", (task.id,)
        )
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.execute("PRAGMA journal_mode=DELETE").fetchone()


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
    archive = json.loads(
        Path(first["legacy_runtime_archive"]).read_text(encoding="utf-8")
    )
    assert set(archive["tables"]) >= {
        "solvers", "memory_entries", "actions", "strategy_cards"
    }
    assert second["migrated"] is False and second["backup"] is None
    connection = sqlite3.connect(path)
    try:
        payload = connection.execute("SELECT payload_json FROM tasks").fetchone()[0]
        assert '"schema_version":6' in payload
        assert connection.execute("SELECT COUNT(*) FROM task_specs").fetchone()[0] == 1
        tables = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert not tables.intersection({
            "solvers", "memory_entries", "actions", "strategy_cards",
            "events", "action_results",
        })
    finally:
        connection.close()


def test_migration_cli_defaults_to_dry_run_and_requires_apply_to_publish(tmp_path) -> None:
    path = tmp_path / "evidence.db"
    report = tmp_path / "migration-report.json"
    _create_v5_database(path)
    script = Path(__file__).parents[1] / "scripts" / "migrate_schema_v5_to_v6.py"

    planned = subprocess.run(
        [
            sys.executable, str(script), "--db", str(path),
            "--report", str(report), "--dry-run", "--backup",
        ],
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


def test_formal_migrate_cli_exposes_dry_run_apply_and_verify(
    tmp_path, capsys
) -> None:
    path = tmp_path / "evidence.db"
    report = tmp_path / "migration-report.json"
    _create_v5_database(path)
    original = path.read_bytes()

    assert cli_main([
        "migrate", "--db", str(path), "--backup", "--dry-run",
        "--report", str(report),
    ]) == 0
    assert path.read_bytes() == original
    assert "DRY RUN" in capsys.readouterr().out

    assert cli_main([
        "migrate", "--db", str(path), "--apply", "--report", str(report),
    ]) == 0
    applied_output = capsys.readouterr().out
    assert "migration complete" in applied_output
    applied = json.loads(report.read_text(encoding="utf-8"))
    assert Path(applied["backup"]).read_bytes() == original

    migrated = path.read_bytes()
    assert cli_main(["migrate", "--db", str(path), "--verify"]) == 0
    assert "verification complete" in capsys.readouterr().out
    assert path.read_bytes() == migrated
    assert verify_database(path) == {
        **verify_database(path), "task_id": "legacy_1", "status": "verified",
        "verified": True, "integrity": "ok", "foreign_key_errors": 0,
    }


def test_verify_rejects_unmigrated_schema_without_mutation(tmp_path) -> None:
    path = tmp_path / "evidence.db"
    _create_v5_database(path)
    original = path.read_bytes()

    with pytest.raises(Exception) as raised:
        verify_database(path)

    assert getattr(raised.value, "code", None) == "VERIFY_SCHEMA_MISMATCH"
    assert path.read_bytes() == original


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


def test_runtime_service_rejects_v5_reads_and_commands_without_mutation(tmp_path) -> None:
    path = tmp_path / "runs" / "legacy_1" / "evidence.db"
    path.parent.mkdir(parents=True)
    _create_v5_database(path)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    service = TaskRuntimeService(run_root=tmp_path / "runs")

    with pytest.raises(UnsupportedTaskSchemaError):
        service.snapshot("legacy_1")
    with pytest.raises(UnsupportedTaskSchemaError):
        service.events("legacy_1")
    with pytest.raises(UnsupportedTaskSchemaError):
        service.command("pause_session", "legacy_1")
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_runtime_api_requires_offline_migration_for_every_v5_route(
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

    for response in (snapshot, events, solver_control, intent_retry):
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
    artifact = LegacyArtifactRecord(
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
