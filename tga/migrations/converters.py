"""Pure and intentionally conservative legacy-domain conversions.

No converter promotes imported material to a verified/confirmed state. Legacy
artifact references that lack stable coordinates become explicit whole-artifact
locators instead of invented ranges.
"""

from __future__ import annotations

from tga.domain.evidence.artifacts import Artifact
from tga.domain.evidence.claims import EvidenceClaim
from tga.domain.evidence.findings import Finding
from tga.migrations.evidence_models import LegacyArtifactRecord, LegacyFinding
from tga.domain.evidence.locators import EvidenceLocator
from tga.domain.knowledge.items import KnowledgeItem
from tga.domain.planning.global_plan import GlobalPlan
from tga.domain.planning.intents import Intent
from tga.domain.planning.local_plan import LocalPlan, LocalPlanStep
from tga.migrations.legacy_models import MemoryEntry, StrategyCard
from tga.domain.task.hints import TaskHint


def memory_entry_to_task_hint(entry: MemoryEntry) -> TaskHint:
    if entry.kind != "hint":
        raise ValueError("only MemoryEntry(kind='hint') can convert to TaskHint")
    return TaskHint(
        id=entry.id,
        task_id=entry.task_id,
        content=entry.content,
        source=entry.source,
        status="unreviewed",
        scope="task",
        created_at=entry.created_at,
        legacy_import=True,
        provenance={
            "legacy_model": "MemoryEntry",
            "legacy_updated_at": entry.updated_at,
            "legacy_artifact_ids": list(entry.artifact_ids),
            "legacy_supersedes_id": entry.supersedes_id,
        },
    )


def memory_entry_to_knowledge(
    entry: MemoryEntry,
    *,
    created_by_solver_id: str,
) -> KnowledgeItem:
    if entry.kind not in {"fact", "constraint", "decision", "failure_boundary"}:
        raise ValueError(
            "only fact, constraint, decision, or failure_boundary MemoryEntry can convert to KnowledgeItem"
        )
    return KnowledgeItem(
        id=entry.id,
        task_id=entry.task_id,
        scope="task",
        status="candidate",
        kind=entry.kind,
        content=entry.content,
        evidence_claim_ids=[],
        source_hint_ids=[],
        source_retrieval_run_ids=[],
        created_by_solver_id=created_by_solver_id,
        supersedes_id=entry.supersedes_id,
        created_at=entry.created_at,
        updated_at=entry.updated_at,
        legacy_import=True,
        provenance={
            "legacy_model": "MemoryEntry",
            "legacy_source": entry.source,
            # Artifact ids are deliberately not relabelled as EvidenceClaims.
            "legacy_artifact_ids": list(entry.artifact_ids),
        },
    )


def artifact_record_to_artifact(record: LegacyArtifactRecord) -> Artifact:
    return Artifact(
        id=record.id,
        task_id=record.task_id,
        intent_id=record.intent_id,
        kind=record.kind,
        path=record.path,
        sha256=record.sha256,
        tool=record.tool,
        target=record.target,
        input_id=record.input_id,
        created_at=record.created_at,
        legacy_import=True,
        provenance={
            "legacy_model": "ArtifactRecord",
            "legacy_provenance": dict(record.provenance),
        },
    )


def legacy_finding_to_evidence(
    legacy: LegacyFinding,
    *,
    imported_at: str = "legacy:unknown",
) -> tuple[EvidenceClaim | None, Finding]:
    claim: EvidenceClaim | None = None
    if legacy.evidence_artifact_id:
        claim = EvidenceClaim(
            id=f"legacy_claim_{legacy.id}",
            task_id=legacy.task_id,
            statement=legacy.evidence_excerpt or legacy.title,
            artifact_id=legacy.evidence_artifact_id,
            locator=EvidenceLocator(
                kind="legacy_whole_artifact",
                text_quote=legacy.evidence_excerpt,
                legacy_reason="schema-v5 Finding did not persist stable fragment coordinates",
            ),
            status="candidate",
            created_at=imported_at,
            legacy_import=True,
            provenance={
                "legacy_model": "Finding",
                "legacy_finding_id": legacy.id,
                "legacy_status": legacy.status,
            },
        )
    finding = Finding(
        id=legacy.id,
        task_id=legacy.task_id,
        title=legacy.title,
        description=legacy.evidence_excerpt or "",
        target=legacy.target,
        severity=legacy.severity,
        status="candidate",
        evidence_claims=[claim] if claim is not None else [],
        reproduction_steps=list(legacy.reproduction_steps),
        remediation=legacy.remediation,
        created_at=imported_at,
        legacy_import=True,
        provenance={
            "legacy_model": "Finding",
            "legacy_status": legacy.status,
            "legacy_tool": legacy.tool,
            "verification_status_inferred": False,
        },
    )
    return claim, finding


def strategy_card_to_plans(
    card: StrategyCard,
    *,
    solver_id: str,
    intent_id: str,
) -> tuple[GlobalPlan, LocalPlan]:
    intent = Intent(
        id=intent_id,
        task_id=card.task_id,
        title=card.title,
        objective=card.summary or card.title,
        status="proposed",
        assigned_solver_id=solver_id,
        budget={},
        created_at=card.created_at,
        updated_at=card.updated_at,
        legacy_import=True,
        provenance={
            "legacy_model": "StrategyCard",
            "legacy_strategy_card_id": card.id,
            "legacy_status": card.status,
            "legacy_claims": list(card.claims),
            "legacy_prerequisites": list(card.prerequisites),
            "verification_status_inferred": False,
        },
    )
    global_plan = GlobalPlan(
        id=f"global_{card.id}",
        task_id=card.task_id,
        version=1,
        status="draft",
        intents=[intent],
        created_by_solver_id=solver_id,
        created_at=card.created_at,
        updated_at=card.updated_at,
        legacy_import=True,
        provenance={
            "legacy_model": "StrategyCard",
            "legacy_strategy_card_id": card.id,
            "candidate_conversion": True,
        },
    )
    local_plan = LocalPlan(
        id=f"local_{card.id}_{solver_id}",
        task_id=card.task_id,
        solver_id=solver_id,
        intent_id=intent_id,
        version=1,
        status="draft",
        steps=[
            LocalPlanStep(
                id=step.id,
                solver_id=solver_id,
                intent_id=intent_id,
                description=f"{step.title}: {step.instructions}",
                status="pending",
                order=index,
                evidence_claim_ids=[],
                provenance={
                    "legacy_model": "StrategyStep",
                    "legacy_status": step.status,
                    "legacy_action_ids": list(step.action_ids),
                    "legacy_artifact_ids": list(step.evidence_artifact_ids),
                },
            )
            for index, step in enumerate(card.steps)
        ],
        created_at=card.created_at,
        updated_at=card.updated_at,
        legacy_import=True,
        provenance={
            "legacy_model": "StrategyCard",
            "legacy_strategy_card_id": card.id,
            "candidate_conversion": True,
        },
    )
    return global_plan, local_plan


__all__ = [
    "artifact_record_to_artifact", "legacy_finding_to_evidence",
    "memory_entry_to_knowledge", "memory_entry_to_task_hint",
    "strategy_card_to_plans",
]
