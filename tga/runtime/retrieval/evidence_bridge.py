"""Explicit bridge from a task Artifact hit to candidate EvidenceClaim."""

from __future__ import annotations

import hashlib

from tga.domain.evidence import EvidenceClaim, EvidenceLocator
from tga.evidence.database import utc_now


class RetrievalEvidenceBridge:
    def __init__(self, repositories) -> None:
        self.repositories = repositories

    def create_candidate_claim(self, *, item, statement: str, solver_id: str) -> EvidenceClaim:
        if item.channel != "task_artifact":
            raise ValueError("only Task Artifact retrieval hits can create EvidenceClaims")
        artifact_id = str(item.metadata.get("artifact_id") or "")
        artifact = self.repositories.evidence.get_artifact(artifact_id)
        solver = self.repositories.solvers.get_solver(solver_id)
        if artifact is None or solver is None or artifact.task_id != solver.task_id:
            raise PermissionError("retrieved Artifact is outside the Solver task")
        hit = self.repositories.retrieval.get_hit(item.hit_id)
        if hit is None:
            raise KeyError(f"RetrievalHit not found: {item.hit_id}")
        chunk = self.repositories.retrieval.get_chunk(hit.chunk_id)
        if chunk is None or chunk.id != item.chunk_id:
            raise KeyError(f"DocumentChunk not found: {item.chunk_id}")
        run = self.repositories.retrieval.get_run(hit.retrieval_run_id)
        if run is None or run.task_id != solver.task_id:
            raise PermissionError("RetrievalRun is outside the Solver task")
        locator = self._evidence_locator(chunk.locator, chunk.content)
        claim_id = "claim_" + hashlib.sha256(
            f"{hit.id}:{statement}".encode()
        ).hexdigest()[:32]
        existing = self.repositories.evidence.get_evidence_claim(claim_id)
        if existing is not None:
            if (
                existing.task_id != solver.task_id
                or existing.artifact_id != artifact.id
                or existing.statement != statement
            ):
                raise RuntimeError("candidate claim identity collision")
            return existing
        claim = EvidenceClaim(
            id=claim_id,
            task_id=solver.task_id,
            statement=statement,
            artifact_id=artifact.id,
            locator=locator,
            status="candidate",
            created_by_solver_id=solver_id,
            created_at=utc_now(),
            provenance={
                "retrieval_run_id": run.id,
                "retrieval_hit_id": hit.id,
                "chunk_id": item.chunk_id,
                "retrieval_is_not_verification": True,
            },
        )
        self.repositories.evidence.add_evidence_claim(claim)
        self.repositories.events.append_agent_event(
            solver.task_id,
            "EVIDENCE_CLAIM_CREATED",
            {
                "evidence_claim_id": claim.id,
                "artifact_id": claim.artifact_id,
                "status": claim.status,
                "retrieval_run_id": run.id,
            },
            solver_id=solver_id,
            intent_id=run.intent_id,
        )
        return claim

    @staticmethod
    def _evidence_locator(locator, content: str) -> EvidenceLocator:
        if locator.kind == "text_range" and locator.char_start is not None and locator.char_end is not None:
            return EvidenceLocator(
                kind="text_range", char_start=locator.char_start,
                char_end=locator.char_end, text_quote=content[:8_000],
            )
        if locator.kind == "line_range" and locator.line_start and locator.line_end:
            return EvidenceLocator(
                kind="line_range", line_start=locator.line_start,
                line_end=locator.line_end, text_quote=content[:8_000],
            )
        if locator.kind == "json_path" and locator.json_path:
            return EvidenceLocator(
                kind="json_path", json_path=locator.json_path,
                text_quote=content[:8_000],
            )
        if locator.kind == "page" and locator.page:
            return EvidenceLocator(
                kind="page", page=locator.page, page_end=locator.page_end,
                text_quote=content[:8_000],
            )
        return EvidenceLocator(
            kind="legacy_whole_artifact",
            legacy_reason=f"retrieval locator {locator.kind} has no EvidenceLocator projection",
            text_quote=content[:8_000],
        )


__all__ = ["RetrievalEvidenceBridge"]
