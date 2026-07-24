from __future__ import annotations

import base64
import hashlib
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apps.api.main import app
from apps.api.routes import tasks as task_routes
from tga.contracts import ExecutionPolicy, ResourceRef, SessionRecord, TGATask
from tga.evidence.store import EvidenceStore
from tga.inputs import InputLimits, SessionWorkspace, cleanup_expired_staged_inputs, safe_original_name
from tga.models.bootstrap import build_model_client
from tga.tools.mcp_manager import MCPManager
from tests.runtime_fixtures import configure_verified_model


PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _configured_api(monkeypatch, tmp_path: Path) -> TestClient:
    monkeypatch.setenv("TGA_RUN_ROOT", str(tmp_path / "runs"))
    configure_verified_model(monkeypatch)
    monkeypatch.setattr(task_routes, "_schedule_runtime_runner", lambda _task_id: False)
    import tga.runtime.manager as runtime_manager

    runtime_manager._manager = None
    return TestClient(app)


def _upload(client: TestClient, name: str, data: bytes, mime: str = "application/octet-stream") -> dict:
    response = client.post(
        "/api/v2/input-uploads",
        params={"filename": name},
        headers={"Content-Type": mime},
        content=data,
    )
    assert response.status_code == 201, response.text
    return response.json()["asset"]


def _create_payload(
    task_id: str,
    *,
    mode: str = "reverse_engineering",
    task_ids: list[str] | None = None,
    hint_text: str | None = None,
    hint_ids: list[str] | None = None,
    policy: ExecutionPolicy | None = None,
) -> dict:
    return {
        "id": task_id,
        "name": task_id,
        "mode": mode,
        "goal": "analyze the supplied material",
        "modeOptions": {"mode": mode},
        "input": {"text": hint_text or "", "fileIds": [*(task_ids or []), *(hint_ids or [])]},
        "executionPolicy": (policy or ExecutionPolicy()).model_dump(mode="json"),
    }


def _mcp_manager(tmp_path: Path) -> MCPManager:
    fixture = Path(__file__).parent / "fixtures" / "fake_mcp_server.py"
    config = tmp_path / "mcp.json"
    config.write_text(
        json.dumps({"version": 1, "servers": {"fixture": {"transport": "stdio", "stdio": {"source": "local_process", "command": sys.executable, "args": [str(fixture)]}, "visibility": {"risk": "passive"}}}}),
        encoding="utf-8",
    )
    manager = MCPManager(config_path=config, cache_path=tmp_path / "mcp-cache.json")
    manager.refresh()
    return manager


def test_schema_v5_uploads_are_archived_with_structured_metadata_and_survive_restart(tmp_path, monkeypatch):
    client = _configured_api(monkeypatch, tmp_path)
    task_asset = _upload(client, "sample.bin", b"\x00\x01sample-binary", "text/plain")
    hint_asset = _upload(client, "topology.png", PNG, "text/plain")

    response = client.post(
        "/api/v2/tasks",
        json=_create_payload(
            "binary_only",
            task_ids=[task_asset["id"]],
            hint_text="Inspect the image too.",
            hint_ids=[hint_asset["id"]],
        ),
    )

    assert response.status_code == 200, response.text
    snapshot = client.get("/api/v2/tasks/binary_only/session").json()
    task_file, hint_file = snapshot["task"]["session_input"]["files"]
    assert task_file["original_name"] == "sample.bin"
    assert task_file["sha256"] == hashlib.sha256(b"\x00\x01sample-binary").hexdigest()
    assert task_file["mime_type"] == "application/octet-stream"
    assert hint_file["mime_type"] == "image/png"  # Browser MIME is not authoritative.
    assert hint_file["media_kind"] == "image"
    root = tmp_path / "runs" / "binary_only" / "workspace"
    assert (root / task_file["relative_path"]).read_bytes() == b"\x00\x01sample-binary"
    assert (root / hint_file["relative_path"]).read_bytes() == PNG
    assert not (tmp_path / "runs" / "_input_staging" / task_asset["id"].removeprefix("asset_")).exists()

    store = EvidenceStore(tmp_path / "runs" / "binary_only" / "evidence.db")
    try:
        restarted = TGATask.model_validate(store.task_snapshot("binary_only")["task"])
        session = store.get_session("binary_only")
        assert restarted.schema_version == 5
        assert restarted.session_input.files[0].sha256 == task_file["sha256"]
        assert session and session.workspace_path == "workspace"
    finally:
        store.close()


