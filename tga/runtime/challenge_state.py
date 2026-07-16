"""Manager-owned challenge state machine for the v2 runtime."""

from __future__ import annotations

from tga.contracts import ChallengeContract, ChallengeStatus, TGATask
from tga.evidence.store import EvidenceStore, utc_now
from tga.runtime.events import EventStore


class ChallengeStateMachine:
    """Persist and audit challenge transitions.

    ``solved`` is terminal and can only be entered with an artifact-backed
    flag proof.  There is intentionally no submission state in this machine.
    """

    _allowed: dict[ChallengeStatus, set[ChallengeStatus]] = {
        "unknown": {"active", "blocked", "expired"},
        "active": {"solved", "blocked", "expired"},
        "blocked": {"active", "expired"},
        "expired": set(),
        "solved": set(),
    }

    def __init__(self, store: EvidenceStore):
        self.store = store

    def ensure(self, task: TGATask) -> ChallengeContract:
        current = self.store.get_challenge(task.id)
        if current is not None:
            return current
        challenge = ChallengeContract(
            task_id=task.id,
            entry_url=task.target,
            allowed_origins=list(task.scope),
            status="unknown",
            flag_format=task.flag_format,
        )
        self.store.upsert_challenge(challenge)
        return challenge

    def activate(self, task: TGATask, *, reason: str = "session_started") -> ChallengeContract:
        current = self.ensure(task)
        if current.status == "active":
            return current
        return self.transition(task.id, "active", reason=reason)

    def transition(
        self,
        task_id: str,
        status: ChallengeStatus,
        *,
        reason: str,
        proof_artifact_id: str | None = None,
        solver_id: str | None = None,
    ) -> ChallengeContract:
        current = self.store.get_challenge(task_id)
        if current is None:
            raise KeyError(f"challenge not found: {task_id}")
        if status == current.status:
            return current
        if status not in self._allowed[current.status]:
            raise ValueError(f"invalid challenge transition {current.status} -> {status}")
        if status == "solved":
            artifact = self.store.get_artifact(proof_artifact_id or "")
            if artifact is None or artifact.task_id != task_id:
                raise ValueError("solved challenge requires a task-owned completion proof artifact")
        updated = current.model_copy(
            update={
                "status": status,
                "status_reason": reason[:280],
                "completion_proof_artifact_id": proof_artifact_id if status == "solved" else current.completion_proof_artifact_id,
                "solved_at": utc_now() if status == "solved" else current.solved_at,
            }
        )
        self.store.upsert_challenge(updated)
        EventStore(self.store).append(
            task_id,
            "CHALLENGE_STATUS_CHANGED",
            {
                "from": current.status,
                "status": updated.status,
                "reason": updated.status_reason,
                "completion_proof_artifact_id": updated.completion_proof_artifact_id,
            },
            solver_id=solver_id,
        )
        return updated
