from __future__ import annotations

import ast
import io
import json
import shutil
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from pydantic import Field

from apps.api.main import app
from tga2.agent.roles import LangChainAgentSuite, OfflineAgentSuite, RoutedAgentSuite
from tga2.agent.schemas import (
    PlanDraft,
    PlanIntentDraft,
    ReportDraft,
    ReviewDraft,
    SupervisorDecision,
    WorkerDraft,
)
from tga2.agent.service import TaskRuntimeService
from tga2.bootstrap import get_container, reset_containers
from tga2.config import DEFAULT_KALI_IMAGE, DEFAULT_KALI_IMAGE_DIGEST
from tga2.core.models import CreateTaskRequest, Task, TaskSpec
from tga2.integrations.mcp import MCPConfig, MCPServer
from tga2.integrations.model import ModelSettings


class _VerifiedResponse:
    id = "verification-response"


class _VerificationModel:
    def with_structured_output(self, _schema, **_kwargs):
        return self

    def invoke(self, _prompt):
        from tga2.agent.schemas import PlanDraft, PlanIntentDraft

        rendered = "\n".join(
            str(item.get("content", ""))
            if isinstance(item, dict)
            else str(getattr(item, "content", item))
            for item in _prompt
        )
        assert '"intents"' in rendered
        assert '"priority"' in rendered
        assert "do not rename fields" in rendered
        return {
            "raw": _VerifiedResponse(),
            "parsed": PlanDraft(
                summary="Verified",
                intents=[PlanIntentDraft(title="Inspect", objective="Inspect input")],
            ),
            "parsing_error": None,
        }


class _CheckpointSuite(OfflineAgentSuite):
    def __init__(self) -> None:
        self.reviews = 0
        self.worker_attempts: list[int] = []

    def plan(self, _task):
        return PlanDraft(
            summary="Two bounded intents",
            intents=[
                PlanIntentDraft(title="First", objective="Inspect first"),
                PlanIntentDraft(title="Second", objective="Inspect second"),
            ],
        )

    def work(self, task, intent, tools, feedback, middleware=()):
        self.worker_attempts.append(int(intent["attempt"]))
        return WorkerDraft(summary=f"Attempt {intent['attempt']}")

    def review(self, task, packet):
        self.reviews += 1
        if self.reviews == 1:
            return ReviewDraft(
                verdict="retry",
                reason_codes=["insufficient_evidence"],
                feedback="Collect stronger evidence.",
            )
        return ReviewDraft(verdict="pass", feedback="Accepted.")

    def decide(self, task, packet):
        verdict = (packet.review_result or {}).get("verdict")
        pending = [
            item for item in packet.plan["intents"] if item["status"] == "pending"
        ]
        if verdict == "retry":
            return SupervisorDecision(
                action="retry", reason="Retry once.", feedback="Use reviewer feedback."
            )
        return SupervisorDecision(
            action="next_intent" if pending else "finish",
            reason="Continue the bounded plan.",
        )

    def report(self, task, snapshot):
        return ReportDraft(executive_summary="Checkpoint flow completed.")


def test_langchain_json_mode_receives_the_full_pydantic_schema() -> None:
    suite = LangChainAgentSuite(_VerificationModel())
    draft = suite.plan(
        Task(
            name="schema",
            mode="ctf",
            spec=TaskSpec(objective="Verify schema delivery"),
        )
    )
    assert draft.intents[0].priority == 50


class _RecordingToolModel(BaseChatModel):
    tool_choices: list[object | None] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "recording-tool-model"

    def bind_tools(self, tools, *, tool_choice=None, **_kwargs):
        self.tool_choices.append(tool_choice)
        return self

    def _generate(self, _messages, stop=None, run_manager=None, **_kwargs):
        return ChatResult(
            generations=[
                ChatGeneration(
                    message=AIMessage(
                        content=(
                            "```json\n"
                            '{"summary":"Checked with ordinary tools.",'
                            '"claims":[],"limitations":[]}\n'
                            "```"
                        )
                    )
                )
            ]
        )


