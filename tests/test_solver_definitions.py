from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from tga.contracts import ModelSnapshot, TGATask
from tga.domain.planning.intents import Intent
from tga.domain.skills.models import SolverSkillSnapshot
from tga.migrations.skill_bundles import legacy_skill_bundle_to_task_common
from tga.domain.solver.definitions import SolverDefinition
from tga.domain.solver.instances import ToolPolicySnapshot
from tga.domain.solver.results import WorkerResult
from tga.infrastructure.skills.catalog import FileSkillCatalog
from tga.infrastructure.solver_definitions.registry import SolverDefinitionRegistry
from tga.infrastructure.team_templates.registry import TeamTemplateRegistry
from tga.application.services.skill_selection_service import (
    SolverSkillSelectionRequest,
    SolverSkillSelectionService,
)
from tga.application.services.solver_factory import SolverFactory
from tga.skills.models import SkillBundleSnapshot, SkillSnapshot as LegacySkillSnapshot


EXPECTED_DEFINITIONS = {
    "task-supervisor", "recon-triage", "web-network-analyst", "code-audit",
    "binary-analysis", "forensics-analysis", "vulnerability-validator",
    "evidence-reviewer", "security-reporter",
}
EXPECTED_MODES = {
    "ctf", "penetration_test", "incident_response", "vulnerability_research",
    "reverse_engineering",
}


def _model() -> ModelSnapshot:
    return ModelSnapshot(
        model="test-model",
        capability_fingerprint="a" * 64,
        verification_id="verification_1",
        verified_at="2026-01-01T00:00:00Z",
        capabilities={"action_tools": True},
        max_output_tokens=2_048,
        timeout_seconds=30,
        temperature=0,
    )


def _task(task_id: str, mode: str = "ctf") -> TGATask:
    return TGATask(id=task_id, name=task_id, mode=mode, goal="test solver definition")


def _intent(task_id: str, intent_id: str = "intent_1", kind: str = "recon") -> Intent:
    return Intent(
        id=intent_id,
        task_id=task_id,
        kind=kind,
        title="Recon",
        objective="Inspect the supplied target.",
        budget={"turns": 4},
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )


def test_builtin_solver_definitions_load_validate_and_hash() -> None:
    registry = SolverDefinitionRegistry.builtin()

    assert set(registry.ids()) == EXPECTED_DEFINITIONS
    for definition in registry.all():
        assert len(definition.content_sha256) == 64
        assert definition.model_config.get("frozen") is True
        assert definition.version


def test_solver_definition_loader_rejects_unknown_fields(tmp_path: Path) -> None:
    source = SolverDefinitionRegistry.builtin().get("task-supervisor")
    assert source is not None
    payload = source.model_dump(mode="json", exclude={"content_sha256"})
    payload["unexpected"] = True
    path = tmp_path / "supervisors" / "invalid.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="unexpected|Extra inputs"):
        SolverDefinitionRegistry(root=tmp_path)


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"required_capabilities": ["unknown.capability"]}, "unknown capabilities"),
        ({"required_skill_names": ["unknown-skill"]}, "unknown Skill"),
    ],
)
def test_solver_definition_loader_rejects_unknown_references(
    tmp_path: Path,
    updates: dict[str, object],
    message: str,
) -> None:
    source = SolverDefinitionRegistry.builtin().require("task-supervisor")
    payload = source.model_dump(mode="json", exclude={"content_sha256"})
    payload.update(updates)
    path = tmp_path / "definition.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        SolverDefinitionRegistry(root=tmp_path)


def test_worker_definition_cannot_own_task_completion() -> None:
    base = SolverDefinitionRegistry.builtin().get("recon-triage")
    assert base is not None
    with pytest.raises(ValidationError, match="Worker cannot own task completion"):
        SolverDefinition.model_validate({
            **base.model_dump(mode="json"),
            "completion_authority": "task",
        })


def test_five_team_templates_reference_compatible_definitions() -> None:
    definitions = SolverDefinitionRegistry.builtin()
    teams = TeamTemplateRegistry.builtin(definitions=definitions)

    assert set(teams.modes()) == EXPECTED_MODES
    for template in teams.all():
        assert template.max_active_workers == 1
        assert template.content_sha256
        supervisor = definitions.require(template.supervisor_definition_id)
        reviewer = definitions.require(template.reviewer_definition_id)
        reporter = definitions.require(template.reporter_definition_id)
        assert supervisor.orchestration_role == "supervisor"
        assert reviewer.orchestration_role == "reviewer"
        assert reporter.orchestration_role == "reporter"
        for definition_id in template.available_solver_definition_ids:
            assert template.mode in definitions.require(definition_id).supported_modes
        if template.mode == "ctf":
            for definition_id in template.required_solver_definition_ids:
                definition = definitions.require(definition_id)
                for subtype in ("web", "pwn", "reverse", "crypto", "misc", "forensics", "auto", "unknown"):
                    assert definition.supports(mode="ctf", subtype=subtype)


