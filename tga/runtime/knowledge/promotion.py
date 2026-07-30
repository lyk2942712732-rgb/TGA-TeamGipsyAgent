"""Conflict-aware requests to promote candidate Knowledge to Task scope."""

from __future__ import annotations

import hashlib

from tga.domain.knowledge import KnowledgePromotionProposal
from tga.evidence.database import utc_now
from tga.runtime.knowledge.conflicts import KnowledgeConflictDetector


class KnowledgePromotionService:
    def __init__(self, repositories, *, detector=None) -> None:
        self.repositories = repositories
        self.detector = detector or KnowledgeConflictDetector()

    def request_task_promotion(
        self,
        *,
        knowledge_item_id: str,
        proposed_by_solver_id: str,
        rationale: str,
    ) -> KnowledgePromotionProposal:
        item = self.repositories.knowledge.get_knowledge(knowledge_item_id)
        if item is None:
            raise KeyError(f"knowledge not found: {knowledge_item_id}")
        existing = self.repositories.knowledge.list_knowledge(item.task_id)
        conflict = self.detector.detect(item, existing)
        digest = hashlib.sha256(
            f"{item.task_id}\0{item.id}\0task".encode()
        ).hexdigest()[:24]
        proposal = KnowledgePromotionProposal(
            id=f"knowledge_promotion_{digest}",
            task_id=item.task_id,
            knowledge_item_id=item.id,
            from_scope=item.scope,
            from_target_id=item.target_id,
            to_scope="task",
            to_target_id=None,
            proposed_by_solver_id=proposed_by_solver_id,
            rationale=rationale,
            evidence_claim_ids=item.evidence_claim_ids,
            status="pending",
            created_at=utc_now(),
            provenance={
                "knowledge_conflict_id": conflict.id if conflict else None,
                "review_required": True,
            },
        )
        with self.repositories.transaction():
            if conflict is not None and not any(
                known.id == conflict.id
                for known in self.repositories.knowledge.list_conflicts(
                    item.task_id
                )
            ):
                self.repositories.knowledge.add_conflict(conflict)
                self.repositories.events.append_agent_event(
                    item.task_id,
                    "KNOWLEDGE_CONFLICT_DETECTED",
                    {
                        "conflict_id": conflict.id,
                        "knowledge_item_ids": list(conflict.knowledge_item_ids),
                        "subject": item.subject,
                    },
                    solver_id=proposed_by_solver_id,
                )
            if not any(
                known.id == proposal.id
                for known in self.repositories.knowledge.list_promotions(
                    item.task_id
                )
            ):
                self.repositories.knowledge.add_promotion(proposal)
            self.repositories.events.append_agent_event(
                item.task_id,
                "KNOWLEDGE_PROMOTION_QUEUED",
                {
                    "proposal_id": proposal.id,
                    "knowledge_item_id": item.id,
                    "conflict_id": conflict.id if conflict else None,
                },
                solver_id=proposed_by_solver_id,
            )
        return proposal


__all__ = ["KnowledgePromotionService"]
