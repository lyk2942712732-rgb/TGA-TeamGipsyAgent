from __future__ import annotations

import ast
from pathlib import Path

from fastapi.testclient import TestClient

from apps.api.main import app
from tga2.agent.service import TaskRuntimeService
from tga2.bootstrap import get_container, reset_containers
from tga2.core.models import CreateTaskRequest
from tga2.integrations.mcp import MCPConfig, MCPServer


def test_offline_vertical_slice(tmp_path: Path) -> None:
    source = tmp_path / "target.txt"
    source.write_text("TGA2 evidence marker", encoding="utf-8")
    service = TaskRuntimeService(run_root=tmp_path / "runs")
    made = service.create_task(
        CreateTaskRequest(
            name="offline",
            objective="Inspect target",
            mode="code_audit",
            input_paths=[str(source)],
        )
    )
    result = service.run_task(made["task_id"])
    snapshot = service.snapshot(made["task_id"])
    assert result["status"] == "completed"
    assert snapshot["schema_version"] == 6
    assert len(snapshot["artifacts"]) == 1
    assert len(snapshot["evidence_claims"]) == 1
    assert len(snapshot["findings"]) == 1


def test_apps_api_is_the_only_http_boundary(tmp_path: Path) -> None:
    reset_containers()
    app.state.container = get_container(tmp_path / "runs")
    client = TestClient(app)
    created = client.post(
        "/api/v2/tasks",
        json={
            "id": "browser-id",
            "name": "browser",
            "mode": "code_audit",
            "goal": "Inspect target",
            "input": {"text": "Use evidence", "fileIds": []},
            "executionPolicy": {
                "network": {"access": "disabled"},
                "local_compute": {"mode": "disabled", "timeout_seconds": 120},
            },
        },
    )
    assert created.status_code == 201
    assert (
        client.get(f"/api/v2/tasks/{created.json()['task_id']}/session").status_code
        == 200
    )
    assert not (Path(__file__).parents[1] / "tga2" / "api.py").exists()


def test_prompt_skill_and_solver_settings_reach_runtime(tmp_path: Path) -> None:
    reset_containers()
    app.state.container = get_container(tmp_path / "runs")
    client = TestClient(app)
    prompt = client.put(
        "/api/v2/settings/agent-prompts",
        json={
            "schema_version": 1,
            "common_system_prompt": "competition prompt",
            "modes": [
                {
                    "id": "code_audit",
                    "label": "Code audit",
                    "methodology": ["Trace data flow"],
                    "completion_focus": "Require evidence",
                    "observer_focus": "Reject unsupported claims",
                }
            ],
        },
    )
    assert prompt.status_code == 200
    assert (
        app.state.container.configuration.runtime.prompts["common"]
        == "competition prompt"
    )
    assert app.state.container.configuration.runtime.mode_prompts[0]["id"] == "code_audit"
    imported = client.post(
        "/api/v2/settings/skills/import",
        content=b"evidence audit",
        headers={"x-tga-filename": "evidence.md"},
    )
    assert imported.status_code == 201
    assert app.state.container.runtime.skills.get("evidence") is not None
    selected = app.state.container.runtime.skills.select(
        "unrelated objective", selected_names=["evidence"]
    )
    assert [item.name for item in selected] == ["evidence"]
    solver = client.put(
        "/api/v2/solvers/worker/capabilities",
        json={
            "host_capability_overrides": {"add": [], "remove": ["save_note"]},
            "kali": None,
        },
    )
    assert solver.status_code == 200
    assert (
        "save_note"
        not in app.state.container.configuration.runtime.solver_tools["worker"]
    )


def test_mcp_config_translation() -> None:
    config = MCPConfig(
        servers={
            "demo": MCPServer(transport="stdio", command="python", args=["server.py"])
        }
    )
    assert config.adapter_connections() == {
        "demo": {"transport": "stdio", "command": "python", "args": ["server.py"]}
    }


def test_builtin_skills_are_real_and_read_only(tmp_path: Path) -> None:
    reset_containers()
    app.state.container = get_container(tmp_path / "runs")
    client = TestClient(app)
    catalog = client.get("/api/v2/settings/skills").json()["skills"]
    assert any(item["name"] == "code-audit" for item in catalog)
    assert client.delete("/api/v2/settings/skills/code-audit").status_code == 409


def test_tga2_has_no_legacy_or_fastapi_imports() -> None:
    root = Path(__file__).parents[1] / "tga2"
    violations = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [item.name for item in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                if name in {"tga", "fastapi"} or name.startswith(("tga.", "fastapi.")):
                    violations.append(f"{path}: {name}")
    assert violations == []