def test_solver_skill_selection_rejects_missing_capability_or_policy_permission() -> None:
    definitions = SolverDefinitionRegistry.builtin()
    definition = definitions.require("web-network-analyst")
    service = SolverSkillSelectionService(FileSkillCatalog.builtin())
    request = SolverSkillSelectionRequest(
        task_id="task_1",
        solver_id="solver_1",
        mode="ctf",
        mode_config={"mode": "ctf", "subtype": "web"},
        definition=definition,
        intent=_intent("task_1", kind="web_analysis"),
        available_capabilities=("http.request", "artifact.inspect"),
        tool_policy_allowed_capabilities=("artifact.inspect",),
        created_at="2026-01-01T00:00:00Z",
    )

    with pytest.raises(ValueError, match="ToolPolicy"):
        service.select_solver_skills(request)


def test_skill_snapshot_is_frozen_from_later_source_edits(tmp_path: Path) -> None:
    skill_path = tmp_path / "stable.md"
    skill_path.write_text(
        "---\nname: stable-skill\nversion: 1\nmodes: [ctf]\n"
        "capabilities: [workspace.read]\ntags: [recon]\n---\nOriginal body.\n",
        encoding="utf-8",
    )
    definition = SolverDefinition.model_validate({
        "id": "snapshot-worker", "version": "1", "orchestration_role": "worker",
        "specialties": ["recon"], "supported_modes": ["ctf"],
        "supported_subtypes": ["web"], "system_prompt_template": "Inspect {objective}",
        "default_skill_tags": ["recon"], "required_skill_names": ["stable-skill"],
        "required_capabilities": ["workspace.read"], "allowed_tool_groups": ["resource_read"],
        "tool_policy_profile": "read-only", "accepted_intent_kinds": ["recon"],
        "output_contract": {"name": "worker_result", "required_fields": ["summary"]},
        "default_budget": {"max_turns": 8, "max_input_tokens": 8000,
                           "max_output_tokens": 2000, "max_tool_calls": 8,
                           "max_artifacts": 8},
        "completion_authority": "worker_only", "content_sha256": "b" * 64,
    })
    service = SolverSkillSelectionService(FileSkillCatalog(tmp_path))
    request = SolverSkillSelectionRequest(
        task_id="task_1", solver_id="solver_1", mode="ctf",
        mode_config={"mode": "ctf", "subtype": "web"}, definition=definition,
        intent=_intent("task_1"), available_capabilities=("workspace.read",),
        tool_policy_allowed_capabilities=("workspace.read",),
        created_at="2026-01-01T00:00:00Z",
    )

    snapshot = service.select_solver_skills(request)
    skill_path.write_text(skill_path.read_text(encoding="utf-8").replace("Original", "Changed"), encoding="utf-8")

    assert snapshot.skills[0].body.strip() == "Original body."
    assert "Changed" not in snapshot.model_dump_json()
    assert SolverSkillSnapshot.model_validate_json(snapshot.model_dump_json()) == snapshot


def test_legacy_task_skill_bundle_remains_losslessly_readable() -> None:
    body = "Legacy task guidance."
    import hashlib

    legacy = SkillBundleSnapshot(
        selector="task-skill-selector-v1:manual",
        skills=[LegacySkillSnapshot(
            name=f"legacy-{index}", version="1", origin="builtin", modes=["ctf"],
            capabilities=["workspace.read"], tags=["legacy"], body=body,
            content_sha256=hashlib.sha256(body.encode()).hexdigest(), score=1,
            selection_reasons=["legacy task selection"],
        ) for index in range(3)],
        total_chars=len(body) * 3,
    )

    snapshot = legacy_skill_bundle_to_task_common(
        legacy, task_id="task_1", created_at="2026-01-01T00:00:00Z"
    )

    assert snapshot.legacy_import is True
    assert len(snapshot.skills) == 3
    assert [item.body for item in snapshot.skills] == [body, body, body]
    assert snapshot.model_dump(mode="json")["skills"][0]["required_capabilities"] == ["workspace.read"]


def test_skill_activation_has_no_tool_grant_surface() -> None:
    from tga.domain.skills.models import SkillActivation

    assert not {"tools", "tool_groups", "capabilities", "allowed_capabilities"}.intersection(
        SkillActivation.model_fields
    )