@tool
def _inspect_authorized_value(value: str) -> str:
    """Inspect an authorized test value."""

    return value


def test_deepseek_thinking_worker_does_not_force_tool_choice() -> None:
    model = _RecordingToolModel()
    suite = LangChainAgentSuite(
        model, force_prompt_worker_output=True
    )
    draft = suite.work(
        Task(
            name="deepseek tools",
            mode="ctf",
            spec=TaskSpec(objective="Use tools without forced tool_choice"),
        ),
        {"id": "intent-1", "objective": "Inspect target"},
        [_inspect_authorized_value],
        "",
    )

    assert model.tool_choices == [None]
    assert draft.summary == "Checked with ordinary tools."


def test_deepseek_thinking_capability_disables_forced_tool_choice() -> None:
    deepseek = ModelSettings(
        preset_id="deepseek",
        provider="openai",
        model="deepseek-v4-pro",
        base_url="https://api.deepseek.com",
        reasoning_mode="auto",
    )
    standard = ModelSettings(
        preset_id="openai",
        provider="openai",
        model="gpt-4.1-mini",
        reasoning_mode="disabled",
    )

    assert deepseek.supports_forced_tool_choice is False
    assert standard.supports_forced_tool_choice is True


class _AskUserSuite(OfflineAgentSuite):
    def __init__(self) -> None:
        self.decisions = 0

    def decide(self, task, packet):
        self.decisions += 1
        if self.decisions == 1:
            return SupervisorDecision(
                action="ask_user",
                reason="A target detail is missing.",
                user_question="Which authorized target should be used?",
            )
        return SupervisorDecision(action="finish", reason="User supplied the detail.")


@pytest.fixture(autouse=True)
def copy_tracked_configuration(tmp_path: Path) -> None:
    source = Path(__file__).parents[1] / "runs2" / ".config"
    shutil.copytree(source, tmp_path / "runs" / ".config")


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


def test_supervisor_checkpoint_retries_then_advances_plan(tmp_path: Path) -> None:
    service = TaskRuntimeService(run_root=tmp_path / "runs")
    suite = _CheckpointSuite()
    service.agents = suite
    made = service.create_task(
        CreateTaskRequest(
            name="checkpoint",
            objective="Exercise dynamic supervisor routing",
            mode="vulnerability_research",
        )
    )
    result = service.run_task(made["task_id"])
    snapshot = service.snapshot(made["task_id"])

    assert result["status"] == "completed"
    assert suite.worker_attempts == [1, 2, 1]
    assert snapshot["global_plan"]["version"] == 1
    assert len(snapshot["intents"]) == 2
    assert all(item["status"] == "completed" for item in snapshot["intents"])
    event_types = [item["type"] for item in snapshot["events"]]
    assert event_types.count("SUPERVISOR_DECIDED") == 3
    assert "INTENT_RETRY_REQUESTED" in event_types


def test_supervisor_user_input_interrupt_has_distinct_status_and_resumes(
    tmp_path: Path,
) -> None:
    service = TaskRuntimeService(run_root=tmp_path / "runs")
    suite = _AskUserSuite()
    service.agents = suite
    made = service.create_task(
        CreateTaskRequest(
            name="user input",
            objective="Exercise the explicit user-input checkpoint",
            mode="vulnerability_research",
        )
    )

    paused = service.run_task(made["task_id"])
    assert paused["status"] == "awaiting_user_input"
    assert paused["interrupts"][0]["kind"] == "user_input"
    assert service.snapshot(made["task_id"])["session"]["status"] == (
        "awaiting_user_input"
    )

    resumed = service.resume_task(
        made["task_id"], {"content": "Use 192.0.2.10 within the existing scope."}
    )
    assert resumed["status"] == "completed"
    event_types = [item["type"] for item in service.snapshot(made["task_id"])["events"]]
    assert "USER_INPUT_REQUIRED" in event_types
    assert "USER_INPUT_RECEIVED" in event_types


