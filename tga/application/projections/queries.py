"""Queries that assemble v6 projections without exposing SQLite rows."""

from __future__ import annotations

import hashlib

from tga.application.projections.models import (
    EvidenceProjection,
    ApprovalProjection,
    IntentProjection,
    KnowledgeProjection,
    SessionProjection,
    SolverProjection,
    TaskSummaryProjection,
    TimelineProjection,
)


class TaskProjectionQueries:
    def __init__(self, persistence):
        self.persistence = persistence

    def task_summary(self, task_id: str) -> TaskSummaryProjection:
        task = self.persistence.tasks.get_task(task_id)
        if task is None:
            raise KeyError(f"task not found: {task_id}")
        plan = self.persistence.plans.get_global_plan(task_id)
        return TaskSummaryProjection(
            task_id=task.id,
            name=task.name,
            mode=task.mode,
            goal=task.goal,
            status=plan.status if plan else "created",
            updated_at=plan.updated_at if plan else "",
        )

    def solvers(self, task_id: str) -> list[SolverProjection]:
        return [
            SolverProjection(
                task_id=item.task_id,
                solver_id=item.id,
                definition_id=item.definition_id,
                orchestration_role=item.orchestration_role,
                specialties=list(item.specialties),
                parent_solver_id=item.parent_solver_id,
                status=item.status,
                assigned_intent_id=item.assigned_intent_id,
                model_snapshot=item.model_snapshot.model_dump(mode="json"),
                skill_snapshot=(
                    item.skill_snapshot.model_dump(mode="json")
                    if item.skill_snapshot else {}
                ),
                tool_policy=item.tool_policy_snapshot.model_dump(mode="json"),
                timestamps=item.timestamps.model_dump(mode="json"),
            )
            for item in self.persistence.solvers.list_solvers(task_id)
        ]

    def session(self, task_id: str) -> SessionProjection | None:
        row = self.persistence.database.conn.execute(
            "SELECT * FROM sessions WHERE task_id=?", (task_id,)
        ).fetchone()
        if row is None:
            return None
        return SessionProjection(
            task_id=task_id,
            status=row["status"],
            active_solver_id=row["active_solver_id"],
            turn_count=row["turn_count"],
            max_turns=row["max_turns"],
        )

    def approvals(self, task_id: str) -> list[ApprovalProjection]:
        rows = self.persistence.database.conn.execute(
            "SELECT id,status,action_id,updated_at FROM approvals "
            "WHERE task_id=? ORDER BY created_at,id", (task_id,)
        ).fetchall()
        return [
            ApprovalProjection(
                approval_id=row["id"],
                status=row["status"],
                action_id=str(row["action_id"] or ""),
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

    def intents(self, task_id: str) -> list[IntentProjection]:
        plan = self.persistence.plans.get_global_plan(task_id)
        if plan is None:
            return []
        return [
            IntentProjection(
                task_id=item.task_id,
                intent_id=item.id,
                kind=item.kind,
                title=item.title,
                objective=item.objective,
                status=item.status,
                assigned_solver_id=item.assigned_solver_id,
                dependencies=[dependency.intent_id for dependency in item.dependencies],
                priority=item.priority,
                budget=item.budget,
                created_at=item.created_at,
                updated_at=item.updated_at,
            )
            for item in plan.intents
        ]

    def knowledge(self, task_id: str) -> list[KnowledgeProjection]:
        return [
            KnowledgeProjection(
                knowledge_id=item.id,
                scope=item.scope,
                target_id=item.target_id,
                status=item.status,
                kind=item.kind,
                content_preview=item.content[:500],
                content_sha256=hashlib.sha256(item.content.encode()).hexdigest(),
                created_by_solver_id=item.created_by_solver_id,
                created_at=item.created_at,
            )
            for item in self.persistence.knowledge.list_knowledge(task_id)
        ]

    def evidence(self, task_id: str) -> EvidenceProjection:
        return EvidenceProjection(
            task_id=task_id,
            artifacts=[item.model_dump(mode="json") for item in self.persistence.evidence.list_artifacts(task_id)],
            claims=[item.model_dump(mode="json") for item in self.persistence.evidence.list_evidence_claims(task_id)],
            findings=[item.model_dump(mode="json") for item in self.persistence.evidence.list_findings(task_id)],
        )

    def timeline(
        self, task_id: str, *, after_seq: int = 0, limit: int = 200
    ) -> TimelineProjection:
        events = self.persistence.events.list_agent_events(
            task_id, after_seq=after_seq, limit=limit
        )
        return TimelineProjection(
            task_id=task_id,
            after_seq=after_seq,
            next_after_seq=events[-1].seq if events else after_seq,
            events=[item.model_dump(mode="json") for item in events],
        )


__all__ = ["TaskProjectionQueries"]
