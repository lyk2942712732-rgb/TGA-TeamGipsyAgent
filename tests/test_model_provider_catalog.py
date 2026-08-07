from __future__ import annotations

import json

from fastapi.testclient import TestClient

from apps.api.main import app
from tga.contracts import ExecutionPolicy
from tga.infrastructure.solver_definitions.registry import SolverDefinitionRegistry
from tga.infrastructure.team_templates.registry import TeamTemplateRegistry
from tga.models.bootstrap import build_model_client
from tga.models.provider_catalog import (
    add_provider_api_key,
    create_provider,
    list_provider_catalog,
    model_target_status,
    record_catalog_verification,
)
from tga.runtime.task_creation import CreateTaskCommand, TaskCreationService
from tga.tools.mcp_manager import MCPManager


def _configured_provider() -> tuple[str, str]:
    provider = create_provider(
        name="DeepSeek Team", preset_id="deepseek",
        base_url="https://api.deepseek.com", model="deepseek-chat",
        api_key="catalog-secret-1234",
    )
    model_id = provider["models"][0]["id"]
    record_catalog_verification(
        provider_id=provider["id"], model_id=model_id,
        capabilities={"chat_completions": True, "tool_calling": True, "vision": False},
        verification_id="verify_catalog_test",
    )
    return provider["id"], model_id


def test_provider_catalog_masks_keys_and_key_rotation_stales_models() -> None:
    provider_id, model_id = _configured_provider()
    public = list_provider_catalog()["providers"][0]

    assert public["models"][0]["verification_status"] == "verified"
    assert public["api_keys"][0]["masked"].endswith("1234")
    assert "catalog-secret-1234" not in json.dumps(public)

    add_provider_api_key(provider_id=provider_id, api_key="rotated-secret-5678", label="Rotation")
    status = model_target_status(provider_id=provider_id, model_id=model_id)
    assert status["api_key"] == "rotated-secret-5678"
    assert status["verification_status"] == "stale"


def test_catalog_snapshot_builds_the_selected_provider_client() -> None:
    provider_id, model_id = _configured_provider()
    status = model_target_status(provider_id=provider_id, model_id=model_id)

    from tga.domain.task.models import ModelSnapshot

    verification = status["verification"]
    snapshot = ModelSnapshot(
        provider=status["provider"], provider_id=provider_id,
        model=status["model"], model_id=model_id, api_key_id=status["api_key_id"],
        capability_fingerprint=verification["capability_fingerprint"],
        verification_id=verification["id"], verified_at=verification["verified_at"],
        capabilities=verification["capabilities"], max_output_tokens=status["max_output_tokens"],
        timeout_seconds=status["timeout_seconds"], temperature=status["temperature"],
        reasoning_mode=status["reasoning_mode"],
    )
    client = build_model_client(snapshot=snapshot)

    assert client is not None
    assert client.model == "deepseek-chat"
    assert client.api_key == "catalog-secret-1234"
    assert client.base_url == "https://api.deepseek.com"


def test_task_preflight_freezes_a_model_for_every_team_agent(tmp_path) -> None:
    provider_id, model_id = _configured_provider()
    definitions = SolverDefinitionRegistry.builtin()
    template = TeamTemplateRegistry.builtin(definitions=definitions).require("ctf")
    agent_ids = {
        template.supervisor_definition_id, template.reviewer_definition_id,
        template.reporter_definition_id, *template.available_solver_definition_ids,
    }
    manager = MCPManager(
        config_path=tmp_path / "missing-mcp.json", cache_path=tmp_path / "mcp-cache.json",
    )
    service = TaskCreationService(
        run_root=tmp_path / "runs", mcp_manager=manager, schedule=lambda _task_id: False,
    )
    command = CreateTaskCommand(
        task_id="agent_model_task", name="Agent model task", mode="ctf",
        goal="Analyze the challenge and preserve evidence", mode_options={"subtype": "web"},
        input_text="Inspect https://example.test", file_ids=[],
        execution_policy=ExecutionPolicy(),
        agent_models={
            agent_id: {"provider_id": provider_id, "model_id": model_id}
            for agent_id in agent_ids
        },
    )

    preflight = service.preflight(command)

    assert set(preflight.task.agent_model_snapshots) == agent_ids
    assert preflight.task.model_snapshot == preflight.task.agent_model_snapshots[template.supervisor_definition_id]
    assert preflight.checks[0]["detail"] == f"{len(agent_ids)} agent model snapshots verified"


def test_provider_api_never_returns_the_raw_key() -> None:
    client = TestClient(app)
    response = client.post("/api/v2/settings/llm/providers", json={
        "name": "Private Gateway", "preset_id": "custom",
        "base_url": "https://models.example.test/v1", "model": "security-model",
        "api_key": "http-secret-9876",
    })
    assert response.status_code == 201, response.text
    provider_id = response.json()["provider"]["id"]

    catalog = client.get("/api/v2/settings/llm/providers")
    assert catalog.status_code == 200
    encoded = catalog.text
    assert "http-secret-9876" not in encoded
    assert "9876" in encoded
    assert any(item["id"] == provider_id for item in catalog.json()["providers"])