def test_schema_v5_api_lists_reads_and_searches_workspace_inputs(tmp_path, monkeypatch):
    client = _configured_api(monkeypatch, tmp_path)
    asset = _upload(client, "notes.txt", b"first\nneedle\nlast", "application/octet-stream")
    assert client.post("/api/v2/tasks", json=_create_payload("readable", task_ids=[asset["id"]])).status_code == 200

    manifest = client.get("/api/v2/tasks/readable/inputs").json()
    assert manifest["files"][0]["relative_path"].startswith("inputs/files/")
    assert client.get(f"/api/v2/tasks/readable/inputs/{asset['id']}/read", params={"limit": 5}).json()["content"] == "first"
    matches = client.get(f"/api/v2/tasks/readable/inputs/{asset['id']}/search", params={"query": "needle"}).json()["matches"]
    assert matches == [{"line": 2, "text": "needle"}]


def test_same_named_files_never_overwrite(tmp_path, monkeypatch):
    client = _configured_api(monkeypatch, tmp_path)
    first = _upload(client, "same.txt", b"first")
    second = _upload(client, "same.txt", b"second")
    response = client.post("/api/v2/tasks", json=_create_payload("same_names", task_ids=[first["id"], second["id"]]))
    assert response.status_code == 200
    files = client.get("/api/v2/tasks/same_names/session").json()["task"]["session_input"]["files"]
    assert len({item["stored_name"] for item in files}) == 2
    workspace = tmp_path / "runs" / "same_names" / "workspace"
    assert {(workspace / item["relative_path"]).read_bytes() for item in files} == {b"first", b"second"}


def test_upload_and_claim_limits_return_structured_errors(tmp_path, monkeypatch):
    client = _configured_api(monkeypatch, tmp_path)
    monkeypatch.setenv("TGA_INPUT_MAX_FILE_BYTES", "3")
    too_large = client.post("/api/v2/input-uploads", params={"filename": "large.bin"}, content=b"1234")
    assert too_large.status_code == 413
    assert too_large.json()["detail"] == {
        "code": "FILE_TOO_LARGE", "message": "input exceeds per-file size limit", "field": "file", "limit": 3,
    }

    monkeypatch.setenv("TGA_INPUT_MAX_FILE_BYTES", "10")
    monkeypatch.setenv("TGA_INPUT_MAX_TOTAL_BYTES", "5")
    first = _upload(client, "one.txt", b"123")
    second = _upload(client, "two.txt", b"456")
    failed = client.post("/api/v2/tasks", json=_create_payload("total_limit", task_ids=[first["id"], second["id"]]))
    assert failed.status_code == 422
    assert failed.json()["detail"]["code"] == "SESSION_CREATE_FAILED"
    assert not (tmp_path / "runs" / "total_limit").exists()


def test_file_count_limit_is_enforced_at_session_claim(tmp_path, monkeypatch):
    client = _configured_api(monkeypatch, tmp_path)
    first = _upload(client, "one.txt", b"one")
    second = _upload(client, "two.txt", b"two")
    monkeypatch.setenv("TGA_INPUT_MAX_FILES", "1")

    response = client.post("/api/v2/tasks", json=_create_payload("count_limit", task_ids=[first["id"], second["id"]]))

    assert response.status_code == 422
    assert "file count limit" in response.json()["detail"]["message"]
    assert not (tmp_path / "runs" / "count_limit").exists()
    staging = tmp_path / "runs" / "_input_staging"
    assert (staging / first["id"].removeprefix("asset_")).is_dir()
    assert (staging / second["id"].removeprefix("asset_")).is_dir()


