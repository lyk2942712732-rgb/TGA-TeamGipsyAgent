from __future__ import annotations

import pytest
from pydantic import ValidationError
from pydantic import BaseModel

from tga.domain.evidence.claims import EvidenceClaim
from tga.domain.evidence.findings import Finding
from tga.domain.evidence.legacy_models import ArtifactRecord, Finding as LegacyFinding
from tga.domain.evidence.locators import EvidenceLocator
from tga.domain.knowledge.items import KnowledgeItem
from tga.domain.legacy.converters import (
    artifact_record_to_artifact,
    legacy_finding_to_evidence,
    memory_entry_to_knowledge,
    memory_entry_to_task_hint,
    strategy_card_to_plans,
)
from tga.domain.planning.global_plan import GlobalPlan
from tga.domain.planning.intents import Intent, IntentDependency
from tga.domain.planning.local_plan import LocalPlan, LocalPlanStep
from tga.domain.solver.legacy_models import MemoryEntry, StrategyCard, StrategyStep
from tga.domain.task.hints import TaskHint
from tga.domain.task.spec import TaskDirective, TaskSpec


def _locator() -> EvidenceLocator:
    return EvidenceLocator(
        kind="legacy_whole_artifact",
        legacy_reason="schema-v5 record has no stable fragment coordinates",
    )


def _claim(*, status: str = "candidate") -> EvidenceClaim:
    return EvidenceClaim(
        id=f"claim_{status}",
        task_id="task_1",
        statement="The artifact contains the observed response.",
        artifact_id="artifact_1",
        locator=_locator(),
        status=status,
        created_at="2026-01-01T00:00:00Z",
    )


def _intent(intent_id: str, *dependencies: str) -> Intent:
    return Intent(
        id=intent_id,
        task_id="task_1",
        title=intent_id,
        objective=f"Complete {intent_id}",
        dependencies=[IntentDependency(intent_id=item) for item in dependencies],
        budget={"turns": 4},
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )


def test_task_spec_keeps_formal_directives_separate_from_hints() -> None:
    directive = TaskDirective(
        id="directive_1",
        task_id="task_1",
        kind="instruction",
        content="Do not modify the target.",
        source="user",
        created_at="2026-01-01T00:00:00Z",
    )
    spec = TaskSpec(
        task_id="task_1",
        objective="Assess the supplied target.",
        instructions=[directive],
        constraints=[],
        success_criteria=[],
        resources=[],
    )
    hint = TaskHint(
        id="hint_1",
        task_id="task_1",
        content="An old article suggests a default password.",
        source="user",
        status="unreviewed",
        scope="task",
        created_at="2026-01-01T00:00:00Z",
    )

    assert spec.instructions == [directive]
    assert hint not in spec.instructions


def test_hint_alone_cannot_create_verified_factual_knowledge() -> None:
    with pytest.raises(ValidationError, match="verified factual knowledge"):
        KnowledgeItem(
            id="knowledge_1",
            task_id="task_1",
            scope="task",
            status="verified",
            kind="fact",
            content="The default password works.",
            source_hint_ids=["hint_1"],
            created_by_solver_id="solver_1",
            created_at="2026-01-01T00:00:00Z",
        )


def test_verified_fact_requires_claim_or_explicit_human_source() -> None:
    by_claim = KnowledgeItem(
        id="knowledge_claim",
        task_id="task_1",
        scope="task",
        status="verified",
        kind="fact",
        content="The response discloses a version.",
        evidence_claim_ids=["claim_confirmed"],
        created_by_solver_id="solver_1",
        created_at="2026-01-01T00:00:00Z",
    )
    by_human = KnowledgeItem(
        id="knowledge_human",
        task_id="task_1",
        scope="task",
        status="verified",
        kind="fact",
        content="The owner confirmed the maintenance window.",
        human_source="user_intervention:intervention_1",
        created_by_solver_id="solver_1",
        created_at="2026-01-01T00:00:00Z",
    )

    assert by_claim.status == by_human.status == "verified"


def test_confirmed_finding_requires_a_confirmed_evidence_claim() -> None:
    with pytest.raises(ValidationError, match="confirmed EvidenceClaim"):
        Finding(
            id="finding_1",
            task_id="task_1",
            title="Version disclosure",
            severity="low",
            status="confirmed",
            evidence_claims=[_claim(status="candidate")],
            created_at="2026-01-01T00:00:00Z",
        )

    finding = Finding(
        id="finding_2",
        task_id="task_1",
        title="Version disclosure",
        severity="low",
        status="confirmed",
        evidence_claims=[_claim(status="confirmed")],
        created_at="2026-01-01T00:00:00Z",
    )
    assert finding.status == "confirmed"


def test_global_plan_rejects_self_dependency_and_cycles() -> None:
    with pytest.raises(ValidationError, match="depend on itself"):
        _intent("intent_1", "intent_1")

    with pytest.raises(ValidationError, match="cycle"):
        GlobalPlan(
            id="plan_1",
            task_id="task_1",
            version=1,
            status="draft",
            intents=[_intent("intent_1", "intent_2"), _intent("intent_2", "intent_1")],
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
        )


