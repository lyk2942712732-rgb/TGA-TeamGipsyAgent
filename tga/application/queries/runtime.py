"""Application queries returning transport-stable Phase-9 DTOs."""

from __future__ import annotations

from pathlib import Path

from tga.application.projections.models import (
    ApprovalPage,
    EventPage,
    EvidencePageResponse,
    IntentPage,
    LegacyRuntimeSnapshotResponse,
    RuntimeSnapshotResponse,
    SolverResponse,
    TeamResponse,
)
from tga.runtime.service import TaskRuntimeService


class RuntimeQueries:
    def __init__(self, *, run_root: str | Path) -> None:
        self.backend = TaskRuntimeService(run_root=run_root)

    def snapshot(self, task_id: str) -> RuntimeSnapshotResponse | LegacyRuntimeSnapshotResponse:
        payload = self.backend.runtime_snapshot(task_id)
        if int(payload.get("schema_version") or 0) == 5:
            return LegacyRuntimeSnapshotResponse.model_validate(payload)
        return RuntimeSnapshotResponse.model_validate(payload)

    def team(self, task_id: str) -> TeamResponse:
        return TeamResponse.model_validate(self.backend.team_projection(task_id))

    def solver(self, task_id: str, solver_id: str) -> SolverResponse:
        return SolverResponse.model_validate(
            self.backend.solver_projection(task_id, solver_id)
        )

    def intents(self, task_id: str, *, offset: int, limit: int) -> IntentPage:
        return IntentPage.model_validate(
            self.backend.intent_page(task_id, offset=offset, limit=limit)
        )

    def evidence(self, task_id: str, *, offset: int, limit: int) -> EvidencePageResponse:
        return EvidencePageResponse.model_validate(
            self.backend.evidence_page(task_id, offset=offset, limit=limit)
        )

    def approvals(
        self, task_id: str, *, offset: int, limit: int, status: str | None
    ) -> ApprovalPage:
        return ApprovalPage.model_validate(
            self.backend.approval_page(
                task_id, offset=offset, limit=limit, status=status
            )
        )

    def events(self, task_id: str, *, after_seq: int, limit: int) -> EventPage:
        return EventPage.model_validate(
            self.backend.event_page(task_id, after_seq=after_seq, limit=limit)
        )

    async def wait_for_events(
        self, task_id: str, *, after_seq: int, timeout: float = 15.0
    ) -> bool:
        return await self.backend.wait_for_events(
            task_id, after_seq=after_seq, timeout=timeout
        )

    def artifact(self, task_id: str, artifact_id: str) -> dict:
        return self.backend.artifact_record(task_id, artifact_id)

    def artifact_index(self, task_id: str, artifact_id: str):
        return self.backend.artifact_index(task_id, artifact_id)

    def tasks(self) -> list[dict]:
        return self.backend.list_tasks()

    def report(self, task_id: str) -> str:
        return self.backend.render_report(task_id)

    def task_root(self, task_id: str) -> Path:
        return self.backend.task_root(task_id)

    def task_schema_version(self, task_id: str) -> int:
        return self.backend.task_schema_version(task_id)


__all__ = ["RuntimeQueries"]