def test_upload_rejects_traversal_and_dangerous_names(tmp_path, monkeypatch):
    client = _configured_api(monkeypatch, tmp_path)
    for name in ("../escape.txt", "CON", "folder/file", "name?.txt"):
        response = client.post("/api/v2/input-uploads", params={"filename": name}, content=b"x")
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "INVALID_FILENAME"
        with pytest.raises(ValueError):
            safe_original_name(name)


def test_hint_can_be_empty_but_mode_rules_require_context(tmp_path, monkeypatch):
    client = _configured_api(monkeypatch, tmp_path)
    task_asset = _upload(client, "task.txt", b"question")
    empty_hint = client.post("/api/v2/tasks", json=_create_payload("empty_hint", mode="ctf", task_ids=[task_asset["id"]], hint_text="   "))
    assert empty_hint.status_code == 200

    no_task = client.post("/api/v2/tasks", json=_create_payload("no_task", mode="ctf", hint_text="only a hint"))
    assert no_task.status_code == 200

    incident = client.post("/api/v2/tasks", json=_create_payload("hint_incident", mode="incident_response", hint_text="Investigate host A"))
    assert incident.status_code == 200
    empty_incident = client.post("/api/v2/tasks", json=_create_payload("empty_incident", mode="incident_response"))
    assert empty_incident.status_code == 422
    assert "at least one task input file or prompt" in empty_incident.json()["detail"]["message"]


def test_failed_creation_removes_partial_workspace_but_keeps_staging_for_retry(tmp_path, monkeypatch):
    client = _configured_api(monkeypatch, tmp_path)
    asset = _upload(client, "retry.txt", b"retry me")
    invalid = ExecutionPolicy.model_validate({"preset": "custom", "network": {"access": "custom", "interaction": "observe", "custom_origins": ["not an origin"]}})
    failed = client.post("/api/v2/tasks", json=_create_payload("retry_task", task_ids=[asset["id"]], policy=invalid))
    stage = tmp_path / "runs" / "_input_staging" / asset["id"].removeprefix("asset_")
    assert failed.status_code == 422
    assert not (tmp_path / "runs" / "retry_task").exists()
    assert stage.is_dir()

    succeeded = client.post("/api/v2/tasks", json=_create_payload("retry_task", task_ids=[asset["id"]]))
    assert succeeded.status_code == 200
    assert not stage.exists()


def test_unexpected_runtime_start_failure_removes_partial_session_and_keeps_staging(tmp_path, monkeypatch):
    client = _configured_api(monkeypatch, tmp_path)
    asset = _upload(client, "unexpected.txt", b"retry after exception")

    def fail_start(*_args, **_kwargs):
        raise RuntimeError("runtime startup failed")

    monkeypatch.setattr(task_routes.TaskRuntimeService, "create_task", fail_start)
    with pytest.raises(RuntimeError, match="runtime startup failed"):
        client.post("/api/v2/tasks", json=_create_payload("unexpected_failure", task_ids=[asset["id"]]))

    assert not (tmp_path / "runs" / "unexpected_failure").exists()
    assert (tmp_path / "runs" / "_input_staging" / asset["id"].removeprefix("asset_")).is_dir()


def test_expired_staging_is_swept_but_recent_retry_assets_remain(tmp_path):
    root = tmp_path / "staging"
    now = datetime.now(UTC)
    for token, age_hours in (("a" * 32, 48), ("b" * 32, 1)):
        stage = root / token
        stage.mkdir(parents=True)
        (stage / "manifest.json").write_text(json.dumps({"created_at": (now - timedelta(hours=age_hours)).isoformat()}), encoding="utf-8")
    assert cleanup_expired_staged_inputs(root, now=now, ttl_seconds=24 * 60 * 60) == 1
    assert not (root / ("a" * 32)).exists()
    assert (root / ("b" * 32)).is_dir()


