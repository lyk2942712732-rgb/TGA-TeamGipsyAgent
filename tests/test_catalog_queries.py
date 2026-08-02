from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apps.api.main import app
from tga.contracts import TGATask
from tests.runtime_fixtures import task as v6_task
from tga.evidence.store import EvidenceStore
from tga.modes import TASK_MODES, default_execution_policy


CATALOG_KINDS = (
    "resources", "reports", "knowledge-bases", "teams", "solvers",
    "policies", "skills",
)


def _store(run_root: Path, task_id: str, *, schema_version: int = 6) -> EvidenceStore:
    store = EvidenceStore(run_root / task_id / "evidence.db")
    task = v6_task(
        id=task_id, name=f"Task {task_id}", mode="ctf",
        goal="Exercise catalog queries",
    )
    store.create_task(task)
    if schema_version != 6:
        historical = task.model_copy(update={"schema_version": schema_version})
        store.conn.execute(
            "UPDATE tasks SET payload_json=? WHERE id=?",
            (historical.model_dump_json(), task_id),
        )
        store.conn.commit()
    return store


def _insert_resource(
    store: EvidenceStore, *, task_id: str, identifier: str, schema_version: int
) -> None:
    payload = {
        "id": identifier, "task_id": task_id, "kind": "tool_output",
        "path": f"artifacts/{identifier}.txt", "sha256": "a" * 64,
        "created_at": "2026-07-30T00:00:00Z",
    }
    store.conn.execute(
        "INSERT INTO artifacts(id,task_id,payload_json,created_at,schema_version) "
        "VALUES (?,?,?,?,?)",
        (identifier, task_id, json.dumps(payload), payload["created_at"], schema_version),
    )
    store.conn.commit()


@pytest.mark.parametrize("kind", CATALOG_KINDS)
def test_all_catalog_kinds_have_stable_bounded_api_shape(
    tmp_path, monkeypatch, kind: str
) -> None:
    monkeypatch.setenv("TGA_RUN_ROOT", str(tmp_path / "runs"))

    response = TestClient(app).get(
        f"/api/v2/catalog/{kind}", params={"offset": 0, "limit": 2}
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        **payload, "view_version": 1, "kind": kind, "supported": True,
        "reason": None, "offset": 0, "limit": 2,
    }
    assert len(payload["items"]) <= 2
    assert isinstance(payload["total"], int)
    assert isinstance(payload["errors"], list)
    if kind in {"resources", "reports", "knowledge-bases"}:
        assert payload["items"] == [] and payload["total"] == 0
    else:
        assert payload["total"] > 0


def test_resource_catalog_filters_schema_v5_rows_and_supports_search_paging(
    tmp_path, monkeypatch
) -> None:
    run_root = tmp_path / "runs"
    monkeypatch.setenv("TGA_RUN_ROOT", str(run_root))
    store = _store(run_root, "current")
    try:
        _insert_resource(
            store, task_id="current", identifier="artifact_alpha", schema_version=6
        )
        _insert_resource(
            store, task_id="current", identifier="artifact_beta", schema_version=6
        )
        _insert_resource(
            store, task_id="current", identifier="artifact_legacy", schema_version=5
        )
    finally:
        store.close()
    legacy = _store(run_root, "legacy", schema_version=5)
    try:
        _insert_resource(
            legacy, task_id="legacy", identifier="artifact_v5_task", schema_version=5
        )
    finally:
        legacy.close()

    client = TestClient(app)
    first = client.get("/api/v2/catalog/resources", params={"limit": 1}).json()
    second = client.get(
        "/api/v2/catalog/resources", params={"offset": 1, "limit": 1}
    ).json()
    searched = client.get(
        "/api/v2/catalog/resources", params={"query": "beta", "limit": 10}
    ).json()

    assert first["total"] == 2 and first["next_offset"] == 1
    assert second["total"] == 2 and second["next_offset"] is None
    assert {first["items"][0]["id"], second["items"][0]["id"]} == {
        "artifact_alpha", "artifact_beta",
    }
    assert [item["id"] for item in searched["items"]] == ["artifact_beta"]
    assert "artifact_legacy" not in json.dumps(first) + json.dumps(second)
    assert "artifact_v5_task" not in json.dumps(first) + json.dumps(second)