def test_same_definition_can_create_instances_for_different_tasks() -> None:
    definition = SolverDefinitionRegistry.builtin().require("task-supervisor")
    policy = ToolPolicySnapshot(
        profile=definition.tool_policy_profile,
        allowed_tool_groups=definition.allowed_tool_groups,
        allowed_capabilities=(),
        content_sha256="c" * 64,
    )
    factory = SolverFactory()

    first = factory.create(
        instance_id="solver_task_a", task=_task("task_a"), definition=definition,
        intent=None, model_snapshot=_model(), skill_snapshot=None,
        tool_policy_snapshot=policy, parent_solver_id=None,
        created_at="2026-01-01T00:00:00Z",
    )
    second = factory.create(
        instance_id="solver_task_b", task=_task("task_b"), definition=definition,
        intent=None, model_snapshot=_model(), skill_snapshot=None,
        tool_policy_snapshot=policy, parent_solver_id=None,
        created_at="2026-01-01T00:00:00Z",
    )

    assert first.definition_id == second.definition_id == "task-supervisor"
    assert first.task_id == "task_a" and second.task_id == "task_b"
    assert first.id != second.id


def test_factory_builds_worker_without_starting_a_runner() -> None:
    definition = SolverDefinitionRegistry.builtin().require("web-network-analyst")
    task = _task("task_worker")
    intent = _intent(task.id, kind="web_analysis")
    policy = ToolPolicySnapshot(
        profile=definition.tool_policy_profile,
        allowed_tool_groups=definition.allowed_tool_groups,
        allowed_capabilities=definition.required_capabilities,
        content_sha256="d" * 64,
    )
    skills = SolverSkillSelectionService(FileSkillCatalog.builtin()).select_solver_skills(
        SolverSkillSelectionRequest(
            task_id=task.id, solver_id="solver_worker", mode="ctf",
            mode_config={"mode": "ctf", "subtype": "web"}, definition=definition,
            intent=intent, available_capabilities=definition.required_capabilities,
            tool_policy_allowed_capabilities=definition.required_capabilities,
            created_at="2026-01-01T00:00:00Z",
        )
    )

    instance = SolverFactory().create(
        instance_id="solver_worker", task=task, definition=definition, intent=intent,
        model_snapshot=_model(), skill_snapshot=skills, tool_policy_snapshot=policy,
        parent_solver_id="solver_supervisor", created_at="2026-01-01T00:00:00Z",
    )

    assert instance.status == "created"
    assert instance.assigned_intent_id == intent.id
    assert instance.transcript_ref == "solver://solver_worker/transcript"
    assert instance.private_workspace_ref == "solver://solver_worker/workspace"


def test_loaded_completion_authority_matches_orchestration_role() -> None:
    registry = SolverDefinitionRegistry.builtin()
    assert registry.require("task-supervisor").completion_authority == "task"
    for definition in registry.all():
        if definition.orchestration_role == "worker":
            assert definition.completion_authority != "task"


def test_definition_scene_support_and_structured_worker_result() -> None:
    definition = SolverDefinitionRegistry.builtin().require("recon-triage")
    assert definition.supports(mode="ctf", subtype="web") is True
    assert definition.supports(mode="incident_response") is False

    result = WorkerResult(
        task_id="task_1",
        solver_id="solver_1",
        intent_id="intent_1",
        status="partial",
        summary="Mapped the documented HTTP surface.",
        artifact_ids=("artifact_1",),
        candidate_evidence_claim_ids=("claim_1",),
        candidate_knowledge_ids=("knowledge_1",),
        finding_ids=(),
        coverage={"completed": ["landing page"], "not_covered": ["authenticated routes"]},
        limitations=("No credentials were supplied.",),
        budget_usage={"turns": 2, "input_tokens": 100, "output_tokens": 50,
                      "tool_calls": 1, "artifacts": 1},
        errors=(),
    )

    payload = result.model_dump(mode="json")
    assert payload["solver_id"] == "solver_1"
    assert payload["candidate_evidence_claim_ids"] == ["claim_1"]


def test_every_phase_3_domain_model_forbids_extra_fields() -> None:
    import importlib
    import inspect

    modules = (
        "tga.domain.skills.models", "tga.domain.solver.assignments",
        "tga.domain.solver.budgets", "tga.domain.solver.definitions",
        "tga.domain.solver.instances", "tga.domain.solver.results",
        "tga.domain.solver.teams",
    )
    checked: list[str] = []
    for module_name in modules:
        module = importlib.import_module(module_name)
        for name, model in vars(module).items():
            if (
                inspect.isclass(model)
                and issubclass(model, BaseModel)
                and model.__module__ == module_name
            ):
                checked.append(f"{module_name}.{name}")
                assert model.model_config.get("extra") == "forbid"
    assert len(checked) >= 15
