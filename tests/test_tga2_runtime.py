from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from apps.api.main import app
from tga2.agent.roles import LangChainAgentSuite, OfflineAgentSuite, RoutedAgentSuite
from tga2.agent.service import TaskRuntimeService
from tga2.bootstrap import get_container, reset_containers
from tga2.config import DEFAULT_KALI_IMAGE, DEFAULT_KALI_IMAGE_DIGEST
from tga2.core.models import CreateTaskRequest
from tga2.integrations.mcp import MCPConfig, MCPServer


class _VerifiedResponse:
    id = "verification-response"


class _VerificationModel:
    def invoke(self, _prompt: str) -> _VerifiedResponse:
        return _VerifiedResponse()


def test_offline_vertical_slice(tmp_path: Path) -> None:
    source = tmp_path / "target.txt"
    source.write_text("TGA2 evidence marker", encoding="utf-8")
    service = TaskRuntimeService(run_root=tmp_path / "runs")
    made = service.create_task(
        CreateTaskRequest(
            name="offline",
            objective="Inspect target",
            mode="vulnerability_research",
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
            "mode": "vulnerability_research",
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


def test_code_audit_mode_is_not_exposed_or_accepted(tmp_path: Path) -> None:
    reset_containers()
    app.state.container = get_container(tmp_path / "runs")
    client = TestClient(app)
    teams = client.get("/api/v2/catalog/teams").json()["items"]
    assert "code_audit" not in {item["mode"] for item in teams}
    rejected = client.post(
        "/api/v2/tasks",
        json={
            "name": "removed-mode",
            "mode": "code_audit",
            "goal": "must be rejected",
            "input": {"text": "", "fileIds": []},
        },
    )
    assert rejected.status_code == 422


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
                    "id": "vulnerability_research",
                    "label": "Vulnerability research",
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
    assert (
        app.state.container.configuration.runtime.mode_prompts[0]["id"]
        == "vulnerability_research"
    )
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


def test_kali_profile_uses_the_released_image_and_never_reverts_to_kali_rolling(
    tmp_path: Path,
) -> None:
    reset_containers()
    run_root = tmp_path / "runs"
    app.state.container = get_container(run_root)
    client = TestClient(app)

    profile = client.get("/api/v2/kali/profiles").json()["items"][0]
    assert profile["image"] == DEFAULT_KALI_IMAGE
    assert profile["image_digest"] == DEFAULT_KALI_IMAGE_DIGEST
    assert profile["enabled"] is True

    changed = client.put(
        "/api/v2/kali/profiles/tga2-kali",
        json={
            "enabled": True,
            "image": "ghcr.io/example/tga-kali:test",
            "expected_digest": None,
        },
    )
    assert changed.status_code == 200
    assert changed.json()["image"] == "ghcr.io/example/tga-kali:test"

    assigned = client.put(
        "/api/v2/solvers/worker/capabilities",
        json={
            "host_capability_overrides": {"add": [], "remove": []},
            "kali": {"profile_id": "tga2-kali", "capabilities": ["kali.exec"]},
        },
    )
    assert assigned.status_code == 200
    assert assigned.json()["kali"]["image_name"] == "ghcr.io/example/tga-kali"
    assert app.state.container.configuration.runtime.sandbox_image == (
        "ghcr.io/example/tga-kali:test"
    )

    reset_containers()
    assert get_container(run_root).configuration.runtime.sandbox_image == (
        "ghcr.io/example/tga-kali:test"
    )


def test_kali_health_verifies_the_configured_local_image_digest(
    tmp_path: Path, monkeypatch
) -> None:
    import apps.api.routes.catalog as catalog_routes

    reset_containers()
    app.state.container = get_container(tmp_path / "runs")
    client = TestClient(app)
    monkeypatch.setattr(catalog_routes.shutil, "which", lambda _name: "/usr/bin/docker")
    monkeypatch.setattr(
        catalog_routes.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                [
                    {
                        "Id": DEFAULT_KALI_IMAGE_DIGEST,
                        "RepoDigests": [],
                    }
                ]
            ),
            stderr="",
        ),
    )

    health = client.post("/api/v2/solvers/worker/kali-health/check")
    assert health.status_code == 200
    assert health.json()["status"] == "healthy"
    assert health.json()["image"] == DEFAULT_KALI_IMAGE
    assert health.json()["image_store"]["expected_digest"] == (
        DEFAULT_KALI_IMAGE_DIGEST
    )
    assert health.json()["image_store"]["actual_digest"] == (
        DEFAULT_KALI_IMAGE_DIGEST
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


def test_provider_registry_verifies_selected_provider_and_persists(
    tmp_path: Path, monkeypatch
) -> None:
    import apps.api.routes.settings as settings_routes
    import tga2.agent.service as service_module

    monkeypatch.setattr(settings_routes, "build_chat_model", lambda _settings: _VerificationModel())
    monkeypatch.setattr(service_module, "build_chat_model", lambda _settings: _VerificationModel())
    run_root = tmp_path / "runs"
    reset_containers()
    app.state.container = get_container(run_root)
    client = TestClient(app)

    created = client.post(
        "/api/v2/settings/llm/providers",
        json={
            "preset_id": "deepseek",
            "name": "My DeepSeek Gateway",
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-chat",
            "api_key": "deepseek-secret",
        },
    )
    assert created.status_code == 201
    provider = created.json()["provider"]
    assert provider["name"] == "My DeepSeek Gateway"
    assert provider["id"].startswith("provider_")
    model_id = provider["models"][0]["id"]

    verified = client.post(
        f"/api/v2/settings/llm/providers/{provider['id']}/models/{model_id}/verify"
    )
    assert verified.status_code == 200
    assert verified.json()["provider_name"] == "My DeepSeek Gateway"
    assert verified.json()["model"] == "deepseek-chat"
    assert isinstance(app.state.container.runtime.agents, LangChainAgentSuite)

    reset_containers()
    reloaded = get_container(run_root)
    stored = reloaded.configuration.model_registry.provider(provider["id"])
    assert stored.name == "My DeepSeek Gateway"
    assert stored.model(model_id).verification_status == "verified"


def test_agent_model_assignments_are_validated_and_routed(
    tmp_path: Path, monkeypatch
) -> None:
    import apps.api.routes.settings as settings_routes
    import tga2.agent.service as service_module

    monkeypatch.setattr(settings_routes, "build_chat_model", lambda _settings: _VerificationModel())
    monkeypatch.setattr(service_module, "build_chat_model", lambda _settings: _VerificationModel())
    reset_containers()
    app.state.container = get_container(tmp_path / "runs")
    client = TestClient(app)
    provider = client.post(
        "/api/v2/settings/llm/providers",
        json={
            "preset_id": "custom",
            "name": "Competition Gateway",
            "base_url": "https://models.example/v1",
            "model": "competition-model",
            "api_key": "competition-secret",
        },
    ).json()["provider"]
    model_id = provider["models"][0]["id"]
    assert client.post(
        f"/api/v2/settings/llm/providers/{provider['id']}/models/{model_id}/verify"
    ).status_code == 200
    assignments = {
        "supervisor": {"providerId": provider["id"], "modelId": model_id},
        "worker": {"providerId": "offline", "modelId": "offline"},
        "reviewer": {"providerId": provider["id"], "modelId": model_id},
        "reporter": {"providerId": "offline", "modelId": "offline"},
    }
    request = CreateTaskRequest(
        name="routed",
        objective="route models",
        mode="vulnerability_research",
        agent_models=assignments,
    )
    made = app.state.container.runtime.create_task(request)
    with app.state.container.runtime._runtime(made["task_id"]) as (_store, graph):
        routed = graph.dependencies.agents
        assert isinstance(routed, RoutedAgentSuite)
        assert isinstance(routed.roles["supervisor"], LangChainAgentSuite)
        assert isinstance(routed.roles["worker"], OfflineAgentSuite)


def test_cancelled_task_is_not_restarted_or_completed(tmp_path: Path) -> None:
    service = TaskRuntimeService(run_root=tmp_path / "runs")
    made = service.create_task(
        CreateTaskRequest(
            name="cancelled",
            objective="must not execute",
            mode="vulnerability_research",
        )
    )
    service.cancel_task(made["task_id"])
    result = service.run_task(made["task_id"])
    assert result["status"] == "cancelled"
    assert service.snapshot(made["task_id"])["session"]["status"] == "cancelled"


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
