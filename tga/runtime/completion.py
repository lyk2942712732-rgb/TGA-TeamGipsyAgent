"""Evidence-backed challenge completion gate."""

from __future__ import annotations

from dataclasses import dataclass

from tga.contracts import ArtifactRecord, TGATask
from tga.core.flag_gate import flag_ok
from tga.evidence.store import EvidenceStore
from tga.runtime.challenge_state import ChallengeStateMachine
from tga.runtime.events import EventStore


@dataclass(frozen=True)
class CompletionDecision:
    solved: bool
    value: str | None = None
    evidence_artifact_id: str | None = None
    reason: str = ""


class CompletionGate:
    """Confirm candidates only when format and artifact provenance agree."""

    def __init__(self, store: EvidenceStore, *, artifact_text):
        self.store = store
        self.artifact_text = artifact_text

    def evaluate(
        self,
        *,
        task: TGATask,
        candidate: str,
        artifacts: list[ArtifactRecord],
        solver_id: str,
    ) -> CompletionDecision:
        evidence = next(
            (
                artifact
                for artifact in artifacts
                if flag_ok(
                    candidate,
                    flag_format=task.flag_format or "",
                    artifact_texts=[self.artifact_text(task.id, artifact)],
                )
            ),
            None,
        )
        events = EventStore(self.store)
        if evidence is None:
            events.append(
                task.id,
                "GATE_REJECTED",
                {"kind": "flag", "value": candidate, "reason": "flag_format_or_provenance_failed"},
                solver_id=solver_id,
            )
            return CompletionDecision(solved=False, value=candidate, reason="flag_format_or_provenance_failed")
        self.store.add_flag(task.id, candidate, evidence.id)
        events.append(
            task.id,
            "FLAG_CONFIRMED",
            {"value": candidate, "evidence_artifact_id": evidence.id},
            solver_id=solver_id,
        )
        ChallengeStateMachine(self.store).transition(
            task.id,
            "solved",
            reason="confirmed_flag",
            proof_artifact_id=evidence.id,
            solver_id=solver_id,
        )
        return CompletionDecision(solved=True, value=candidate, evidence_artifact_id=evidence.id, reason="confirmed_flag")
