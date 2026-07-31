"""Policy-filtered, auditable retrieval with keyword fallback."""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from tga.domain.retrieval import (
    DocumentChunk,
    OwnerScope,
    RetrievalHit,
    RetrievalPolicy,
    RetrievalRequest,
    RetrievalRun,
    RetrievedContextItem,
    RetrievedContextPack,
)


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _terms(value: str) -> list[str]:
    return [item.casefold() for item in re.findall(r"[\w.-]+", value) if len(item) > 1]


def _injection_flags(content: str) -> tuple[str, ...]:
    folded = content.casefold()
    markers = (
        "ignore previous instructions", "ignore all previous instructions",
        "system prompt", "developer prompt", "do not follow the system",
        "run this command", "execute this command", "rm -rf",
    )
    return ("prompt_injection",) if any(item in folded for item in markers) else ()


class RetrievalService:
    """First local backend: BM25-like keyword search and optional embeddings."""

    def __init__(self, repository, embedding_gateway=None, event_repository=None) -> None:
        self.repository = repository
        self.embedding_gateway = embedding_gateway
        self.events = event_repository

    def retrieve(
        self, request: RetrievalRequest, policy: RetrievalPolicy
    ) -> RetrievedContextPack:
        snapshot = self.repository.get_snapshot(request.index_snapshot_id)
        if snapshot is None:
            raise KeyError(f"IndexSnapshot not found: {request.index_snapshot_id}")
        requested_kbs = request.knowledge_base_ids or snapshot.knowledge_base_ids
        if not set(requested_kbs).issubset(set(snapshot.knowledge_base_ids)):
            raise PermissionError("RetrievalRequest KnowledgeBase is outside the fixed snapshot")
        if (
            policy.allowed_knowledge_base_ids is not None
            and not set(requested_kbs).issubset(set(policy.allowed_knowledge_base_ids))
        ):
            raise PermissionError("RetrievalPolicy denies a requested KnowledgeBase")
        chunks = self.repository.list_chunks(snapshot.chunk_ids)
        sources = {
            item.id: item
            for item in self.repository.list_sources(knowledge_base_ids=requested_kbs)
        }
        candidates = [
            item for item in chunks
            if item.knowledge_base_id in requested_kbs
            and item.channel in request.channels
            and (source := sources.get(item.source_id)) is not None
            and self._allowed(item, source, request, policy)
        ]
        query = (request.rewritten_query or request.query).strip()
        effective_method = request.method
        vectors: dict[str, float] = {}
        if request.method in {"vector", "hybrid"}:
            configured_model = getattr(self.embedding_gateway, "model", None)
            if snapshot.embedding_model and configured_model == snapshot.embedding_model:
                try:
                    vectors = self._vector_scores(query, candidates)
                except Exception:
                    vectors = {}
            if not vectors:
                effective_method = "keyword"
        keyword_scores = self._keyword_scores(query, candidates)
        ranked: list[tuple[float, float, DocumentChunk, tuple[str, ...]]] = []
        for item in candidates:
            keyword = keyword_scores.get(item.id, 0.0)
            vector = vectors.get(item.id, 0.0)
            retrieval_score = (
                vector if effective_method == "vector"
                else keyword if effective_method == "keyword"
                else keyword * 0.65 + vector * 0.35
            )
            if retrieval_score <= 0:
                continue
            trust_boost = {
                "authoritative": 0.2, "trusted": 0.1, "unverified": 0.0,
            }[item.trust_level]
            rerank_score = retrieval_score + trust_boost
            flags = tuple(dict.fromkeys((*item.safety_flags, *_injection_flags(item.content))))
            ranked.append((retrieval_score, rerank_score, item, flags))
        ranked.sort(key=lambda value: (-value[1], -value[0], value[2].id))
        ranked = ranked[:policy.max_results]

        run_id = f"run_{hashlib.sha256(request.id.encode()).hexdigest()[:32]}"
        created_at = request.created_at or _now()
        selected: list[RetrievedContextItem] = []
        hits: list[RetrievalHit] = []
        remaining = policy.max_context_tokens
        any_truncated = False
        for rank, (score, rerank, chunk, flags) in enumerate(ranked, start=1):
            overhead_tokens = 48
            selected_for_context = remaining > overhead_tokens
            truncated = False
            content = chunk.content
            content_tokens = chunk.token_count
            allowed_content_tokens = max(0, remaining - overhead_tokens)
            if selected_for_context and content_tokens > allowed_content_tokens:
                content = content[: max(1, allowed_content_tokens * 4)]
                content_tokens = allowed_content_tokens
                truncated = True
                any_truncated = True
            if not selected_for_context:
                any_truncated = True
            hit_id = f"hit_{hashlib.sha256(f'{run_id}:{chunk.id}'.encode()).hexdigest()[:32]}"
            hit = RetrievalHit(
                id=hit_id,
                retrieval_run_id=run_id,
                owner=request.owner,
                chunk_id=chunk.id,
                retrieval_score=round(score, 8),
                rerank_score=round(rerank, 8),
                rank=rank,
                selected_for_context=selected_for_context,
                safety_flags=flags,
                created_at=created_at,
            )
            hits.append(hit)
            if selected_for_context:
                selected.append(RetrievedContextItem(
                    hit_id=hit.id,
                    owner=chunk.owner,
                    chunk_id=chunk.id,
                    knowledge_base_id=chunk.knowledge_base_id,
                    source_id=chunk.source_id,
                    document_id=chunk.document_id,
                    revision_id=chunk.revision_id,
                    channel=chunk.channel,
                    label=self._label(chunk.channel),
                    trust_level=chunk.trust_level,
                    content=self._wrap_untrusted(content, flags),
                    locator=chunk.locator,
                    retrieval_score=hit.retrieval_score,
                    rerank_score=hit.rerank_score,
                    rank=rank,
                    token_count=overhead_tokens + max(1, content_tokens),
                    truncated=truncated,
                    safety_flags=flags,
                    metadata=chunk.metadata,
                ))
                remaining -= overhead_tokens + max(1, content_tokens)
        run = RetrievalRun(
            id=run_id,
            owner=request.owner,
            task_id=request.task_id,
            solver_id=request.solver_id,
            intent_id=request.intent_id,
            query=request.query,
            rewritten_query=query,
            index_snapshot_id=snapshot.id,
            filters=request.filters,
            requested_method=request.method,
            method=effective_method,
            knowledge_base_ids=tuple(requested_kbs),
            channels=request.channels,
            policy_snapshot=policy.model_dump(mode="json"),
            created_at=created_at,
        )
        self.repository.save_run(run, hits)
        if self.events is not None and request.task_id is not None:
            self.events.append_agent_event(
                request.task_id,
                "RETRIEVAL_COMPLETED",
                {
                    "retrieval_run_id": run.id,
                    "index_snapshot_id": run.index_snapshot_id,
                    "method": run.method,
                    "requested_method": run.requested_method,
                    "hit_count": len(hits),
                    "selected_count": sum(hit.selected_for_context for hit in hits),
                },
                solver_id=request.solver_id,
                intent_id=request.intent_id,
            )
        total_tokens = sum(item.token_count for item in selected)
        return RetrievedContextPack(
            retrieval_run_id=run.id,
            owner=request.owner,
            index_snapshot_id=snapshot.id,
            task_id=request.task_id,
            solver_id=request.solver_id,
            intent_id=request.intent_id,
            items=tuple(selected),
            total_tokens=total_tokens,
            max_context_tokens=policy.max_context_tokens,
            truncated=any_truncated,
            created_at=created_at,
        )

    def retrieve_for_context(
        self,
        *,
        task_id: str,
        solver_id: str,
        intent_id: str | None,
        query: str,
        policy: RetrievalPolicy,
        workspace_id: str | None = None,
    ) -> RetrievedContextPack | None:
        principal = OwnerScope(scope="solver", task_id=task_id, solver_id=solver_id)
        binding_owner = OwnerScope(scope="task", task_id=task_id)
        binding = self.repository.get_snapshot_binding(binding_owner, "context")
        snapshot_id = binding.index_snapshot_id if binding is not None else None
        pack = self.retrieve_for_principal(
            owner=principal,
            task_id=task_id,
            solver_id=solver_id,
            intent_id=intent_id,
            query=query,
            policy=policy,
            channels=("reference", "task_artifact"),
            workspace_id=workspace_id,
            request_prefix="context",
            snapshot_id=snapshot_id,
        )
        if pack is not None and binding is None:
            self.repository.bind_snapshot(
                owner=binding_owner,
                purpose="context",
                snapshot_id=pack.index_snapshot_id,
                expected_snapshot_id=None,
                updated_at=pack.created_at,
            )
        return pack

    def retrieve_skill_candidates(
        self,
        *,
        task_id: str,
        solver_id: str,
        intent_id: str | None,
        query: str,
        policy: RetrievalPolicy,
        workspace_id: str | None = None,
    ) -> RetrievedContextPack | None:
        """Discover Skill references without mixing them into model context."""
        principal = OwnerScope(scope="solver", task_id=task_id, solver_id=solver_id)
        binding = self.repository.get_snapshot_binding(principal, "skill_selection")
        snapshot_id = binding.index_snapshot_id if binding else self._latest_skill_snapshot_id(
            owner=principal, policy=policy, workspace_id=workspace_id
        )
        if snapshot_id is None:
            return None
        pack = self.retrieve_for_principal(
            owner=principal,
            task_id=task_id,
            solver_id=solver_id,
            intent_id=intent_id,
            query=query,
            policy=policy,
            channels=("skill",),
            workspace_id=workspace_id,
            request_prefix="skill_selection",
            snapshot_id=snapshot_id,
        )
        if pack is not None and binding is None:
            self.repository.bind_snapshot(
                owner=principal,
                purpose="skill_selection",
                snapshot_id=pack.index_snapshot_id,
                expected_snapshot_id=None,
                updated_at=pack.created_at,
            )
        return pack

    def _latest_skill_snapshot_id(
        self,
        *,
        owner: OwnerScope,
        policy: RetrievalPolicy,
        workspace_id: str | None,
    ) -> str | None:
        snapshots = [
            snapshot for snapshot in self.repository.list_snapshots()
            if self._owner_visible(
                snapshot.owner, owner, policy, workspace_id=workspace_id
            )
            and any(
                chunk.channel == "skill"
                for chunk in self.repository.list_chunks(snapshot.chunk_ids)
            )
        ]
        return snapshots[-1].id if snapshots else None

    def refresh_snapshot_binding(
        self, *, owner: OwnerScope, snapshot_id: str, purpose: str
    ):
        binding_owner = (
            OwnerScope(scope="task", task_id=owner.task_id)
            if purpose == "context" and owner.scope == "solver"
            else owner
        )
        snapshot = self.repository.get_snapshot(snapshot_id)
        if snapshot is None:
            raise KeyError(f"IndexSnapshot not found: {snapshot_id}")
        if not self._structurally_visible(snapshot.owner, binding_owner):
            raise PermissionError("IndexSnapshot owner is outside the binding principal")
        current = self.repository.get_snapshot_binding(binding_owner, purpose)
        return self.repository.bind_snapshot(
            owner=binding_owner,
            purpose=purpose,
            snapshot_id=snapshot_id,
            expected_snapshot_id=current.index_snapshot_id if current else None,
            updated_at=_now(),
        )

    def retrieve_for_principal(
        self,
        *,
        owner: OwnerScope,
        task_id: str | None,
        solver_id: str | None,
        intent_id: str | None,
        query: str,
        policy: RetrievalPolicy,
        channels=("reference",),
        knowledge_base_ids: tuple[str, ...] = (),
        snapshot_id: str | None = None,
        method: str = "hybrid",
        filters: dict[str, Any] | None = None,
        workspace_id: str | None = None,
        request_prefix: str = "search",
    ) -> RetrievedContextPack | None:
        snapshots = [
            item for item in self.repository.list_snapshots()
            if self._owner_visible(item.owner, owner, policy, workspace_id=workspace_id)
        ]
        if snapshot_id:
            snapshots = [item for item in snapshots if item.id == snapshot_id]
        if not snapshots:
            return None
        snapshot = snapshots[-1]
        requested_kbs = knowledge_base_ids or snapshot.knowledge_base_ids
        return self.retrieve(RetrievalRequest(
            id=f"{request_prefix}_{uuid4().hex}",
            owner=owner,
            task_id=task_id,
            solver_id=solver_id,
            intent_id=intent_id,
            query=query,
            index_snapshot_id=snapshot.id,
            channels=tuple(channels),
            knowledge_base_ids=tuple(requested_kbs),
            filters={**(filters or {}), **({"workspace_id": workspace_id} if workspace_id else {})},
            method=method,
            created_at=_now(),
        ), policy)

    @staticmethod
    def _keyword_scores(query: str, chunks: list[DocumentChunk]) -> dict[str, float]:
        terms = _terms(query)
        if not terms or not chunks:
            return {}
        documents = {item.id: Counter(_terms(item.content)) for item in chunks}
        lengths = {item_id: sum(counts.values()) for item_id, counts in documents.items()}
        average = sum(lengths.values()) / max(1, len(lengths))
        scores: dict[str, float] = {}
        for term in terms:
            frequency = sum(1 for counts in documents.values() if counts.get(term))
            idf = math.log(1 + (len(documents) - frequency + 0.5) / (frequency + 0.5))
            for item_id, counts in documents.items():
                tf = counts.get(term, 0)
                if not tf:
                    continue
                denominator = tf + 1.2 * (1 - 0.75 + 0.75 * lengths[item_id] / max(1, average))
                scores[item_id] = scores.get(item_id, 0.0) + idf * tf * 2.2 / denominator
        return scores

    def _vector_scores(self, query: str, chunks: list[DocumentChunk]) -> dict[str, float]:
        if self.embedding_gateway is None or not chunks:
            return {}
        values = self.embedding_gateway.embed([query, *(item.content for item in chunks)])
        if len(values) != len(chunks) + 1:
            raise ValueError("EmbeddingGateway returned the wrong vector count")
        query_vector = values[0]
        return {
            item.id: max(0.0, self._cosine(query_vector, values[index + 1]))
            for index, item in enumerate(chunks)
        }

    @staticmethod
    def _cosine(left, right) -> float:
        numerator = sum(float(a) * float(b) for a, b in zip(left, right, strict=False))
        left_norm = math.sqrt(sum(float(item) ** 2 for item in left))
        right_norm = math.sqrt(sum(float(item) ** 2 for item in right))
        return numerator / (left_norm * right_norm) if left_norm and right_norm else 0.0

    def _allowed(self, chunk, source, request, policy) -> bool:
        if source.status != "active" or source.owner != chunk.owner:
            return False
        if chunk.owner.scope not in policy.allowed_owner_scopes:
            return False
        if policy.allowed_source_ids is not None and source.id not in policy.allowed_source_ids:
            return False
        if policy.allowed_source_kinds is not None and source.kind not in policy.allowed_source_kinds:
            return False
        if source.trust_level not in policy.allowed_trust_levels:
            return False
        if chunk.channel == "task_artifact" and not policy.task_artifact_access:
            return False
        filters = request.filters
        if filters.get("source_ids") and source.id not in filters["source_ids"]:
            return False
        if filters.get("trust_levels") and source.trust_level not in filters["trust_levels"]:
            return False
        return self._owner_visible(
            chunk.owner,
            request.owner,
            policy,
            workspace_id=str(filters.get("workspace_id") or "") or None,
        )

    @staticmethod
    def _owner_visible(
        resource: OwnerScope, principal: OwnerScope, policy: RetrievalPolicy,
        *, workspace_id: str | None = None,
    ) -> bool:
        if resource.scope not in policy.allowed_owner_scopes:
            return False
        if resource.scope == "global":
            return True
        if resource.scope == "workspace":
            principal_workspace = principal.workspace_id or workspace_id
            return bool(principal_workspace and principal_workspace == resource.workspace_id)
        principal_task = principal.task_id
        if resource.scope == "task":
            return bool(principal_task and principal_task == resource.task_id)
        if not principal_task or principal_task != resource.task_id:
            return False
        return policy.cross_solver_access or principal.solver_id == resource.solver_id

    @staticmethod
    def _structurally_visible(resource: OwnerScope, principal: OwnerScope) -> bool:
        if resource.scope == "global":
            return True
        if resource.scope == "workspace":
            return (
                principal.scope == "workspace"
                and principal.workspace_id == resource.workspace_id
            )
        if resource.scope == "task":
            return bool(principal.task_id and principal.task_id == resource.task_id)
        return (
            principal.scope == "solver"
            and principal.task_id == resource.task_id
            and principal.solver_id == resource.solver_id
        )

    @staticmethod
    def _label(channel: str) -> str:
        return {
            "skill": "[RETRIEVED SKILL CANDIDATE — NOT ACTIVE]",
            "reference": "[RETRIEVED REFERENCE — NOT TASK EVIDENCE]",
            "task_artifact": "[RETRIEVED TASK ARTIFACT — CANDIDATE EVIDENCE]",
        }[channel]

    @staticmethod
    def _wrap_untrusted(content: str, flags: tuple[str, ...]) -> str:
        safety = ",".join(flags) if flags else "untrusted_reference_data"
        return (
            f"<UNTRUSTED_RETRIEVED_DATA safety={safety}>\n"
            f"{content}\n</UNTRUSTED_RETRIEVED_DATA>"
        )


__all__ = ["RetrievalService"]
