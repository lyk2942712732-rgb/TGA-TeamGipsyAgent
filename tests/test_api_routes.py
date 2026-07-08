from fastapi.testclient import TestClient

from apps.api.main import app


def test_api_create_task_runs_and_writes_report(tmp_path, monkeypatch):
    monkeypatch.setenv("TGA_RUN_ROOT", str(tmp_path / "runs"))
    client = TestClient(app)

    response = client.post(
        "/api/tasks",
        json={
            "task": {
                "id": "task_api",
                "name": "api-demo",
                "mode": "ctf",
                "target": "http://127.0.0.1:1",
                "scope": ["127.0.0.1:1"],
                "intensity": "normal",
                "allow_active_scan": False,
                "goal": "solve",
                "flag_format": "flag\\{[^}]+\\}",
            }
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["task_id"] == "task_api"
    assert (tmp_path / "runs" / "task_api" / "reports" / "report.md").exists()

    report_response = client.get("/api/tasks/task_api/report")
    assert report_response.status_code == 200
    assert "# TGA Report" in report_response.text