def test_old_request_fields_are_rejected(tmp_path, monkeypatch):
    client = _configured_api(monkeypatch, tmp_path)
    manager = _mcp_manager(tmp_path)
    monkeypatch.setattr(task_routes, "_catalog_runner", lambda: manager)
    asset = _upload(client, "task.txt", b"material")
    payload = _create_payload("deprecated", mode="ctf", task_ids=[asset["id"]])
    payload.update({
        "targetUrls": ["https://outside.example"],
        "references": ["repo://forged"],
        "mcpServiceGrants": ["forged"],
        "mcpMethodGrants": ["forged.danger"],
        "mcp_servers": ["forged"],
    })
    response = client.post("/api/v2/tasks", json=payload)
    assert response.status_code == 422
    assert all(item["type"] == "extra_forbidden" for item in response.json()["detail"])


def test_pre_v4_sessions_are_rejected_without_mutating_the_database(tmp_path, monkeypatch):
    monkeypatch.setenv("TGA_RUN_ROOT", str(tmp_path / "runs"))
    task_id = "legacy_read"
    store = EvidenceStore(tmp_path / "runs" / task_id / "evidence.db")
    try:
        payload = {
            "id": task_id, "name": "old schema", "mode": "ctf", "goal": "read old schema",
            "mode_config": {"mode": "ctf"}, "execution_policy": {}, "schema_version": 3,
        }
        store.conn.execute(
            "INSERT INTO tasks(id,payload_json,created_at) VALUES (?, ?, ?)",
            (task_id, json.dumps(payload), datetime.now(UTC).isoformat()),
        )
        store.conn.commit()
        store.create_session(SessionRecord(task_id=task_id, schema_version=3))
    finally:
        store.close()
    client = TestClient(app)
    for method, path in (
        (client.get, "/api/v2/tasks/legacy_read/session"),
        (client.get, "/api/v2/tasks/legacy_read/inputs"),
        (client.get, "/api/v2/tasks/legacy_read/events"),
        (lambda value: client.post(value, json={}), "/api/v2/tasks/legacy_read/start"),
    ):
        response = method(path)
        assert response.status_code == 409
        assert response.json()["detail"] == {
            "code": "SCHEMA_VERSION_UNSUPPORTED",
            "message": "task schema 3 is not executable; migrate it to schema 5",
            "schema_version": 3,
            "required_schema_version": 5,
        }

    # Opening a legacy database through the current store is itself rejected;
    # verify the raw payload remains intact without letting the runtime mutate it.
    import sqlite3
    connection = sqlite3.connect(tmp_path / "runs" / task_id / "evidence.db")
    try:
        raw = connection.execute("SELECT payload_json FROM tasks WHERE id=?", (task_id,)).fetchone()[0]
        assert json.loads(raw)["schema_version"] == 3
        assert connection.execute("SELECT status FROM sessions WHERE task_id=?", (task_id,)).fetchone()[0] == "created"
    finally:
        connection.close()


def test_model_vision_capability_can_be_declared_explicitly(monkeypatch):
    monkeypatch.setenv("TGA_LLM_BASE_URL", "https://model.test/v1")
    monkeypatch.setenv("TGA_LLM_API_KEY", "key")
    monkeypatch.setenv("TGA_LLM_MODEL", "text-only")
    monkeypatch.setenv("TGA_LLM_SUPPORTS_VISION", "false")
    assert build_model_client().supports_vision is False


def test_model_runtime_temperature_is_configurable_and_bounded(monkeypatch):
    monkeypatch.setenv("TGA_LLM_API_KEY", "test-key")
    monkeypatch.setenv("TGA_LLM_BASE_URL", "https://model.test/v1")
    monkeypatch.setenv("TGA_LLM_MODEL", "test-model")
    monkeypatch.setenv("TGA_LLM_TEMPERATURE", "0")
    assert build_model_client().temperature == 0

    monkeypatch.setenv("TGA_LLM_TEMPERATURE", "9")
    assert build_model_client().temperature == 2

    monkeypatch.setenv("TGA_LLM_TEMPERATURE", "invalid")
    assert build_model_client().temperature == 0.2
