from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from dataclasses import replace
import hashlib
import pytest

from fastapi.testclient import TestClient

from apps.api.main import app
from apps.api.routes import tasks as task_routes

from tga.contracts import ExecutionPolicy, TGATask
from tga.evidence.store import EvidenceStore
from tga.infrastructure.persistence import PersistenceBundle
from tga.rag import NullRAGRetriever
from tga.rag.retrieval import RAGQuery
from tga.runtime.prompts import build_agent_system_prompt
from tga.runtime.service import TaskRuntimeService
from tga.runtime.task_creation import CreateTaskCommand, TaskCreationService
from tga.domain.skills.models import (
    SkillSnapshot as CurrentSkillSnapshot,
    TaskCommonSkillSnapshot,
)
from tga.skills.loader import Skill
from tga.skills.models import SkillBundleSnapshot, SkillSnapshot
from tga.skills.retrieval import RetrievedSkill, SkillRetrievalQuery
from tga.skills.selection import (
    MAX_SKILL_CONTEXT_CHARS,
    SkillSelectionRequest,
    SkillSelector,
)
from tga.skills.store import SkillStore


class StaticRetriever:
    retriever_id = "static-test-v1"

    def __init__(self, values: list[RetrievedSkill]) -> None:
        self.values = values

    def retrieve(self, query: SkillRetrievalQuery) -> list[RetrievedSkill]:
        return [item for item in self.values if query.mode in item.skill.modes]


class EmptyMCPManager:
    config = SimpleNamespace(servers={})

    def ensure_catalog(self):
        return SimpleNamespace(version="mcp_empty", routes=[], servers=[])


class AcceptingManager:
    def start_session(self, *, task_id: str, initial_hint: str | None = None) -> dict:
        return {"accepted": True, "status": "created"}


def _skill(name: str, *, body: str, capabilities: list[str], tags: list[str]) -> Skill:
    return Skill(
        name=name,
        version="1",
        modes=["ctf"],
        capabilities=capabilities,
        tags=tags,
        source=f"/{name}.md",
        body=body,
    )


def test_selector_is_deterministic_filters_missing_capabilities_and_bounds_context() -> None:
    values = [
        RetrievedSkill(_skill("z-unavailable", body="ignored", capabilities=["missing.tool"], tags=["web"]), "custom"),
        RetrievedSkill(_skill("b-web", body="B" * 20_000, capabilities=["http.request"], tags=["web"]), "builtin"),
        RetrievedSkill(_skill("a-web", body="A" * 20_000, capabilities=["http.request"], tags=["web"]), "custom"),
        RetrievedSkill(_skill("c-web", body="C" * 20_000, capabilities=["http.request"], tags=["web"]), "builtin"),
    ]
    selector = SkillSelector(StaticRetriever(values))
    request = SkillSelectionRequest(
        mode="ctf",
        goal="Inspect this web challenge",
        prompt="https://example.test",
        mode_config={"subtype": "web"},
        available_capabilities=("http.request",),
    )

    first = selector.select(request)
    second = selector.select(request)

    assert first == second
    assert [item.name for item in first.skills] == ["a-web", "b-web"]
    assert first.total_chars <= MAX_SKILL_CONTEXT_CHARS
    assert "z-unavailable" not in {item.name for item in first.skills}
    assert all("上下文预算已截断" in item.body for item in first.skills)
    assert all(item.content_sha256 == hashlib.sha256(item.body.encode("utf-8")).hexdigest() for item in first.skills)


