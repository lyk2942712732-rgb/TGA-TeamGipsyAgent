"""Evidence-backed challenge completion gate."""

from __future__ import annotations

from dataclasses import dataclass

from tga.contracts import ArtifactRecord, TGATask
from tga.core.flag_gate import flag_ok
from tga.evidence.store import EvidenceStore


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
        ordered_artifacts = sorted(artifacts, key=lambda item: (item.kind != "http_body", item.created_at, item.id))
        evidence = next(
            (
                artifact
                for artifact in ordered_artifacts
                if flag_ok(
                    candidate,
                    flag_format=task.flag_format or "",
                    artifact_texts=[self.artifact_text(task.id, artifact)],
                )
            ),
            None,
        )
        if evidence is None:
            return CompletionDecision(solved=False, value=candidate, reason="flag_format_or_provenance_failed")
        return CompletionDecision(
            solved=True,
            value=candidate,
            evidence_artifact_id=evidence.id,
            reason="confirmed_flag",
        )

    def confirm(
        self, *, task: TGATask, candidate: str, evidence: ArtifactRecord,
        solver_id: str, reason: str,
    ) -> CompletionDecision:
        """Persist a flag after a local or configured remote verifier accepts it."""
        if evidence.task_id != task.id or self.store.get_artifact(evidence.id) is None:
            return CompletionDecision(solved=False, value=candidate, reason="artifact_provenance_failed")
        return CompletionDecision(solved=True, value=candidate, evidence_artifact_id=evidence.id, reason=reason)