def test_local_plan_steps_must_match_solver_and_intent_ownership() -> None:
    with pytest.raises(ValidationError, match="ownership"):
        LocalPlan(
            id="local_1",
            task_id="task_1",
            solver_id="solver_1",
            intent_id="intent_1",
            version=1,
            status="draft",
            steps=[LocalPlanStep(
                id="step_1",
                solver_id="solver_2",
                intent_id="intent_1",
                description="Inspect the response.",
            )],
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
        )


def test_knowledge_cannot_supersede_itself() -> None:
    with pytest.raises(ValidationError, match="supersede itself"):
        KnowledgeItem(
            id="knowledge_1",
            task_id="task_1",
            scope="solver",
            target_id="solver_1",
            status="candidate",
            kind="hypothesis",
            content="The service may use a proxy.",
            created_by_solver_id="solver_1",
            supersedes_id="knowledge_1",
            created_at="2026-01-01T00:00:00Z",
        )


def test_new_models_forbid_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        TaskHint(
            id="hint_1",
            task_id="task_1",
            content="candidate",
            source="user",
            status="unreviewed",
            scope="task",
            created_at="2026-01-01T00:00:00Z",
            unexpected=True,
        )


def test_every_phase_2_pydantic_model_forbids_extra_fields() -> None:
    modules = [
        "tga.domain.task.spec", "tga.domain.task.hints",
        "tga.domain.task.interventions", "tga.domain.planning.intents",
        "tga.domain.planning.global_plan", "tga.domain.planning.local_plan",
        "tga.domain.planning.proposals", "tga.domain.knowledge.items",
        "tga.domain.knowledge.conflicts", "tga.domain.knowledge.promotion",
        "tga.domain.evidence.artifacts", "tga.domain.evidence.locators",
        "tga.domain.evidence.claims", "tga.domain.evidence.findings",
    ]
    import importlib
    import inspect

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
    assert len(checked) >= 14


@pytest.mark.parametrize(
    "locator",
    [
        EvidenceLocator(kind="text_range", char_start=0, char_end=8),
        EvidenceLocator(kind="line_range", line_start=1, line_end=3),
        EvidenceLocator(kind="json_path", json_path="$.response.headers.server"),
        EvidenceLocator(kind="page", page=2, page_end=3),
        EvidenceLocator(kind="binary_offset", binary_offset=16, binary_length=4),
        _locator(),
    ],
)
def test_evidence_locator_supports_required_coordinate_types(locator: EvidenceLocator) -> None:
    assert locator.kind in {
        "text_range", "line_range", "json_path", "page", "binary_offset",
        "legacy_whole_artifact",
    }


def test_memory_converters_never_infer_verification() -> None:
    hint = MemoryEntry(
        id="memory_hint",
        task_id="task_1",
        kind="hint",
        content="Try the legacy endpoint.",
        source="user",
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )
    fact = MemoryEntry(
        id="memory_fact",
        task_id="task_1",
        kind="fact",
        content="The legacy endpoint returned 200.",
        artifact_ids=["artifact_1"],
        source="solver_1",
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )

    converted_hint = memory_entry_to_task_hint(hint)
    converted_fact = memory_entry_to_knowledge(fact, created_by_solver_id="solver_1")

    assert converted_hint.status == "unreviewed"
    assert converted_hint.legacy_import is True
    assert converted_fact.status == "candidate"
    assert converted_fact.evidence_claim_ids == []
    assert converted_fact.provenance["legacy_artifact_ids"] == ["artifact_1"]


def test_artifact_and_finding_converters_use_conservative_legacy_provenance() -> None:
    artifact = artifact_record_to_artifact(ArtifactRecord(
        id="artifact_1",
        task_id="task_1",
        intent_id=None,
        kind="http_response",
        path="artifacts/response.json",
        sha256="a" * 64,
        tool="http.request",
        target="https://example.test",
        created_at="2026-01-01T00:00:00Z",
    ))
    claim, finding = legacy_finding_to_evidence(LegacyFinding(
        id="finding_legacy",
        task_id="task_1",
        title="Version disclosure",
        target="https://example.test",
        severity="low",
        status="confirmed",
        evidence_artifact_id="artifact_1",
        evidence_excerpt="Server: example/1.0",
    ))

    assert artifact.legacy_import is True
    assert claim.status == "candidate"
    assert claim.locator.kind == "legacy_whole_artifact"
    assert finding.status == "candidate"
    assert finding.provenance["legacy_status"] == "confirmed"


def test_strategy_card_conversion_creates_only_candidate_plan_structures() -> None:
    card = StrategyCard(
        id="strategy_1",
        task_id="task_1",
        title="Try the documented flow",
        summary="Legacy mixed strategy state.",
        steps=[StrategyStep(
            id="legacy_step",
            title="Inspect login",
            instructions="Request the login page.",
            status="succeeded",
        )],
        status="succeeded",
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )

    global_plan, local_plan = strategy_card_to_plans(
        card, solver_id="solver_1", intent_id="intent_1"
    )

    assert global_plan.status == "draft"
    assert global_plan.legacy_import is True
    assert global_plan.intents[0].status == "proposed"
    assert local_plan.status == "draft"
    assert all(step.status == "pending" for step in local_plan.steps)