def test_manual_selector_preserves_user_order_and_rejects_invalid_choices() -> None:
    values = [
        RetrievedSkill(_skill("first-web", body="first", capabilities=["http.request"], tags=["web"]), "custom"),
        RetrievedSkill(_skill("second-web", body="second", capabilities=[], tags=["web"]), "builtin"),
    ]
    selector = SkillSelector(StaticRetriever(values))
    selected = selector.select(SkillSelectionRequest(
        mode="ctf", goal="web", available_capabilities=("http.request",),
        selected_skill_names=("second-web", "first-web"),
    ))
    assert selected.selector.endswith(":manual")
    assert [item.name for item in selected.skills] == ["second-web", "first-web"]
    assert all(item.selection_reasons == ["用户在创建任务时手动选择"] for item in selected.skills)

    with pytest.raises(ValueError, match="unavailable capabilities"):
        selector.select(SkillSelectionRequest(
            mode="ctf", goal="web", available_capabilities=(), selected_skill_names=("first-web",),
        ))
    with pytest.raises(ValueError, match="do not exist or are incompatible"):
        selector.select(SkillSelectionRequest(
            mode="ctf", goal="web", available_capabilities=("http.request",), selected_skill_names=("missing",),
        ))


def test_task_creation_freezes_custom_skill_and_injects_body_into_system_prompt(tmp_path: Path, monkeypatch) -> None:
    custom_root = tmp_path / "custom-skills"
    monkeypatch.setenv("TGA_CUSTOM_SKILLS_ROOT", str(custom_root))
    original_body = "# Custom workflow\nCUSTOM_SKILL_RUNTIME_MARKER\nPreserve bounded HTTP evidence."
    custom_root.mkdir(parents=True)
    (custom_root / "custom-runtime-skill.md").write_text(
        "---\n"
        "name: custom-runtime-skill\n"
        'version: "1"\n'
        "modes: [ctf]\n"
        "capabilities: [http.request]\n"
        "tags: [web, recon]\n"
        "---\n"
        f"{original_body}\n",
        encoding="utf-8",
    )
    runtime = TaskRuntimeService(run_root=tmp_path / "runs", manager=AcceptingManager())
    service = TaskCreationService(
        run_root=tmp_path / "runs",
        mcp_manager=EmptyMCPManager(),  # type: ignore[arg-type]
        schedule=lambda _task_id: False,
        runtime_service=runtime,
        model_status=lambda: {
            "configured": True,
            "verification_status": "verified",
            "provider": "test",
            "model": "test-model",
            "verification": {
                "id": "verify-1",
                "verified_at": "2026-01-01T00:00:00Z",
                "capability_fingerprint": "f" * 64,
                "capabilities": {},
            },
            "max_output_tokens": 4096,
            "timeout_seconds": 60,
            "temperature": 0.0,
        },
    )

    execution_policy = ExecutionPolicy()
    execution_policy.network.access = "public_internet"
    command = CreateTaskCommand(
        task_id="skill_runtime_task",
        name="Skill runtime task",
        mode="ctf",
        goal="Inspect the web target and preserve evidence",
        mode_options={"subtype": "web"},
        input_text="https://example.test/",
        file_ids=[],
        execution_policy=execution_policy,
        selected_skill_names=("custom-runtime-skill",),
    )
    preflight = service.preflight(command)
    created = service.create(replace(command, preflight_fingerprint=preflight.fingerprint))

    store = EvidenceStore(tmp_path / "runs" / created.task_id / "evidence.db")
    try:
        task = store.get_task(created.task_id)
        assert task is not None
        assert "skill_bundle_snapshot" not in task.model_dump(mode="json")
        common = PersistenceBundle(store).tasks.get_task_common_skill_snapshot(created.task_id)
        assert common is not None and common.legacy_import is False
        selected = next(item for item in common.skills if item.name == "custom-runtime-skill")
        assert selected.body == original_body
        assert common.selector.startswith("task-common:")
        event = next(item for item in store.list_agent_events(created.task_id) if item.type == "SKILLS_SNAPSHOTTED")
        assert event.payload["scope"] == "task_common"
        assert any(item["name"] == "custom-runtime-skill" for item in event.payload["skills"])
    finally:
        store.close()

    SkillStore(custom_root).update(
        "custom-runtime-skill",
        modes=["ctf"],
        capabilities=["http.request"],
        tags=["web"],
        version="2",
        body="# Changed\nNEW_BODY_MUST_NOT_REPLACE_TASK_SNAPSHOT",
    )
    store = EvidenceStore(tmp_path / "runs" / created.task_id / "evidence.db")
    try:
        frozen = store.get_task(created.task_id)
        assert frozen is not None
        common = PersistenceBundle(store).tasks.get_task_common_skill_snapshot(created.task_id)
        prompt = build_agent_system_prompt(frozen, task_common=common)
        assert "CUSTOM_SKILL_RUNTIME_MARKER" in prompt
        assert "NEW_BODY_MUST_NOT_REPLACE_TASK_SNAPSHOT" not in prompt
    finally:
        store.close()