def test_runtime_json_is_the_single_budget_source(tmp_path: Path) -> None:
    service = TaskRuntimeService(run_root=tmp_path / "runs")
    runtime = service.configuration.runtime
    assert runtime.schema_version == 4
    assert runtime.budget.task.model_dump() == {
        "max_intents": 4,
        "max_model_calls": 60,
        "max_tool_calls": 80,
        "max_duration_minutes": 20,
    }
    assert runtime.budget.intent.max_attempts == 3
    assert runtime.budget.roles.worker.calls_per_attempt == 8
    assert runtime.budget.roles.worker.tool_calls_per_attempt == 15
    payload = json.loads((tmp_path / "runs" / ".config" / "runtime.json").read_text())
    assert "model_call_limit" not in payload["roles"]["worker"]
    assert "max_calls" not in payload["tool_defaults"]


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
    assert client.get(f"/api/v2/tasks/{created.json()['task_id']}").status_code == 200
    assert (
        client.get(f"/api/v2/tasks/{created.json()['task_id']}/session").status_code
        == 404
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


def test_resource_report_and_policy_catalogs_match_frontend_contracts(
    tmp_path: Path,
) -> None:
    reset_containers()
    app.state.container = get_container(tmp_path / "runs")
    client = TestClient(app)
    source = tmp_path / "target.txt"
    source.write_text("catalog evidence", encoding="utf-8")
    made = app.state.container.runtime.create_task(
        CreateTaskRequest(
            name="catalog task",
            objective="Inspect catalog evidence",
            mode="vulnerability_research",
            input_paths=[str(source)],
        )
    )
    app.state.container.runtime.run_task(made["task_id"])

    resources = client.get("/api/v2/catalog/resources").json()["items"]
    assert {item["kind"] for item in resources} == {
        "artifacts",
        "evidence",
        "findings",
    }
    assert all(
        {"id", "task_id", "task_name", "title", "status", "raw"} <= item.keys()
        for item in resources
    )

    reports = client.get("/api/v2/catalog/reports").json()["items"]
    assert reports == [
        {
            "id": f"report-{made['task_id']}",
            "task_id": made["task_id"],
            "task_name": "catalog task",
            "title": "catalog task 报告",
            "mode": "vulnerability_research",
            "status": "final",
            "findings": 1,
            "updated_at": reports[0]["updated_at"],
        }
    ]

    policies = client.get("/api/v2/catalog/policies").json()["items"]
    assert len({item["id"] for item in policies}) == len(policies)


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
        app.state.container.configuration.runtime.common_prompt == "competition prompt"
    )
    assert app.state.container.configuration.scene("vulnerability_research")["prompts"][
        "methodology"
    ] == ["Trace data flow"]
    created = client.post(
        "/api/v2/settings/skills",
        json={
            "name": "evidence",
            "description": "Evidence handling",
            "tags": ["evidence"],
            "version": "1",
            "instructions": "# Evidence\n\nRead the locator guide when needed.",
        },
    )
    assert created.status_code == 201
    added = client.put(
        "/api/v2/settings/skills/evidence/documents",
        json={"path": "references/locators.md", "content": "# Locators\nUse lines."},
    )
    assert added.status_code == 200
    package = app.state.container.runtime.skills.get_required("evidence")
    assert [item.path for item in package.documents] == [
        "SKILL.md",
        "references/locators.md",
    ]
    read = client.get(
        "/api/v2/settings/skills/evidence/documents/references/locators.md"
    )
    assert read.json()["document"]["content"] == "# Locators\nUse lines."
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
        not in app.state.container.configuration.runtime.roles["worker"].tools
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
    assert health.json()["image_store"]["actual_digest"] == (DEFAULT_KALI_IMAGE_DIGEST)


