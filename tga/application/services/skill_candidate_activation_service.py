"""Governed bridge from retrieval references to complete Skill documents."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

from tga.domain.planning import Intent
from tga.domain.retrieval import OwnerScope, RetrievalPolicy
from tga.domain.skills import (
    SkillCandidate,
    SkillCandidateRejection,
    SkillDocument,
    SkillSelectionDecision,
)
from tga.domain.solver import SolverDefinition
from tga.modes import TaskMode


MAX_RAG_SKILLS = 3
MAX_RAG_SKILL_CHARS = 24_000


@dataclass(frozen=True, slots=True)
class ApprovedSkillCandidate:
    candidate: SkillCandidate
    document: SkillDocument
    priority: int
    selection_reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SkillCandidateActivationResult:
    candidates: tuple[SkillCandidate, ...]
    approved: tuple[ApprovedSkillCandidate, ...]
    decision: SkillSelectionDecision


class SkillCandidateActivationService:
    """Reload and validate candidates; retrieved wrapper text is never parsed."""

    def __init__(self, *, repository, capability_registry, event_repository=None) -> None:
        self.repository = repository
        self.capability_registry = capability_registry
        self.events = event_repository

    def activate(
        self,
        *,
        pack,
        task_id: str,
        solver_id: str,
        mode: TaskMode,
        definition: SolverDefinition,
        intent: Intent | None,
        available_capabilities: tuple[str, ...],
        tool_policy_allowed_capabilities: tuple[str, ...],
        policy: RetrievalPolicy,
        workspace_id: str | None,
        created_at: str,
        reserved_skill_names: tuple[str, ...] = (),
        max_skills: int = MAX_RAG_SKILLS,
        max_chars: int = MAX_RAG_SKILL_CHARS,
    ) -> SkillCandidateActivationResult:
        candidates = tuple(self._candidate(item, pack) for item in pack.items)
        rejected: list[SkillCandidateRejection] = []
        viable: list[ApprovedSkillCandidate] = []
        seen_versions: set[tuple[str, str]] = set()
        registry_names = {
            item["name"] for item in self.capability_registry.snapshot()["capabilities"]
        }
        for candidate in candidates:
            rejection = self._validate_candidate(
                candidate=candidate,
                pack=pack,
                task_id=task_id,
                solver_id=solver_id,
                mode=mode,
                definition=definition,
                intent=intent,
                available=set(available_capabilities),
                policy_allowed=set(tool_policy_allowed_capabilities),
                registry_names=registry_names,
                policy=policy,
                workspace_id=workspace_id,
                reserved_names=set(reserved_skill_names),
                seen_versions=seen_versions,
            )
            if isinstance(rejection, SkillCandidateRejection):
                rejected.append(rejection)
                continue
            document, priority, reasons = rejection
            seen_versions.add((document.name, document.version))
            viable.append(ApprovedSkillCandidate(
                candidate=candidate,
                document=document,
                priority=priority,
                selection_reasons=reasons,
            ))

        by_name: dict[str, list[ApprovedSkillCandidate]] = {}
        for item in viable:
            by_name.setdefault(item.document.name, []).append(item)
        version_resolved: list[ApprovedSkillCandidate] = []
        for name in sorted(by_name):
            versions = by_name[name]
            versions.sort(key=lambda item: self._version_release_key(item), reverse=True)
            version_resolved.append(versions[0])
            for skipped in versions[1:]:
                rejected.append(self._reject(
                    skipped.candidate,
                    "VERSION_NOT_SELECTED",
                    "A different published version won the release policy",
                ))

        viable = version_resolved
        viable.sort(key=lambda item: (
            len(self.repository.get_skill_publication(item.candidate.revision_id).requires),
            -item.priority,
            -item.candidate.retrieval_score,
            item.document.name,
        ))
        approved: list[ApprovedSkillCandidate] = []
        selected_names = set(reserved_skill_names)
        chars = 0
        for item in viable:
            publication = self.repository.get_skill_publication(item.candidate.revision_id)
            if item.document.name in selected_names:
                rejected.append(self._reject(item.candidate, "NAME_CONFLICT", "Skill name is already selected"))
                continue
            if publication is None:
                rejected.append(self._reject(item.candidate, "PUBLICATION_MISSING", "Skill publication metadata is missing"))
                continue
            selected = {value.document.name for value in approved}
            if set(publication.conflicts_with).intersection(selected):
                rejected.append(self._reject(item.candidate, "SKILL_CONFLICT", "Skill conflicts with an already selected Skill"))
                continue
            if not set(publication.requires).issubset(selected | selected_names):
                rejected.append(self._reject(item.candidate, "DEPENDENCY_MISSING", "Required Skill dependency is not selected"))
                continue
            body_chars = len(item.document.body.strip())
            if len(approved) >= max_skills or chars + body_chars > max_chars:
                rejected.append(self._reject(item.candidate, "SKILL_BUDGET_EXCEEDED", "Skill count or character budget is exhausted"))
                continue
            approved.append(item)
            selected_names.add(item.document.name)
            chars += body_chars

        decision_id = "skilldec_" + hashlib.sha256(json.dumps({
            "task_id": task_id,
            "solver_id": solver_id,
            "intent_id": intent.id if intent else None,
            "run": pack.retrieval_run_id,
            "candidates": [item.id for item in candidates],
            "selected": [item.candidate.id for item in approved],
        }, sort_keys=True).encode()).hexdigest()[:32]
        decision = SkillSelectionDecision(
            id=decision_id,
            task_id=task_id,
            solver_id=solver_id,
            intent_id=intent.id if intent else None,
            solver_definition_id=definition.id,
            retrieval_run_ids=(pack.retrieval_run_id,),
            index_snapshot_ids=(pack.index_snapshot_id,),
            candidate_ids=tuple(item.id for item in candidates),
            selected_candidate_ids=tuple(item.candidate.id for item in approved),
            selected_skill_names=tuple(item.document.name for item in approved),
            rejected_candidates=tuple(rejected),
            policy_snapshot=policy.model_dump(mode="json"),
            budget_snapshot={
                "max_skills": max_skills,
                "max_chars": max_chars,
                "selected_skills": len(approved),
                "selected_chars": chars,
                "reserved_skill_names": list(reserved_skill_names),
            },
            created_at=created_at,
        )
        self._emit_events(decision, candidates, approved)
        return SkillCandidateActivationResult(
            candidates=candidates,
            approved=tuple(approved),
            decision=decision,
        )

    def _validate_candidate(
        self,
        *,
        candidate: SkillCandidate,
        pack,
        task_id: str,
        solver_id: str,
        mode: TaskMode,
        definition: SolverDefinition,
        intent: Intent | None,
        available: set[str],
        policy_allowed: set[str],
        registry_names: set[str],
        policy: RetrievalPolicy,
        workspace_id: str | None,
        reserved_names: set[str],
        seen_versions: set[tuple[str, str]],
    ):
        source = self.repository.get_source(candidate.source_id)
        revision = self.repository.get_revision(candidate.revision_id)
        snapshot = self.repository.get_snapshot(pack.index_snapshot_id)
        publication = self.repository.get_skill_publication(candidate.revision_id)
        if source is None or source.status != "active" or source.channel != "skill":
            return self._reject(candidate, "SOURCE_NOT_ACTIVE", "Skill source is missing or inactive")
        owner_error = _owner_rejection(
            source.owner, task_id=task_id, solver_id=solver_id,
            workspace_id=workspace_id, policy=policy,
        )
        if owner_error:
            return self._reject(candidate, "OWNER_SCOPE_DENIED", owner_error)
        if revision is None or revision.extraction_status != "parsed":
            return self._reject(candidate, "REVISION_INVALID", "Skill revision is missing or not parsed")
        if snapshot is None or snapshot.document_hashes.get(candidate.document_id) != revision.content_sha256:
            return self._reject(candidate, "SNAPSHOT_REVISION_MISMATCH", "Skill revision is not fixed by the selected IndexSnapshot")
        if publication is None or publication.status != "published":
            return self._reject(candidate, "PUBLICATION_NOT_APPROVED", "Only published Skill revisions can be activated")
        payload = revision.metadata.get("skill_document")
        if not isinstance(payload, dict):
            return self._reject(candidate, "FULL_DOCUMENT_MISSING", "Full SkillDocument is unavailable")
        try:
            document = SkillDocument.model_validate(payload)
        except ValueError:
            return self._reject(candidate, "FULL_DOCUMENT_INVALID", "Full SkillDocument failed validation")
        body_sha = hashlib.sha256(document.body.strip().encode()).hexdigest()
        if body_sha != revision.metadata.get("skill_body_sha256") or body_sha != candidate.content_sha256:
            return self._reject(candidate, "CONTENT_HASH_MISMATCH", "Full Skill body hash does not match candidate provenance")
        if (document.name, document.version) in seen_versions:
            return self._reject(candidate, "DUPLICATE_VERSION", "Duplicate Skill name and version")
        if document.name in reserved_names:
            return self._reject(candidate, "REQUIRED_SKILL_PROTECTED", "RAG cannot replace a required local Skill")
        if mode not in document.modes:
            return self._reject(candidate, "MODE_INCOMPATIBLE", f"Skill does not support mode {mode}")
        if publication.compatible_solver_ids and definition.id not in publication.compatible_solver_ids:
            return self._reject(candidate, "SOLVER_INCOMPATIBLE", "Skill publication excludes this SolverDefinition")
        if intent and publication.compatible_intent_kinds and intent.kind not in publication.compatible_intent_kinds:
            return self._reject(candidate, "INTENT_INCOMPATIBLE", "Skill publication excludes this Intent kind")
        required = set(document.required_capabilities)
        if not required.issubset(registry_names):
            return self._reject(candidate, "CAPABILITY_UNKNOWN", "Skill requires a capability absent from the registry")
        definition_capabilities = set(definition.required_capabilities)
        if not required.issubset(definition_capabilities):
            return self._reject(candidate, "CAPABILITY_SOLVER_DENIED", "Skill capability is outside SolverDefinition")
        if not required.issubset(available) or not required.issubset(policy_allowed):
            return self._reject(candidate, "CAPABILITY_POLICY_DENIED", "Skill capability is outside ToolPolicy")
        flags = set(candidate.safety_flags) | set(revision.metadata.get("safety_flags") or ())
        if flags.intersection({"prompt_injection", "authorization_escalation"}):
            return self._reject(candidate, "UNSAFE_INSTRUCTIONS", "Skill contains injection or authorization escalation markers")
        query = " ".join((
            definition.id, *definition.specialties, *definition.default_skill_tags,
            intent.kind if intent else "", intent.objective if intent else "",
        ))
        overlap = _match_score(query, document)
        if document.name not in definition.required_skill_names and overlap <= 0:
            return self._reject(candidate, "INTENT_IRRELEVANT", "Skill is not relevant to Solver or Intent semantics")
        reasons = [f"published revision: {revision.id}", f"retrieval score: {candidate.retrieval_score:.4f}"]
        if overlap:
            reasons.append(f"solver/intent relevance score: {overlap}")
        return document, publication.priority, tuple(reasons)

    def _candidate(self, item, pack) -> SkillCandidate:
        revision = self.repository.get_revision(item.revision_id)
        publication = self.repository.get_skill_publication(item.revision_id)
        payload = revision.metadata.get("skill_document") if revision else None
        if not isinstance(payload, dict):
            payload = {
                "name": str(item.metadata.get("skill_name") or "invalid-candidate"),
                "version": str(item.metadata.get("skill_version") or "unknown"),
                "modes": ["ctf"], "tags": [], "required_capabilities": [],
            }
        return SkillCandidate(
            id=f"skillcand_{item.hit_id.removeprefix('hit_')}",
            retrieval_run_id=pack.retrieval_run_id,
            index_snapshot_id=pack.index_snapshot_id,
            knowledge_base_id=item.knowledge_base_id,
            source_id=item.source_id,
            document_id=item.document_id,
            revision_id=item.revision_id,
            owner=item.owner.model_dump(mode="json"),
            name=str(payload.get("name") or "invalid-candidate"),
            version=str(payload.get("version") or "unknown"),
            modes=tuple(payload.get("modes") or ("ctf",)),
            tags=tuple(payload.get("tags") or ()),
            required_capabilities=tuple(payload.get("required_capabilities") or ()),
            content_sha256=str(
                (revision.metadata.get("skill_body_sha256") if revision else None)
                or item.metadata.get("skill_body_sha256")
                or "0" * 64
            ),
            retrieval_score=item.rerank_score,
            trust_level=item.trust_level,
            publication_status=publication.status if publication else "draft",
            safety_flags=item.safety_flags,
        )

    @staticmethod
    def _reject(candidate: SkillCandidate, code: str, reason: str) -> SkillCandidateRejection:
        return SkillCandidateRejection(candidate_id=candidate.id, code=code, reason=reason)

    def _version_release_key(self, item: ApprovedSkillCandidate):
        publication = self.repository.get_skill_publication(item.candidate.revision_id)
        if publication is None:
            return (item.priority, "", "", item.document.version)
        return (
            publication.priority,
            publication.created_at,
            publication.id,
            item.document.version,
        )

    def _emit_events(self, decision, candidates, approved) -> None:
        if self.events is None:
            return
        self.events.append_agent_event(
            decision.task_id,
            "SKILL_RETRIEVAL_COMPLETED",
            {
                "retrieval_run_ids": list(decision.retrieval_run_ids),
                "index_snapshot_ids": list(decision.index_snapshot_ids),
                "candidate_ids": [item.id for item in candidates],
            },
            solver_id=decision.solver_id,
            intent_id=decision.intent_id,
        )
        for rejected in decision.rejected_candidates:
            candidate = next(item for item in candidates if item.id == rejected.candidate_id)
            self.events.append_agent_event(
                decision.task_id,
                "SKILL_CANDIDATE_REJECTED",
                {
                    "decision_id": decision.id,
                    "candidate_id": candidate.id,
                    "retrieval_run_id": candidate.retrieval_run_id,
                    "index_snapshot_id": candidate.index_snapshot_id,
                    "document_id": candidate.document_id,
                    "revision_id": candidate.revision_id,
                    "content_sha256": candidate.content_sha256,
                    "code": rejected.code,
                    "reason": rejected.reason,
                },
                solver_id=decision.solver_id,
                intent_id=decision.intent_id,
            )
        self.events.append_agent_event(
            decision.task_id,
            "SKILL_SELECTION_DECIDED",
            decision.model_dump(mode="json"),
            solver_id=decision.solver_id,
            intent_id=decision.intent_id,
        )


def _owner_rejection(resource: OwnerScope, *, task_id, solver_id, workspace_id, policy):
    if resource.scope not in policy.allowed_owner_scopes:
        return "Owner scope is not allowed by Skill RetrievalPolicy"
    if resource.scope == "global":
        return ""
    if resource.scope == "workspace":
        return "" if workspace_id and resource.workspace_id == workspace_id else "Cross-workspace Skill access is denied"
    if resource.scope == "task":
        return "" if resource.task_id == task_id else "Cross-task Skill access is denied"
    if resource.task_id != task_id or resource.solver_id != solver_id:
        return "Cross-solver Skill access is denied"
    return ""


def _match_score(query: str, document: SkillDocument) -> int:
    tokens = set(re.findall(r"[a-z0-9_.-]{3,}", query.casefold()))
    content = " ".join((document.name, *document.tags, document.body[:2_000])).casefold()
    return sum(token in content for token in tokens)


__all__ = [
    "ApprovedSkillCandidate", "SkillCandidateActivationResult",
    "SkillCandidateActivationService",
]
