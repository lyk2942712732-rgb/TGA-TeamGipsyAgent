"""Deterministic structured Knowledge conflict detection."""

from __future__ import annotations

import hashlib

from tga.domain.knowledge import KnowledgeConflict
from tga.evidence.database import utc_now


class KnowledgeConflictDetector:
    def detect(self, candidate, existing_items) -> KnowledgeConflict | None:
        if not candidate.subject:
            return None
        conflicts = [
            item
            for item in existing_items
            if item.id != candidate.id
            and item.subject == candidate.subject
            and item.status not in {"rejected", "superseded"}
            and self._normalized_value(item) != self._normalized_value(candidate)
        ]
        if not conflicts:
            return None
        item_ids = tuple(sorted({candidate.id, *(item.id for item in conflicts)}))
        digest = hashlib.sha256(
            f"{candidate.task_id}\0{candidate.subject}\0{'|'.join(item_ids)}".encode()
        ).hexdigest()[:24]
        return KnowledgeConflict(
            id=f"knowledge_conflict_{digest}",
            task_id=candidate.task_id,
            knowledge_item_ids=list(item_ids),
            description=(
                f"Conflicting candidate values for subject {candidate.subject}."
            ),
            status="open",
            created_at=utc_now(),
            provenance={
                "detector": "structured_subject_value_v1",
                "subject": candidate.subject,
            },
        )

    @staticmethod
    def _normalized_value(item) -> str:
        return " ".join(str(item.value or item.content).casefold().split())


__all__ = ["KnowledgeConflictDetector"]