def test_catalog_reports_invalid_database_and_resource_columns_without_paths(
    tmp_path, monkeypatch
) -> None:
    run_root = tmp_path / "runs"
    monkeypatch.setenv("TGA_RUN_ROOT", str(run_root))
    broken = run_root / "broken" / "evidence.db"
    broken.parent.mkdir(parents=True)
    broken.write_bytes(b"not sqlite")

    incompatible = run_root / "incompatible" / "evidence.db"
    incompatible.parent.mkdir(parents=True)
    connection = sqlite3.connect(incompatible)
    connection.executescript(
        "CREATE TABLE tasks(id TEXT PRIMARY KEY,payload_json TEXT NOT NULL);"
        "CREATE TABLE artifacts(id TEXT PRIMARY KEY,task_id TEXT,payload_json TEXT);"
    )
    connection.execute(
        "INSERT INTO tasks(id,payload_json) VALUES (?,?)",
        ("incompatible", json.dumps({"schema_version": 6})),
    )
    connection.commit()
    connection.close()

    response = TestClient(app).get("/api/v2/catalog/resources")

    assert response.status_code == 200
    errors = response.json()["errors"]
    assert {(item["task_id"], item["code"]) for item in errors} == {
        ("broken", "CATALOG_DATABASE_INVALID"),
        ("incompatible", "CATALOG_DATABASE_INVALID"),
    }
    encoded = json.dumps(errors)
    assert str(tmp_path) not in encoded
    assert "incompatible schema" in encoded


def test_knowledge_base_catalog_deduplicates_stable_ids_across_task_databases(
    tmp_path, monkeypatch
) -> None:
    run_root = tmp_path / "runs"
    monkeypatch.setenv("TGA_RUN_ROOT", str(run_root))
    payload = {
        "id": "kb_shared", "name": "Shared documentation",
        "owner": {"scope": "global", "workspace_id": None, "task_id": None, "solver_id": None},
        "description": "Verified shared corpus", "status": "active", "metadata": {},
        "created_at": "2026-07-30T00:00:00Z", "updated_at": None,
    }
    for task_id in ("task_a", "task_b"):
        store = _store(run_root, task_id)
        try:
            store.conn.execute(
                "INSERT INTO knowledge_bases(id,owner_scope,status,payload_json,created_at) "
                "VALUES (?,?,?,?,?)",
                (payload["id"], "global", "active", json.dumps(payload), payload["created_at"]),
            )
            store.conn.commit()
        finally:
            store.close()

    result = TestClient(app).get("/api/v2/catalog/knowledge-bases").json()

    assert result["total"] == 1
    assert result["items"] == [payload]


def test_report_catalog_lists_only_schema_v6_exported_reports(tmp_path, monkeypatch) -> None:
    run_root = tmp_path / "runs"
    monkeypatch.setenv("TGA_RUN_ROOT", str(run_root))
    current = _store(run_root, "current")
    current.close()
    report = run_root / "current" / "reports" / "report.md"
    report.parent.mkdir(parents=True)
    report.write_text("# Verified report", encoding="utf-8")
    legacy = _store(run_root, "legacy", schema_version=5)
    legacy.close()
    legacy_report = run_root / "legacy" / "reports" / "report.md"
    legacy_report.parent.mkdir(parents=True)
    legacy_report.write_text("# Legacy report", encoding="utf-8")

    result = TestClient(app).get(
        "/api/v2/catalog/reports", params={"query": "current"}
    ).json()

    assert result["total"] == 1
    assert result["items"][0]["id"] == "report:current"
    assert "legacy" not in json.dumps(result["items"])


def test_unknown_catalog_kind_has_structured_404(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("TGA_RUN_ROOT", str(tmp_path / "runs"))

    response = TestClient(app).get("/api/v2/catalog/unknown")

    assert response.status_code == 404
    assert response.json()["detail"] == {
        "code": "CATALOG_KIND_NOT_FOUND",
        "message": "catalog kind is not available",
        "kind": "unknown",
    }


def test_policy_catalog_projects_one_real_execution_policy_per_mode(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("TGA_RUN_ROOT", str(tmp_path / "runs"))

    payload = TestClient(app).get(
        "/api/v2/catalog/policies", params={"limit": 200}
    ).json()

    assert payload["total"] == len(TASK_MODES)
    by_mode = {item["mode"]: item for item in payload["items"]}
    assert set(by_mode) == set(TASK_MODES)
    for mode in TASK_MODES:
        record = by_mode[mode]
        expected = default_execution_policy(mode)
        assert record["id"] == f"{mode}-execution-policy"
        assert record["editable"] is False
        assert record["preset"] == expected.preset
        assert record["execution_policy"] == expected.model_dump(mode="json")


def test_policy_catalog_search_narrows_to_one_mode(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TGA_RUN_ROOT", str(tmp_path / "runs"))

    payload = TestClient(app).get(
        "/api/v2/catalog/policies", params={"query": "penetration_test"}
    ).json()

    assert [item["mode"] for item in payload["items"]] == ["penetration_test"]
