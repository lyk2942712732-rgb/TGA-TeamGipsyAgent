"""Task-completion proposal separated from ordinary capability execution."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from tga.domain.evidence.claims import EvidenceClaim
from tga.domain.evidence.locators import EvidenceLocator
from tga.domain.knowledge.items import KnowledgeItem
from tga.evidence.database import utc_now
from tga.infrastructure.persistence import PersistenceBundle
from tga.runtime.completion_validators import (
    CompletionValidationContext,
    TaskCompletionSubmission,
    validator_for,
)
from tga.runtime.coordinator import SessionOutcome


def _safe(content: str) -> str:
    text = re.sub(r"```[\s\S]*?```", "[code omitted]", content)
    text = re.sub(
        r"(?i)\b(authorization|cookie|token|secret|password|api[_-]?key)\s*[:=]\s*\S+",
        r"\1=[REDACTED]",
        text,
    )
    return text[:2_000]


class TaskCompletionHandler:
    def __init__(self, state, artifacts) -> None:
        self.state = state
        self.task = state.task
        self.store = state.store
        self.solver_id = state.solver_id
        self.remote_flag_verifier = state.remote_flag_verifier
        self.artifacts = artifacts

    def propose_task_completion(self, arguments: dict[str, Any]) -> dict[str, Any]:
        session = self.store.get_session(self.task.id)
        turn = session.turn_count if session else 0
        raw_evidence = arguments.get("evidence_artifact_ids")
        cited = [str(item) for item in raw_evidence] if isinstance(raw_evidence, list) else []
        raw_claims = arguments.get("claims")
        if isinstance(raw_claims, list):
            cited.extend(
                str(artifact_id)
                for claim in raw_claims
                if isinstance(claim, dict)
                and isinstance(claim.get("evidence_artifact_ids"), list)
                for artifact_id in claim["evidence_artifact_ids"]
            )
        cited = list(dict.fromkeys(cited))
        self.store.append_agent_event(
            self.task.id,
            "FINISH_ATTEMPTED",
            self._event(
                turn=turn,
                code="VALIDATION_PENDING",
                missing=[],
                evidence_artifact_ids=cited,
                terminal=False,
            ),
            solver_id=self.solver_id,
        )
        try:
            if self.task.mode != "ctf" and "flag" in arguments:
                raise ValueError("flag is not a valid task completion proposal field outside CTF mode")
            submission = TaskCompletionSubmission.model_validate(arguments)
        except Exception as exc:
            result = {
                "accepted": False,
                "code": "INVALID_FINISH_SUBMISSION",
                "message": _safe(str(exc))[:1_200],
                "missing": ["valid task completion proposal arguments"],
                "evidence_artifact_ids": cited,
                "retryable": True,
                "details": {},
            }
            self.state.last_finish_rejection = result
            self.store.append_agent_event(
                self.task.id,
                "FINISH_REJECTED",
                self._event(
                    turn=turn,
                    code=result["code"],
                    missing=result["missing"],
                    evidence_artifact_ids=cited,
                    terminal=False,
                ),
                solver_id=self.solver_id,
            )
            return {"ok": False, "terminal": False, "validation": result, **result}

        result_model = validator_for(self.task.mode).validate(
            context=CompletionValidationContext(
                task=self.task,
                solver_id=self.solver_id,
                store=self.store,
                artifact_text=self.artifacts.text,
                remote_flag_verifier=self.remote_flag_verifier,
            ),
            submission=submission,
        )
        result = result_model.model_dump(mode="json")
        event_payload = self._event(
            turn=turn,
            code=result_model.code,
            missing=result_model.missing,
            evidence_artifact_ids=result_model.evidence_artifact_ids,
            terminal=result_model.accepted,
        )
        if not result_model.accepted:
            self.state.last_finish_rejection = result
            self.store.append_agent_event(
                self.task.id,
                "FINISH_REJECTED",
                event_payload,
                solver_id=self.solver_id,
            )
            return {"ok": False, "terminal": False, "validation": result, **result}

        self.state.last_finish_rejection = None
        proof_id = (
            result_model.evidence_artifact_ids[0]
            if result_model.evidence_artifact_ids else ""
        )
        self._record_validated_completion(
            submission=submission,
            validator_code=result_model.code,
            proof_id=proof_id,
        )
        self.state.terminal_outcome = SessionOutcome(
            status="completed",
            stop_reason="finish_accepted",
            turn_count=turn,
            summary=_safe(submission.summary),
            evidence_artifact_ids=result_model.evidence_artifact_ids,
            details={
                "coverage": [_safe(item) for item in submission.coverage],
                "limitations": [_safe(item) for item in submission.limitations],
                "claims": [item.model_dump(mode="json") for item in submission.claims],
                "structured_result": {
                    "flag": submission.flag,
                    "proof_artifact_id": proof_id,
                    "verification": result_model.details.get("verification")
                    or "completion_validator",
                } if submission.flag else {},
                "validator_code": result_model.code,
                "terminal": True,
            },
        )
        return {
            "ok": True,
            "terminal": True,
            "status": "completed",
            "validation": result,
            **result,
        }

    def _record_validated_completion(
        self,
        *,
        submission: TaskCompletionSubmission,
        validator_code: str,
        proof_id: str,
    ) -> None:
        """Promote only evidence explicitly verified by the completion gate."""
        flag = (submission.flag or "").strip()
        if not flag or not proof_id or validator_code != "CTF_FLAG_VERIFIED":
            return
        artifact = self.store.get_artifact(proof_id)
        if artifact is None or artifact.task_id != self.task.id:
            return
        content = self.artifacts.text(self.task.id, artifact)
        start = content.find(flag)
        if start < 0:
            return
        now = utc_now()
        digest = hashlib.sha256(
            f"{self.task.id}\0{proof_id}\0{flag}".encode("utf-8")
        ).hexdigest()[:20]
        claim = EvidenceClaim(
            id=f"claim_completion_{digest}",
            task_id=self.task.id,
            statement=f"Validated CTF completion flag: {flag}",
            artifact_id=proof_id,
            locator=EvidenceLocator(
                kind="text_range",
                char_start=start,
                char_end=start + len(flag),
                text_quote=flag,
            ),
            status="confirmed",
            created_by_solver_id=self.solver_id,
            reviewed_by_solver_id=self.solver_id,
            created_at=now,
            reviewed_at=now,
            provenance={
                "source": "propose_task_completion",
                "validator_code": validator_code,
                "automatic_artifact_confirmation": False,
            },
        )
        repositories = PersistenceBundle(self.store)
        if repositories.evidence.get_evidence_claim(claim.id) is None:
            repositories.evidence.add_evidence_claim(claim)
            repositories.events.append_agent_event(
                self.task.id,
                "EVIDENCE_CLAIM_CREATED",
                {
                    "evidence_claim_id": claim.id,
                    "artifact_id": claim.artifact_id,
                    "status": claim.status,
                },
                solver_id=self.solver_id,
            )
        knowledge = KnowledgeItem(
            id=f"knowledge_completion_{digest}",
            task_id=self.task.id,
            scope="task",
            status="verified",
            kind="fact",
            content=claim.statement,
            evidence_claim_ids=[claim.id],
            created_by_solver_id=self.solver_id,
            created_at=now,
            provenance={
                "source": "propose_task_completion",
                "validator_code": validator_code,
            },
        )
        if not any(
            item.id == knowledge.id
            for item in repositories.knowledge.list_knowledge(self.task.id)
        ):
            repositories.knowledge.add_knowledge(knowledge)
        repositories.events.append_agent_event(
            self.task.id,
            "COMPLETION_EVIDENCE_CONFIRMED",
            {
                "claim_id": claim.id,
                "knowledge_id": knowledge.id,
                "artifact_id": proof_id,
                "validator_code": validator_code,
            },
            solver_id=self.solver_id,
        )

    def _event(
        self,
        *,
        turn: int,
        code: str,
        missing: list[str],
        evidence_artifact_ids: list[str],
        terminal: bool,
    ) -> dict[str, Any]:
        return {
            "task_id": self.task.id,
            "solver_id": self.solver_id,
            "mode": self.task.mode,
            "validator_code": code,
            "missing": [_safe(item) for item in missing[:32]],
            "evidence_artifact_ids": list(dict.fromkeys(evidence_artifact_ids))[:64],
            "turn": turn,
            "terminal": terminal,
        }


def propose_task_completion(
    handler: TaskCompletionHandler, arguments: dict[str, Any]
) -> dict[str, Any]:
    return handler.propose_task_completion(arguments)


__all__ = ["TaskCompletionHandler", "propose_task_completion"]
