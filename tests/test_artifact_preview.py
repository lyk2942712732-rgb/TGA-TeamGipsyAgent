from fastapi.testclient import TestClient

from apps.api.main import app
from tga.contracts import ExecutionPolicy, ResourceProvenance, SessionFile, SessionInput, SessionRecord, TGATask
from tga.evidence.artifacts import ArtifactStore
from tga.evidence.store import EvidenceStore


def _task(task_id: str) -> TGATask:
    return TGATask(
        id=task_id,
        name=task_id,
        mode="ctf",
        goal="test",
        mode_config={"mode": "ctf"},
        execution_policy=ExecutionPolicy(),
        session_input=SessionInput(),
        schema_version=5,
    )


def test_artifact_endpoint_returns_bounded_redacted_preview(tmp_path, monkeypatch):
    monkeypatch.setenv("TGA_RUN_ROOT", str(tmp_path / "runs"))
    task = _task("preview")
    root = tmp_path / "runs" / task.id
    store = EvidenceStore(root / "evidence.db")
    artifact = ArtifactStore(root / "workspace" / "artifacts").save_text(task_id=task.id, intent_id=None, kind="http_response", text="Authorization: Bearer very-secret-token\nCookie: sid=abc123\nbody=ok", tool="http.request", suffix=".txt")
    try:
        store.create_task(task); store.create_session(SessionRecord(task_id=task.id, schema_version=5, workspace_path="workspace")); store.add_artifact(artifact)
    finally:
        store.close()

    response = TestClient(app).get(f"/api/v2/tasks/{task.id}/artifacts/{artifact.id}")
    assert response.status_code == 200
    payload = response.json()
    assert payload["preview"].count("[REDACTED]") == 2
    assert "very-secret-token" not in payload["preview"]
    assert payload["download_url"] is None



def test_artifact_endpoint_is_scoped_to_a_v2_session(tmp_path, monkeypatch):
    monkeypatch.setenv("TGA_RUN_ROOT", str(tmp_path / "runs"))
    task_one = _task("one")
    task_two = _task("two")
    artifact_id = None
    for task in (task_one, task_two):
        root = tmp_path / "runs" / task.id
        store = EvidenceStore(root / "evidence.db")
        try:
            store.create_task(task); store.create_session(SessionRecord(task_id=task.id, schema_version=5, workspace_path="workspace"))
            artifact = ArtifactStore(root / "workspace" / "artifacts").save_text(task_id=task.id, intent_id=None, kind="file", text="same payload", tool="test")
            store.add_artifact(artifact)
            artifact_id = artifact.id
        finally:
            store.close()

    client = TestClient(app)
    assert client.get(f"/api/v2/tasks/one/artifacts/{artifact_id}").status_code == 200
    assert client.get(f"/api/v2/tasks/two/artifacts/{artifact_id}").status_code == 200


def test_schema_v4_artifact_endpoint_reads_workspace_artifacts(tmp_path, monkeypatch):
    monkeypatch.setenv("TGA_RUN_ROOT", str(tmp_path / "runs"))
    task_file = SessionFile(
        id=f"asset_{'a' * 32}", originalName="task.txt", storedName=f"{'a' * 32}.txt",
        relativePath=f"inputs/files/{'a' * 32}.txt", mimeType="text/plain", size=4,
        sha256="b" * 64, kind="task_input", mediaKind="text",
        provenance=ResourceProvenance(source="user_upload", original_name="task.txt"),
    )
    task = TGATask(
        id="preview_v4", name="preview v4", mode="reverse_engineering", goal="inspect",
        mode_config={"mode": "reverse_engineering"}, execution_policy=ExecutionPolicy(),
        session_input=SessionInput(files=[task_file]), schema_version=5,
    )
    root = tmp_path / "runs" / task.id
    store = EvidenceStore(root / "evidence.db")
    artifact = ArtifactStore(root / "workspace" / "artifacts").save_text(
        task_id=task.id, intent_id=None, kind="file", text="schema v4 artifact", tool="test",
    )
    try:
        store.create_task(task)
        store.create_session(SessionRecord(task_id=task.id, schema_version=5, workspace_path="workspace"))
        store.add_artifact(artifact)
    finally:
        store.close()

    response = TestClient(app).get(f"/api/v2/tasks/{task.id}/artifacts/{artifact.id}")
    assert response.status_code == 200
    assert response.json()["preview"] == "schema v4 artifact"
