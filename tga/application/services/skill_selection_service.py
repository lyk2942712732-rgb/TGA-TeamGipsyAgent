"""Task-common and solver-specialized Skill selection without tool grants."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Protocol, Sequence

from tga.domain.planning.intents import Intent
from tga.domain.skills.models import (
    SkillDocument,
    SkillSnapshot,
    SolverSkillSnapshot,
    TaskCommonSkillSnapshot,
)
from tga.domain.solver.definitions import SolverDefinition
from tga.modes import TaskMode
from tga.application.services.skill_candidate_activation_service import ApprovedSkillCandidate


class SkillCatalog(Protocol):
    def all(self) -> tuple[SkillDocument, ...]: ...
    def get(self, name: str) -> SkillDocument | None: ...
    def compatible(self, mode: TaskMode) -> tuple[SkillDocument, ...]: ...


@dataclass(frozen=True, slots=True)
class SolverSkillSelectionRequest:
    task_id: str
    solver_id: str
    mode: TaskMode
    mode_config: dict[str, object]
    definition: SolverDefinition
    intent: Intent | None
    available_capabilities: tuple[str, ...]
    tool_policy_allowed_capabilities: tuple[str, ...]
    created_at: str
    selected_skill_names: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class TaskCommonSkillSelectionRequest:
    task_id: str
    mode: TaskMode
    objective: str
    mode_config: dict[str, object]
    available_capabilities: tuple[str, ...]
    tool_policy_allowed_capabilities: tuple[str, ...]
    created_at: str
    selected_skill_names: tuple[str, ...] | None = None


class SolverSkillSelectionService:
    selector_id = "solver-skill-selector-v1"

    def __init__(self, catalog: SkillCatalog) -> None:
        self.catalog = catalog

    def select_solver_skills(
        self,
        request: SolverSkillSelectionRequest,
        *,
        approved_candidates: Sequence[ApprovedSkillCandidate] = (),
        selection_decision_id: str | None = None,
        skill_index_snapshot_ids: tuple[str, ...] = (),
    ) -> SolverSkillSnapshot:
        if not request.definition.supports(
            mode=request.mode,
            subtype=str(request.mode_config.get("subtype") or "") or None,
        ):
            raise ValueError(
                f"SolverDefinition {request.definition.id} does not support task scene"
            )
        if request.intent is not None:
            if request.intent.task_id != request.task_id:
                raise ValueError("Intent task ownership does not match Skill selection request")
            if request.intent.kind not in request.definition.accepted_intent_kinds:
                raise ValueError(
                    f"SolverDefinition {request.definition.id} does not accept intent kind {request.intent.kind}"
                )
        selected_names = request.selected_skill_names
        if selected_names is not None and len(selected_names) > 3:
            raise ValueError("Solver Specialized Skill selection supports at most 3 items")
        required = tuple(request.definition.required_skill_names)
        if selected_names is not None and not set(required).issubset(selected_names):
            raise ValueError("manual Solver Skill selection must include required Skills")
        names = selected_names or required
        documents = list(self._resolve_and_validate(
            names=names,
            mode=request.mode,
            available=request.available_capabilities,
            policy_allowed=request.tool_policy_allowed_capabilities,
        ))
        snapshots = [
            self._freeze(document, reasons=self._solver_reasons(document, request))
            for document in documents[:3]
        ]
        selected = {item.name for item in snapshots}
        for approved in approved_candidates:
            if len(snapshots) >= 3:
                break
            if approved.document.name in selected:
                continue
            snapshots.append(self._freeze(
                approved.document,
                reasons=approved.selection_reasons,
                candidate=approved,
            ))
            selected.add(approved.document.name)
        if selected_names is None and len(snapshots) < 3:
            for name in self._rank_solver_candidates(request, required):
                if len(snapshots) >= 3 or name in selected:
                    continue
                document = self._resolve_and_validate(
                    names=(name,),
                    mode=request.mode,
                    available=request.available_capabilities,
                    policy_allowed=request.tool_policy_allowed_capabilities,
                )[0]
                snapshots.append(self._freeze(
                    document, reasons=self._solver_reasons(document, request)
                ))
                selected.add(name)
        snapshots = tuple(snapshots)
        if not set(required).issubset(skill.name for skill in snapshots):
            raise ValueError("required Solver Skills could not be selected")
        return SolverSkillSnapshot(
            task_id=request.task_id,
            solver_id=request.solver_id,
            solver_definition_id=request.definition.id,
            intent_id=request.intent.id if request.intent else None,
            selector=self.selector_id,
            skills=snapshots,
            total_chars=sum(len(skill.body) for skill in snapshots),
            created_at=request.created_at,
            skill_index_snapshot_ids=skill_index_snapshot_ids,
            selection_decision_id=selection_decision_id,
        )

    def select_task_common_skills(
        self,
        request: TaskCommonSkillSelectionRequest,
    ) -> TaskCommonSkillSnapshot:
        names = request.selected_skill_names
        if names is not None and len(names) > 2:
            raise ValueError("Task Common Skill selection supports at most 2 items")
        if names is None:
            query = " ".join((request.objective, json.dumps(request.mode_config, sort_keys=True)))
            ranked = sorted(
                self.catalog.compatible(request.mode),
                key=lambda skill: (-_match_score(query, skill), skill.name),
            )
            names = tuple(skill.name for skill in ranked if _match_score(query, skill) > 0)[:2]
        documents = self._resolve_and_validate(
            names=names,
            mode=request.mode,
            available=request.available_capabilities,
            policy_allowed=request.tool_policy_allowed_capabilities,
        )
        snapshots = tuple(
            self._freeze(document, reasons=("task common guidance",))
            for document in documents[:2]
        )
        return TaskCommonSkillSnapshot(
            task_id=request.task_id,
            selector="task-common-skill-selector-v1",
            skills=snapshots,
            total_chars=sum(len(skill.body) for skill in snapshots),
            created_at=request.created_at,
        )

    def _rank_solver_candidates(
        self,
        request: SolverSkillSelectionRequest,
        required: tuple[str, ...],
    ) -> tuple[str, ...]:
        query = " ".join((
            request.definition.id,
            *request.definition.specialties,
            *request.definition.default_skill_tags,
            request.intent.kind if request.intent else "",
            request.intent.objective if request.intent else "",
            json.dumps(request.mode_config, sort_keys=True),
        ))
        candidates = sorted(
            self.catalog.compatible(request.mode),
            key=lambda skill: (-_match_score(query, skill), skill.name),
        )
        ordered = list(required)
        available = set(request.available_capabilities)
        policy_allowed = set(request.tool_policy_allowed_capabilities)
        for skill in candidates:
            prerequisites = set(skill.capability_requirements)
            if not prerequisites.issubset(available) or not prerequisites.issubset(policy_allowed):
                continue
            if skill.name not in ordered and _match_score(query, skill) > 0:
                ordered.append(skill.name)
            if len(ordered) >= 3:
                break
        return tuple(ordered)

    def _resolve_and_validate(
        self,
        *,
        names: tuple[str, ...],
        mode: TaskMode,
        available: tuple[str, ...],
        policy_allowed: tuple[str, ...],
    ) -> tuple[SkillDocument, ...]:
        if len(names) != len(set(names)):
            raise ValueError("Skill selection names must be unique")
        available_set = set(available)
        allowed_set = set(policy_allowed)
        documents: list[SkillDocument] = []
        for name in names:
            document = self.catalog.get(name)
            if document is None:
                raise ValueError(f"unknown Skill: {name}")
            if mode not in document.modes:
                raise ValueError(f"Skill {name} does not support mode {mode}")
            missing = set(document.capability_requirements) - available_set
            if missing:
                raise ValueError(f"Skill {name} requires unavailable capabilities: {sorted(missing)}")
            denied = set(document.capability_requirements) - allowed_set
            if denied:
                raise ValueError(
                    f"Skill {name} is incompatible with ToolPolicy capabilities: {sorted(denied)}"
                )
            documents.append(document)
        return tuple(documents)

    @staticmethod
    def _freeze(
        document: SkillDocument,
        *,
        reasons: tuple[str, ...],
        candidate: ApprovedSkillCandidate | None = None,
    ) -> SkillSnapshot:
        body = document.body.strip()
        if len(body) > 12_000:
            raise ValueError(f"Skill {document.name} exceeds the 12000 character activation limit")
        return SkillSnapshot(
            name=document.name,
            version=document.version,
            modes=document.modes,
            capability_requirements=document.capability_requirements,
            tags=document.tags,
            body=body,
            content_sha256=hashlib.sha256(body.encode("utf-8")).hexdigest(),
            origin=document.origin,
            selection_reasons=reasons,
            source_ref=document.source_ref,
            document_id=candidate.candidate.document_id if candidate else None,
            revision_id=candidate.candidate.revision_id if candidate else None,
            retrieval_run_id=candidate.candidate.retrieval_run_id if candidate else None,
            index_snapshot_id=candidate.candidate.index_snapshot_id if candidate else None,
        )

    @staticmethod
    def _solver_reasons(
        document: SkillDocument,
        request: SolverSkillSelectionRequest,
    ) -> tuple[str, ...]:
        reasons: list[str] = []
        if document.name in request.definition.required_skill_names:
            reasons.append("required by SolverDefinition")
        matching = sorted(set(document.tags).intersection(
            {*request.definition.default_skill_tags, *request.definition.specialties}
        ))
        reasons.extend(f"matches solver tag: {tag}" for tag in matching)
        if request.intent is not None:
            reasons.append(f"selected for intent kind: {request.intent.kind}")
        return tuple(reasons or ["compatible solver guidance"])


def _match_score(query: str, skill: SkillDocument) -> int:
    tokens = set(re.findall(r"[a-z0-9_-]{3,}", query.casefold()))
    document = " ".join((skill.name, *skill.tags, skill.body[:2_000])).casefold()
    return sum(token in document for token in tokens)


__all__ = [
    "SkillCatalog", "SolverSkillSelectionRequest", "SolverSkillSelectionService",
    "TaskCommonSkillSelectionRequest",
]