def test_mcp_config_translation() -> None:
    config = MCPConfig(
        servers={
            "demo": MCPServer(transport="stdio", command="python", args=["server.py"])
        }
    )
    assert config.adapter_connections() == {
        "demo": {"transport": "stdio", "command": "python", "args": ["server.py"]}
    }


def test_skill_zip_installs_one_directory_package_and_rejects_unsafe_paths(
    tmp_path: Path,
) -> None:
    reset_containers()
    app.state.container = get_container(tmp_path / "runs")
    client = TestClient(app)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "ctf-crypto/SKILL.md",
            "---\nname: ctf-crypto\ndescription: Crypto methods\n"
            "tags: [ctf, crypto]\nversion: 2\n---\n\n# Crypto\nRoute by topic.",
        )
        archive.writestr("ctf-crypto/rsa.md", "# RSA\nCheck key material.")
    imported = client.post(
        "/api/v2/settings/skills/import",
        content=buffer.getvalue(),
        headers={"content-type": "application/zip"},
    )
    assert imported.status_code == 201
    assert imported.json()["skill"]["file_count"] == 2
    assert (tmp_path / "runs" / ".config" / "skills" / "ctf-crypto" / "rsa.md").is_file()

    unsafe = io.BytesIO()
    with zipfile.ZipFile(unsafe, "w") as archive:
        archive.writestr("../SKILL.md", "# unsafe")
    rejected = client.post("/api/v2/settings/skills/import", content=unsafe.getvalue())
    assert rejected.status_code == 422


def test_provider_registry_verifies_selected_provider_and_persists(
    tmp_path: Path, monkeypatch
) -> None:
    import apps.api.routes.settings as settings_routes
    import tga2.agent.service as service_module

    monkeypatch.setattr(
        settings_routes, "build_chat_model", lambda _settings: _VerificationModel()
    )
    monkeypatch.setattr(
        service_module, "build_chat_model", lambda _settings: _VerificationModel()
    )
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
    assert isinstance(app.state.container.runtime.agents, RoutedAgentSuite)
    assert all(
        isinstance(suite, OfflineAgentSuite)
        for suite in app.state.container.runtime.agents.roles.values()
    )
    persisted = json.loads((run_root / ".config" / "models.json").read_text())
    assert persisted["providers"][0]["api_keys"][0]["api_key"] == "deepseek-secret"

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

    monkeypatch.setattr(
        settings_routes, "build_chat_model", lambda _settings: _VerificationModel()
    )
    monkeypatch.setattr(
        service_module, "build_chat_model", lambda _settings: _VerificationModel()
    )
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
    assert (
        client.post(
            f"/api/v2/settings/llm/providers/{provider['id']}/models/{model_id}/verify"
        ).status_code
        == 200
    )
    for role in ("supervisor", "reviewer"):
        changed = client.put(
            f"/api/v2/solvers/{role}/capabilities",
            json={
                "host_capability_overrides": {"add": [], "remove": []},
                "kali": None,
                "model": {"provider_id": provider["id"], "model_id": model_id},
            },
        )
        assert changed.status_code == 200
    request = CreateTaskRequest(
        name="routed",
        objective="route models",
        mode="vulnerability_research",
    )
    made = app.state.container.runtime.create_task(request)
    with app.state.container.runtime._runtime(made["task_id"]) as (_store, graph):
        routed = graph.dependencies.agents
        assert isinstance(routed, RoutedAgentSuite)
        assert isinstance(routed.roles["supervisor"], LangChainAgentSuite)
        assert isinstance(routed.roles["worker"], OfflineAgentSuite)
    runtime_payload = json.loads(
        (app.state.container.run_root / ".config" / "runtime.json").read_text()
    )
    assert runtime_payload["roles"]["supervisor"]["model"] == {
        "provider_id": provider["id"],
        "model_id": model_id,
    }


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
