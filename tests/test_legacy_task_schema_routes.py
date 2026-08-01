"""Legacy task databases must answer with the schema contract, not a crash.

`runs/` accumulates task databases written by older schema versions.  Those rows
cannot be validated by the current `TGATask` contract, so any read path that
validated before checking `schema_version` produced an HTTP 500 or leaked a
pydantic error dump.  Every task-scoped read must answer 409
SCHEMA_VERSION_UNSUPPORTED instead.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apps.api.main import app
from tga.runtime.service import (
    TaskRuntimeService,
    UnsupportedTaskSchemaError,
    require_current_task_payload,
)


LEGACY_TASK_ID = "task_legacy_v4"


def _seed_legacy_task(run_root: Path, *, schema_version: int | None) -> str:
    """Write a task row shaped like a pre-v6 install, including dropped fields."""
    task_root = run_root / LEGACY_TASK_ID
    task_root.mkdir(parents=True, exist_ok=True)
    payload: dict = {
        "id": LEGACY_TASK_ID,
        "name": "legacy ctf task",
        "mode": "ctf",
        "goal": "solve",
        # Fields the current contract forbids; they are why validation fails first.
        "target": "",
        "targets": [],
        "hints": [],
        "scope": [],
        "session_input": {"hint": {"text": "target host", "files": []}, "task_files": []},
    }
    if schema_version is not None:
        payload["schema_version"] = schema_version
    connection = sqlite3.connect(task_root / "evidence.db")
    try:
        connection.execute(
            "CREATE TABLE tasks (id TEXT PRIMARY KEY, payload_json TEXT NOT NULL, created_at TEXT)"
        )
        connection.execute(
            "INSERT INTO tasks (id, payload_json, created_at) VALUES (?,?,?)",
            (LEGACY_TASK_ID, json.dumps(payload, ensure_ascii=False), "2026-01-01T00:00:00Z"),
        )
        connection.commit()
    finally:
        connection.close()
    return LEGACY_TASK_ID


@pytest.fixture()
def legacy_client(tmp_path, monkeypatch) -> TestClient:
    run_root = tmp_path / "runs"
    run_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("TGA_RUN_ROOT", str(run_root))
    _seed_legacy_task(run_root, schema_version=4)
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize(
    "path",
    [
        "/api/v2/tasks/{task_id}",
        "/api/v2/tasks/{task_id}/inputs",
        "/api/v2/tasks/{task_id}/session",
        "/api/v2/tasks/{task_id}/team",
        "/api/v2/tasks/{task_id}/evidence?offset=0&limit=50",
        "/api/v2/tasks/{task_id}/timeline?after_seq=0&limit=50",
        "/api/v2/tasks/{task_id}/events?after_seq=0",
        "/api/v2/tasks/{task_id}/report",
    ],
)
def test_legacy_task_reads_answer_409_schema_contract(legacy_client, path):
    response = legacy_client.get(path.format(task_id=LEGACY_TASK_ID))

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "SCHEMA_VERSION_UNSUPPORTED"
    assert detail["schema_version"] == 4
    assert detail["required_schema_version"] == 6


def test_legacy_task_error_never_leaks_validation_internals(legacy_client):
    response = legacy_client.get(f"/api/v2/tasks/{LEGACY_TASK_ID}")

    body = response.text
    assert "validation error" not in body.casefold()
    assert "extra_forbidden" not in body
    assert "pydantic" not in body.casefold()


def test_task_list_skips_legacy_rows_without_failing(legacy_client):
    response = legacy_client.get("/api/v2/tasks")

    assert response.status_code == 200, response.text
    assert response.json()["tasks"] == []


def test_dashboard_and_approvals_ignore_legacy_rows(legacy_client):
    dashboard = legacy_client.get("/api/v2/dashboard")
    approvals = legacy_client.get("/api/v2/approvals?status=pending")

    assert dashboard.status_code == 200, dashboard.text
    assert dashboard.json()["active_tasks"] == []
    assert approvals.status_code == 200, approvals.text
    assert approvals.json()["items"] == []


@pytest.mark.parametrize(
    ("payload", "expected_version"),
    [
        ('{"schema_version": 4}', 4),
        ('{"schema_version": 5}', 5),
        # A pre-versioning row reports 0 rather than raising a JSON/attribute error.
        ('{"id": "x"}', 0),
        ('{"schema_version": "not-a-number"}', 0),
        ("[]", 0),
        ("not json at all", 0),
    ],
)
def test_require_current_task_payload_rejects_non_v6(payload, expected_version):
    with pytest.raises(UnsupportedTaskSchemaError) as error:
        require_current_task_payload(payload)

    assert error.value.schema_version == expected_version
    assert error.value.code == "SCHEMA_VERSION_UNSUPPORTED"


def test_require_current_task_payload_returns_v6_payload():
    payload = require_current_task_payload('{"schema_version": 6, "id": "task_one"}')

    assert payload["id"] == "task_one"


def test_task_definition_reports_schema_version_for_unversioned_row(tmp_path):
    run_root = tmp_path / "runs"
    run_root.mkdir(parents=True, exist_ok=True)
    _seed_legacy_task(run_root, schema_version=None)
    service = TaskRuntimeService(run_root=run_root)

    with pytest.raises(UnsupportedTaskSchemaError) as error:
        service.task_definition(LEGACY_TASK_ID)

    assert error.value.schema_version == 0