def test_skill_bundle_is_rendered_in_first_system_message_contract() -> None:
    body = "SYSTEM_SKILL_BODY_MARKER"
    task = TGATask(
        id="skill_prompt_task",
        name="Skill prompt",
        mode="ctf",
        goal="solve",
        session_input={"prompt": "solve"},
    )
    common = TaskCommonSkillSnapshot(
        task_id=task.id,
        selector="test-selector",
        skills=(CurrentSkillSnapshot(
            name="prompt-skill",
            version="1",
            origin="custom",
            modes=("ctf",),
            body=body,
            content_sha256=hashlib.sha256(body.encode()).hexdigest(),
            selection_reasons=("test",),
        ),),
        total_chars=len(body),
        created_at="2026-07-30T00:00:00Z",
    )

    system_message = {
        "role": "system",
        "content": build_agent_system_prompt(task, task_common=common),
    }

    assert system_message["role"] == "system"
    assert body in system_message["content"]
    assert "cannot grant tools, expand ExecutionPolicy" in system_message["content"]


def test_null_rag_retriever_is_an_explicit_replaceable_noop() -> None:
    retriever = NullRAGRetriever()
    assert retriever.retriever_id == "rag-disabled-v1"
    assert retriever.retrieve(RAGQuery(task_id="task", mode="ctf", text="query")) == ()


def test_skill_preview_api_uses_task_policy_and_returns_selection_reasons(monkeypatch) -> None:
    monkeypatch.setattr(task_routes, "_catalog_runner", lambda: EmptyMCPManager())
    client = TestClient(app)
    policy = ExecutionPolicy()
    policy.network.access = "public_internet"

    response = client.post("/api/v2/tasks/skill-preview", json={
        "mode": "ctf",
        "goal": "Inspect the web login target",
        "modeOptions": {"mode": "ctf", "subtype": "web"},
        "prompt": "https://example.test/login",
        "fileNames": [],
        "executionPolicy": policy.model_dump(mode="json"),
    })

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["selector"].startswith("task-skill-selector-v1:")
    assert payload["count"] >= 1
    assert any(item["name"] == "web-recon" for item in payload["skills"])
    assert all(item["selection_reasons"] for item in payload["skills"])

    policy.network.access = "disabled"
    blocked = client.post("/api/v2/tasks/skill-preview", json={
        "mode": "ctf",
        "goal": "Inspect the web login target",
        "modeOptions": {"mode": "ctf", "subtype": "web"},
        "prompt": "https://example.test/login",
        "fileNames": [],
        "executionPolicy": policy.model_dump(mode="json"),
    })
    assert blocked.status_code == 200
    assert "web-recon" not in {item["name"] for item in blocked.json()["skills"]}

    manual_policy = policy.model_copy(deep=True)
    manual_policy.network.access = "public_internet"
    manual = client.post("/api/v2/tasks/skill-preview", json={
        "mode": "ctf",
        "goal": "Inspect the web login target",
        "modeOptions": {"mode": "ctf", "subtype": "web"},
        "prompt": "https://example.test/login",
        "fileNames": [],
        "executionPolicy": manual_policy.model_dump(mode="json"),
        "selectedSkills": ["web-recon"],
    })
    assert manual.status_code == 200
    assert manual.json()["selector"].endswith(":manual")
    assert [item["name"] for item in manual.json()["skills"]] == ["web-recon"]
