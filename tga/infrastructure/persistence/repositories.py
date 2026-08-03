"""Schema-v6 SQLite adapters with explicit concurrency and ownership rules."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from tga.domain.evidence.artifacts import Artifact
from tga.domain.evidence.claims import EvidenceClaim
from tga.domain.evidence.findings import Finding
from tga.domain.events import AgentEvent
from tga.domain.knowledge.items import KnowledgeItem
from tga.domain.knowledge.conflicts import KnowledgeConflict
from tga.domain.knowledge.promotion import KnowledgePromotionProposal
from tga.domain.planning.global_plan import GlobalPlan
from tga.domain.planning.intents import Intent
from tga.domain.planning.local_plan import LocalPlan
from tga.domain.solver.budgets import SolverBudgetUsage
from tga.domain.solver.assignments import SolverAssignment
from tga.domain.solver.instances import SolverInstance
from tga.domain.solver.leases import SolverLease, TaskOrchestratorLease
from tga.domain.solver.runs import SolverRun
from tga.domain.solver.results import ReportResult, ReviewResult, WorkerResult
from tga.domain.solver.status import SolverInstanceStatus
from tga.domain.solver.team_runtime import TeamRuntimeState
from tga.domain.skills.models import (
    SkillActivation,
    SkillSelectionDecision,
    SolverSkillSnapshot,
    TaskCommonSkillSnapshot,
)
from tga.domain.solver.definitions import SolverDefinition
from tga.domain.task.models import TGATask
from tga.domain.task.spec import TaskSpec
from tga.domain.task.hints import TaskHint
from tga.domain.task.interventions import UserIntervention
from tga.evidence.database import Database, utc_now
from tga.infrastructure.persistence.errors import (
    ArtifactImmutableError,
    IntentClaimConflict,
    OwnershipError,
    PersistenceConflict,
    PlanVersionConflict,
)


def _json(value: Any) -> str:
    if hasattr(value, "model_dump_json"):
        return value.model_dump_json()
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class SqliteTaskRepository:
    def __init__(self, database: Database):
        self.database = database
        self.conn = database.conn

    def get_task(self, task_id: str) -> TGATask | None:
        row = self.conn.execute("SELECT payload_json FROM tasks WHERE id=?", (task_id,)).fetchone()
        return TGATask.model_validate_json(row["payload_json"]) if row else None

    def create_task(self, task: TGATask) -> None:
        if task.schema_version != 6:
            raise ValueError("schema-v6 repository accepts only schema 6 tasks")
        try:
            self.conn.execute(
                "INSERT INTO tasks(id,payload_json,created_at) VALUES (?,?,?)",
                (task.id, task.model_dump_json(), utc_now()),
            )
        except sqlite3.IntegrityError as exc:
            raise PersistenceConflict(f"task already exists: {task.id}") from exc
        self.database._commit()

    def update_task(self, task: TGATask) -> None:
        if task.schema_version != 6:
            raise ValueError("schema-v6 repository accepts only schema 6 tasks")
        cursor = self.conn.execute(
            "UPDATE tasks SET payload_json=? WHERE id=?", (task.model_dump_json(), task.id)
        )
        if cursor.rowcount != 1:
            raise KeyError(f"task not found: {task.id}")
        self.database._commit()

    def get_task_spec(self, task_id: str) -> TaskSpec | None:
        row = self.conn.execute(
            "SELECT payload_json FROM task_specs WHERE task_id=?", (task_id,)
        ).fetchone()
        return TaskSpec.model_validate_json(row["payload_json"]) if row else None

    def save_task_spec(self, spec: TaskSpec) -> None:
        _require_task(self.conn, spec.task_id)
        now = utc_now()
        self.conn.execute(
            "INSERT INTO task_specs(task_id,version,payload_json,created_at,updated_at) "
            "VALUES (?,1,?,?,?) ON CONFLICT(task_id) DO UPDATE SET "
            "version=task_specs.version+1,payload_json=excluded.payload_json,updated_at=excluded.updated_at",
            (spec.task_id, spec.model_dump_json(), now, now),
        )
        self.database._commit()

    def save_hint(self, hint: TaskHint) -> None:
        _require_task(self.conn, hint.task_id)
        now = hint.reviewed_at or hint.created_at
        self.conn.execute(
            "INSERT INTO task_hints(id,task_id,scope,target_id,status,version,payload_json,created_at,updated_at) "
            "VALUES (?,?,?,?,?,1,?,?,?) ON CONFLICT(id) DO UPDATE SET "
            "status=excluded.status,version=task_hints.version+1,payload_json=excluded.payload_json,"
            "updated_at=excluded.updated_at WHERE task_hints.task_id=excluded.task_id",
            (
                hint.id, hint.task_id, hint.scope, hint.target_id, hint.status,
                hint.model_dump_json(), hint.created_at, now,
            ),
        )
        self.database._commit()

    def list_hints(self, task_id: str) -> list[TaskHint]:
        rows = self.conn.execute(
            "SELECT payload_json FROM task_hints WHERE task_id=? ORDER BY created_at,id",
            (task_id,),
        ).fetchall()
        return [TaskHint.model_validate_json(row["payload_json"]) for row in rows]

    def add_intervention(self, intervention: UserIntervention) -> None:
        _require_task(self.conn, intervention.task_id)
        try:
            self.conn.execute(
                "INSERT INTO user_interventions(id,task_id,scope,target_id,kind,version,payload_json,created_at) "
                "VALUES (?,?,?,?,?,1,?,?)",
                (
                    intervention.id, intervention.task_id, intervention.scope,
                    intervention.target_id, intervention.kind,
                    intervention.model_dump_json(), intervention.created_at,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise PersistenceConflict(f"intervention already exists: {intervention.id}") from exc
        self.database._commit()

    def list_interventions(self, task_id: str) -> list[UserIntervention]:
        rows = self.conn.execute(
            "SELECT payload_json FROM user_interventions WHERE task_id=? ORDER BY created_at,id",
            (task_id,),
        ).fetchall()
        return [UserIntervention.model_validate_json(row["payload_json"]) for row in rows]

    def save_task_common_skill_snapshot(self, snapshot: TaskCommonSkillSnapshot) -> None:
        _require_task(self.conn, snapshot.task_id)
        digest = __import__("hashlib").sha256(snapshot.model_dump_json().encode()).hexdigest()
        try:
            self.conn.execute(
                "INSERT INTO task_common_skill_snapshots(task_id,version,content_sha256,payload_json,created_at) "
                "VALUES (?,1,?,?,?)",
                (snapshot.task_id, digest, snapshot.model_dump_json(), snapshot.created_at),
            )
        except sqlite3.IntegrityError as exc:
            raise PersistenceConflict(
                f"task common Skill snapshot is immutable: {snapshot.task_id}"
            ) from exc
        self.database._commit()

    def get_task_common_skill_snapshot(
        self, task_id: str
    ) -> TaskCommonSkillSnapshot | None:
        row = self.conn.execute(
            "SELECT payload_json FROM task_common_skill_snapshots WHERE task_id=?",
            (task_id,),
        ).fetchone()
        return TaskCommonSkillSnapshot.model_validate_json(row["payload_json"]) if row else None


class SqlitePlanRepository:
    def __init__(self, database: Database):
        self.database = database
        self.conn = database.conn

    def get_global_plan(self, task_id: str) -> GlobalPlan | None:
        row = self.conn.execute(
            "SELECT payload_json FROM global_plans WHERE task_id=?", (task_id,)
        ).fetchone()
        if row is None:
            return None
        plan = GlobalPlan.model_validate_json(row["payload_json"])
        intent_rows = self.conn.execute(
            "SELECT payload_json FROM intents WHERE global_plan_id=? AND schema_version=6 "
            "ORDER BY priority DESC,created_at,id",
            (plan.id,),
        ).fetchall()
        intents = [Intent.model_validate_json(item["payload_json"]) for item in intent_rows]
        return plan.model_copy(update={"intents": intents})

    def save_global_plan(self, plan: GlobalPlan) -> None:
        with self.database.transaction():
            self._save_global_plan(plan)

    def _save_global_plan(self, plan: GlobalPlan) -> None:
        self._validate_plan_ownership(plan)
        try:
            self.conn.execute(
                "INSERT INTO global_plans(id,task_id,version,status,payload_json,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    plan.id, plan.task_id, plan.version, plan.status, plan.model_dump_json(),
                    plan.created_at, plan.updated_at,
                ),
            )
            self._replace_intents(plan, preserve_lifecycle=False)
        except sqlite3.IntegrityError as exc:
            raise PersistenceConflict(f"global plan already exists or violates ownership: {plan.id}") from exc
        self.database._commit()

    def compare_and_swap_global_plan(
        self,
        plan: GlobalPlan,
        *,
        expected_version: int,
        preserve_intent_lifecycle: bool = True,
    ) -> None:
        with self.database.transaction():
            self._compare_and_swap_global_plan(
                plan,
                expected_version=expected_version,
                preserve_intent_lifecycle=preserve_intent_lifecycle,
            )

    def _compare_and_swap_global_plan(
        self,
        plan: GlobalPlan,
        *,
        expected_version: int,
        preserve_intent_lifecycle: bool = True,
    ) -> None:
        self._validate_plan_ownership(plan)
        if plan.version != expected_version + 1:
            raise PlanVersionConflict("replacement plan version must be expected_version + 1")
        cursor = self.conn.execute(
            "UPDATE global_plans SET version=?,status=?,payload_json=?,updated_at=? "
            "WHERE id=? AND task_id=? AND version=?",
            (
                plan.version, plan.status, plan.model_dump_json(), plan.updated_at,
                plan.id, plan.task_id, expected_version,
            ),
        )
        if cursor.rowcount != 1:
            raise PlanVersionConflict(
                f"global plan {plan.id} is no longer at version {expected_version}"
            )
        self._replace_intents(
            plan, preserve_lifecycle=preserve_intent_lifecycle
        )
        self.database._commit()

    def claim_intent(
        self, intent_id: str, *, solver_id: str, expected_version: int
    ) -> Intent:
        with self.database.transaction():
            row = self.conn.execute(
                "SELECT payload_json FROM intents WHERE id=? AND schema_version=6",
                (intent_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"intent not found: {intent_id}")
            incomplete = self.conn.execute(
                "SELECT 1 FROM intent_dependencies d "
                "JOIN intents prerequisite ON prerequisite.id=d.depends_on_intent_id "
                "WHERE d.intent_id=? AND prerequisite.status<>d.required_status LIMIT 1",
                (intent_id,),
            ).fetchone()
            if incomplete is not None:
                raise IntentClaimConflict(
                    f"intent dependencies are not complete: {intent_id}"
                )
            intent = Intent.model_validate_json(row["payload_json"])
            claimed = intent.model_copy(update={
                "status": "running",
                "assigned_solver_id": solver_id,
                "updated_at": utc_now(),
            })
            cursor = self.conn.execute(
                "UPDATE intents SET payload_json=?,status='running',assigned_solver_id=?,"
                "version=version+1,claimed_at=?,updated_at=? WHERE id=? AND version=? "
                "AND status IN ('pending','assigned') AND assigned_solver_id IS NULL",
                (
                    claimed.model_dump_json(), solver_id, claimed.updated_at,
                    claimed.updated_at, intent_id, expected_version,
                ),
            )
            if cursor.rowcount != 1:
                raise IntentClaimConflict(
                    f"intent is already claimed or stale: {intent_id}"
                )
            return claimed

    def get_intent_version(self, intent_id: str) -> int:
        row = self.conn.execute(
            "SELECT version FROM intents WHERE id=? AND schema_version=6",
            (intent_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"intent not found: {intent_id}")
        return int(row["version"])

    def claim_pending_intent(
        self, intent_id: str, solver_id: str, expected_version: int
    ) -> Intent:
        return self.claim_intent(
            intent_id, solver_id=solver_id, expected_version=expected_version
        )

    def update_intent_status(
        self, intent_id: str, status: str, *, expected_status: str | None = None
    ) -> Intent:
        row = self.conn.execute(
            "SELECT task_id,status FROM intents WHERE id=? AND schema_version=6",
            (intent_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"intent not found: {intent_id}")
        if expected_status is not None and row["status"] != expected_status:
            raise PersistenceConflict(
                f"intent expected {expected_status}, found {row['status']}: {intent_id}"
            )
        with self.database.transaction():
            plan = self.get_global_plan(str(row["task_id"]))
            if plan is None:
                raise KeyError(f"global plan missing for intent: {intent_id}")
            now = utc_now()
            replacement_intent = next(
                item.model_copy(update={"status": status, "updated_at": now})
                for item in plan.intents if item.id == intent_id
            )
            replacement = plan.model_copy(update={
                "version": plan.version + 1,
                "updated_at": now,
                "intents": [
                    replacement_intent if item.id == intent_id else item
                    for item in plan.intents
                ],
            })
            self._compare_and_swap_global_plan(
                replacement,
                expected_version=plan.version,
                preserve_intent_lifecycle=False,
            )
            return replacement_intent

    def reset_intent_for_retry(self, intent_id: str) -> Intent:
        row = self.conn.execute(
            "SELECT task_id,status FROM intents WHERE id=? AND schema_version=6",
            (intent_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"intent not found: {intent_id}")
        if row["status"] not in {"blocked", "failed"}:
            raise PersistenceConflict(
                f"intent is not retryable from {row['status']}: {intent_id}"
            )
        with self.database.transaction():
            plan = self.get_global_plan(str(row["task_id"]))
            if plan is None:
                raise KeyError(f"global plan missing for intent: {intent_id}")
            now = utc_now()
            reset = next(
                item.model_copy(update={
                    "status": "pending",
                    "assigned_solver_id": None,
                    "updated_at": now,
                })
                for item in plan.intents
                if item.id == intent_id
            )
            replacement = plan.model_copy(update={
                "version": plan.version + 1,
                "updated_at": now,
                "intents": [
                    reset if item.id == intent_id else item
                    for item in plan.intents
                ],
            })
            self._compare_and_swap_global_plan(
                replacement,
                expected_version=plan.version,
                preserve_intent_lifecycle=False,
            )
            return reset

    def get_local_plan(self, solver_id: str, intent_id: str) -> LocalPlan | None:
        row = self.conn.execute(
            "SELECT payload_json FROM local_plans WHERE solver_id=? AND intent_id=?",
            (solver_id, intent_id),
        ).fetchone()
        return LocalPlan.model_validate_json(row["payload_json"]) if row else None

    def save_local_plan(self, plan: LocalPlan) -> None:
        with self.database.transaction():
            self._save_local_plan(plan)

    def _save_local_plan(self, plan: LocalPlan) -> None:
        _require_task(self.conn, plan.task_id)
        intent = self.conn.execute(
            "SELECT task_id FROM intents WHERE id=?", (plan.intent_id,)
        ).fetchone()
        if intent is None or intent["task_id"] != plan.task_id:
            raise OwnershipError("local plan intent does not belong to task")
        try:
            self.conn.execute(
                "INSERT INTO local_plans(id,task_id,solver_id,intent_id,version,status,payload_json,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    plan.id, plan.task_id, plan.solver_id, plan.intent_id, plan.version,
                    plan.status, plan.model_dump_json(), plan.created_at, plan.updated_at,
                ),
            )
            for step in plan.steps:
                self.conn.execute(
                    "INSERT INTO local_plan_steps(id,local_plan_id,task_id,solver_id,intent_id,step_order,status,payload_json) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (
                        step.id, plan.id, plan.task_id, plan.solver_id, plan.intent_id,
                        step.order, step.status, step.model_dump_json(),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise PersistenceConflict(f"local plan already exists: {plan.id}") from exc
        self.database._commit()

    def _validate_plan_ownership(self, plan: GlobalPlan) -> None:
        _require_task(self.conn, plan.task_id)
        if any(intent.task_id != plan.task_id for intent in plan.intents):
            raise OwnershipError("global plan intents must belong to the same task")

    def _replace_intents(
        self, plan: GlobalPlan, *, preserve_lifecycle: bool
    ) -> None:
        existing = {
            row["id"]
            for row in self.conn.execute(
                "SELECT id FROM intents WHERE global_plan_id=? AND schema_version=6", (plan.id,)
            ).fetchall()
        }
        incoming = {intent.id for intent in plan.intents}
        if existing - incoming:
            raise PersistenceConflict("CAS cannot silently delete persisted intents")
        for intent in plan.intents:
            row = self.conn.execute(
                "SELECT task_id,payload_json FROM intents WHERE id=?",
                (intent.id,),
            ).fetchone()
            if row is not None and row["task_id"] != plan.task_id:
                raise OwnershipError("intent id is owned by another task")
            effective = intent
            if row is not None and preserve_lifecycle:
                persisted = Intent.model_validate_json(row["payload_json"])
                effective = intent.model_copy(update={
                    "status": persisted.status,
                    "assigned_solver_id": persisted.assigned_solver_id,
                    "updated_at": persisted.updated_at,
                })
            self.conn.execute(
                "INSERT INTO intents(id,task_id,payload_json,status,created_at,updated_at,"
                "global_plan_id,assigned_solver_id,priority,version,schema_version) "
                "VALUES (?,?,?,?,?,?,?,?,?,1,6) ON CONFLICT(id) DO UPDATE SET "
                "payload_json=excluded.payload_json,status=excluded.status,updated_at=excluded.updated_at,"
                "global_plan_id=excluded.global_plan_id,assigned_solver_id=excluded.assigned_solver_id,"
                "priority=excluded.priority WHERE intents.task_id=excluded.task_id AND intents.schema_version=6",
                (
                    effective.id, effective.task_id, effective.model_dump_json(),
                    effective.status, effective.created_at, effective.updated_at,
                    plan.id, effective.assigned_solver_id, effective.priority,
                ),
            )
        # Insert every node before its edges so valid DAGs are independent of
        # presentation order and foreign keys can remain strict.
        for intent in plan.intents:
            self.conn.execute("DELETE FROM intent_dependencies WHERE intent_id=?", (intent.id,))
            for dependency in intent.dependencies:
                self.conn.execute(
                    "INSERT INTO intent_dependencies(task_id,intent_id,depends_on_intent_id,required_status,condition) "
                    "VALUES (?,?,?,?,?)",
                    (
                        plan.task_id, intent.id, dependency.intent_id,
                        dependency.required_status, dependency.condition,
                    ),
                )


class SqliteEvidenceRepository:
    def __init__(self, database: Database):
        self.database = database
        self.conn = database.conn

    def get_artifact(self, artifact_id: str) -> Artifact | None:
        row = self.conn.execute(
            "SELECT payload_json FROM artifacts WHERE id=? AND schema_version=6", (artifact_id,)
        ).fetchone()
        return Artifact.model_validate_json(row["payload_json"]) if row else None

    def list_artifacts(self, task_id: str) -> list[Artifact]:
        rows = self.conn.execute(
            "SELECT payload_json FROM artifacts WHERE task_id=? AND schema_version=6 ORDER BY created_at,id",
            (task_id,),
        ).fetchall()
        return [Artifact.model_validate_json(row["payload_json"]) for row in rows]

    def page_artifacts(
        self, task_id: str, *, offset: int, limit: int
    ) -> tuple[int, list[Artifact]]:
        total, rows = self._page_payloads(
            "artifacts", task_id, offset=offset, limit=limit,
            where_suffix=" AND schema_version=6",
        )
        return total, [Artifact.model_validate_json(row["payload_json"]) for row in rows]

    def add_artifact(self, artifact: Artifact) -> None:
        """Insert one immutable Artifact row.

        Artifacts are append-only in schema v6.  Re-inserting a byte-identical
        row for the same task is a no-op so retried handlers stay idempotent;
        any other id collision is an immutability violation.
        """
        _require_task(self.conn, artifact.task_id)
        if artifact.intent_id is not None:
            _require_owned(self.conn, "intents", artifact.intent_id, artifact.task_id, "artifact intent")
        payload = artifact.model_dump_json()
        existing = self.conn.execute(
            "SELECT task_id,payload_json FROM artifacts WHERE id=?", (artifact.id,)
        ).fetchone()
        if existing is not None:
            if (
                existing["task_id"] == artifact.task_id
                and existing["payload_json"] == payload
            ):
                return
            raise ArtifactImmutableError(f"artifact id is immutable: {artifact.id}")
        try:
            self.conn.execute(
                "INSERT INTO artifacts(id,task_id,intent_id,payload_json,created_at,schema_version) "
                "VALUES (?,?,?,?,?,6)",
                (
                    artifact.id, artifact.task_id, artifact.intent_id,
                    payload, artifact.created_at,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ArtifactImmutableError(f"artifact id is immutable: {artifact.id}") from exc
        self.database._commit()

    def get_evidence_claim(self, claim_id: str) -> EvidenceClaim | None:
        row = self.conn.execute(
            "SELECT payload_json FROM evidence_claims WHERE id=?", (claim_id,)
        ).fetchone()
        return EvidenceClaim.model_validate_json(row["payload_json"]) if row else None

    def list_evidence_claims(self, task_id: str) -> list[EvidenceClaim]:
        rows = self.conn.execute(
            "SELECT payload_json FROM evidence_claims WHERE task_id=? ORDER BY created_at,id",
            (task_id,),
        ).fetchall()
        return [EvidenceClaim.model_validate_json(row["payload_json"]) for row in rows]

    def page_evidence_claims(
        self, task_id: str, *, offset: int, limit: int
    ) -> tuple[int, list[EvidenceClaim]]:
        total, rows = self._page_payloads(
            "evidence_claims", task_id, offset=offset, limit=limit
        )
        return total, [
            EvidenceClaim.model_validate_json(row["payload_json"]) for row in rows
        ]

    def add_evidence_claim(self, claim: EvidenceClaim) -> None:
        artifact = self.conn.execute(
            "SELECT task_id FROM artifacts WHERE id=?", (claim.artifact_id,)
        ).fetchone()
        if artifact is None or artifact["task_id"] != claim.task_id:
            raise OwnershipError("EvidenceClaim artifact does not belong to claim task")
        try:
            self.conn.execute(
                "INSERT INTO evidence_claims(id,task_id,artifact_id,status,version,payload_json,created_at,reviewed_at) "
                "VALUES (?,?,?,?,1,?,?,?)",
                (
                    claim.id, claim.task_id, claim.artifact_id, claim.status,
                    claim.model_dump_json(), claim.created_at, claim.reviewed_at,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise PersistenceConflict(f"evidence claim already exists: {claim.id}") from exc
        self.database._commit()

    def review_evidence_claim(
        self, claim_id: str, *, status: str, reviewer_solver_id: str, reviewed_at: str
    ) -> EvidenceClaim:
        if status not in {"confirmed", "rejected"}:
            raise ValueError("EvidenceClaim review status must be confirmed or rejected")
        current = self.get_evidence_claim(claim_id)
        if current is None:
            raise KeyError(f"evidence claim not found: {claim_id}")
        if current.status == status:
            return current
        if current.status != "candidate":
            raise PersistenceConflict(f"EvidenceClaim is already reviewed: {claim_id}")
        replacement = current.model_copy(update={
            "status": status,
            "reviewed_by_solver_id": reviewer_solver_id,
            "reviewed_at": reviewed_at,
        })
        cursor = self.conn.execute(
            "UPDATE evidence_claims SET status=?,version=version+1,payload_json=?,reviewed_at=? "
            "WHERE id=? AND status='candidate'",
            (status, replacement.model_dump_json(), reviewed_at, claim_id),
        )
        if cursor.rowcount != 1:
            raise PersistenceConflict(f"EvidenceClaim changed concurrently: {claim_id}")
        self.database._commit()
        return replacement

    def get_finding(self, finding_id: str) -> Finding | None:
        row = self.conn.execute(
            "SELECT payload_json FROM findings WHERE id=? AND schema_version=6", (finding_id,)
        ).fetchone()
        return self._hydrate_finding(row["payload_json"]) if row else None

    def list_findings(self, task_id: str) -> list[Finding]:
        rows = self.conn.execute(
            "SELECT payload_json FROM findings WHERE task_id=? AND schema_version=6 ORDER BY created_at,id",
            (task_id,),
        ).fetchall()
        return [self._hydrate_finding(row["payload_json"]) for row in rows]

    def page_findings(
        self, task_id: str, *, offset: int, limit: int
    ) -> tuple[int, list[Finding]]:
        total, rows = self._page_payloads(
            "findings", task_id, offset=offset, limit=limit,
            where_suffix=" AND schema_version=6",
        )
        return total, [self._hydrate_finding(row["payload_json"]) for row in rows]

    def _page_payloads(
        self,
        table: str,
        task_id: str,
        *,
        offset: int,
        limit: int,
        where_suffix: str = "",
    ) -> tuple[int, list[sqlite3.Row]]:
        if table not in {"artifacts", "evidence_claims", "findings"}:
            raise ValueError(f"unsupported evidence table: {table}")
        bounded_offset = max(0, int(offset))
        bounded_limit = max(1, min(int(limit), 200))
        where = f"task_id=?{where_suffix}"
        total = int(self.conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE {where}", (task_id,)
        ).fetchone()[0])
        rows = self.conn.execute(
            f"SELECT payload_json FROM {table} WHERE {where} "
            "ORDER BY created_at,id LIMIT ? OFFSET ?",
            (task_id, bounded_limit, bounded_offset),
        ).fetchall()
        return total, rows

    def save_finding(self, finding: Finding) -> None:
        with self.database.transaction():
            self._save_finding(finding)

    def _save_finding(self, finding: Finding) -> None:
        for claim in finding.evidence_claims:
            _require_owned(self.conn, "evidence_claims", claim.id, finding.task_id, "finding claim")
        payload = finding.model_copy(update={"evidence_claims": []})
        existing = self.conn.execute("SELECT task_id FROM findings WHERE id=?", (finding.id,)).fetchone()
        if existing is not None and existing["task_id"] != finding.task_id:
            raise OwnershipError("finding id is owned by another task")
        now = utc_now()
        self.conn.execute(
            "INSERT INTO findings(id,task_id,payload_json,status,evidence_artifact_id,created_at,updated_at,schema_version) "
            "VALUES (?,?,?,?,?,?,?,6) ON CONFLICT(id) DO UPDATE SET "
            "payload_json=excluded.payload_json,status=excluded.status,updated_at=excluded.updated_at "
            "WHERE findings.task_id=excluded.task_id AND findings.schema_version=6",
            (
                finding.id, finding.task_id, payload.model_dump_json(), finding.status,
                finding.evidence_claims[0].artifact_id if finding.evidence_claims else None,
                finding.created_at, now,
            ),
        )
        self.conn.execute("DELETE FROM finding_evidence_links WHERE finding_id=?", (finding.id,))
        for claim in finding.evidence_claims:
            self.conn.execute(
                "INSERT INTO finding_evidence_links(task_id,finding_id,claim_id) VALUES (?,?,?)",
                (finding.task_id, finding.id, claim.id),
            )
        self.database._commit()

    def _hydrate_finding(self, payload_json: str) -> Finding:
        payload = json.loads(payload_json)
        rows = self.conn.execute(
            "SELECT c.payload_json FROM evidence_claims c JOIN finding_evidence_links l "
            "ON l.claim_id=c.id WHERE l.finding_id=? ORDER BY c.created_at,c.id",
            (payload["id"],),
        ).fetchall()
        payload["evidence_claims"] = [json.loads(row["payload_json"]) for row in rows]
        return Finding.model_validate(payload)


class SqliteKnowledgeRepository:
    def __init__(self, database: Database):
        self.database = database
        self.conn = database.conn

    def list_knowledge(self, task_id: str) -> list[KnowledgeItem]:
        rows = self.conn.execute(
            "SELECT payload_json FROM knowledge_items WHERE task_id=? ORDER BY created_at,id",
            (task_id,),
        ).fetchall()
        return [KnowledgeItem.model_validate_json(row["payload_json"]) for row in rows]

    def get_knowledge(self, knowledge_id: str) -> KnowledgeItem | None:
        row = self.conn.execute(
            "SELECT payload_json FROM knowledge_items WHERE id=?", (knowledge_id,)
        ).fetchone()
        return KnowledgeItem.model_validate_json(row["payload_json"]) if row else None

    def add_knowledge(self, item: KnowledgeItem) -> None:
        with self.database.transaction():
            self._add_knowledge(item)

    def _add_knowledge(self, item: KnowledgeItem) -> None:
        _require_task(self.conn, item.task_id)
        for claim_id in item.evidence_claim_ids:
            _require_owned(self.conn, "evidence_claims", claim_id, item.task_id, "knowledge claim")
        updated = item.updated_at or item.created_at
        try:
            self.conn.execute(
                "INSERT INTO knowledge_items(id,task_id,scope,target_id,status,kind,subject,structured_value,version,payload_json,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,1,?,?,?)",
                (
                    item.id, item.task_id, item.scope, item.target_id, item.status, item.kind,
                    item.subject, item.value, item.model_dump_json(), item.created_at, updated,
                ),
            )
            for claim_id in item.evidence_claim_ids:
                self.conn.execute(
                    "INSERT INTO knowledge_evidence_links(task_id,knowledge_id,claim_id) VALUES (?,?,?)",
                    (item.task_id, item.id, claim_id),
                )
        except sqlite3.IntegrityError as exc:
            raise PersistenceConflict(f"knowledge item already exists: {item.id}") from exc
        self.database._commit()

    def review_knowledge(
        self, knowledge_id: str, *, status: str, reviewer_solver_id: str,
        reviewed_at: str,
    ) -> KnowledgeItem:
        if status not in {"verified", "rejected"}:
            raise ValueError("Knowledge review status must be verified or rejected")
        current = self.get_knowledge(knowledge_id)
        if current is None:
            raise KeyError(f"knowledge not found: {knowledge_id}")
        if current.status == status:
            return current
        if current.status != "candidate":
            raise PersistenceConflict(f"Knowledge is already reviewed: {knowledge_id}")
        replacement = KnowledgeItem.model_validate({
            **current.model_dump(mode="python"),
            "status": status,
            "updated_at": reviewed_at,
            "provenance": {
                **current.provenance,
                "reviewed_by_solver_id": reviewer_solver_id,
                "reviewed_at": reviewed_at,
            },
        })
        cursor = self.conn.execute(
            "UPDATE knowledge_items SET status=?,version=version+1,payload_json=?,updated_at=? "
            "WHERE id=? AND status='candidate'",
            (status, replacement.model_dump_json(), reviewed_at, knowledge_id),
        )
        if cursor.rowcount != 1:
            raise PersistenceConflict(f"Knowledge changed concurrently: {knowledge_id}")
        self.database._commit()
        return replacement

    def add_conflict(self, conflict: KnowledgeConflict) -> None:
        with self.database.transaction():
            self._add_conflict(conflict)

    def _add_conflict(self, conflict: KnowledgeConflict) -> None:
        _require_task(self.conn, conflict.task_id)
        for item_id in conflict.knowledge_item_ids:
            _require_owned(
                self.conn, "knowledge_items", item_id, conflict.task_id, "conflicting knowledge"
            )
        self.conn.execute(
            "INSERT INTO knowledge_conflicts(id,task_id,status,version,payload_json,created_at,resolved_at) "
            "VALUES (?,?,?,1,?,?,?)",
            (
                conflict.id, conflict.task_id, conflict.status, conflict.model_dump_json(),
                conflict.created_at, conflict.resolved_at,
            ),
        )
        for item_id in conflict.knowledge_item_ids:
            self.conn.execute(
                "INSERT INTO knowledge_conflict_items(conflict_id,knowledge_id,task_id) VALUES (?,?,?)",
                (conflict.id, item_id, conflict.task_id),
            )
        self.database._commit()

    def list_conflicts(
        self, task_id: str, *, status: str | None = None
    ) -> list[KnowledgeConflict]:
        rows = self.conn.execute(
            "SELECT payload_json FROM knowledge_conflicts WHERE task_id=? "
            "AND (? IS NULL OR status=?) ORDER BY created_at,id",
            (task_id, status, status),
        ).fetchall()
        return [KnowledgeConflict.model_validate_json(row["payload_json"]) for row in rows]

    def add_promotion(self, proposal: KnowledgePromotionProposal) -> None:
        with self.database.transaction():
            self._add_promotion(proposal)

    def _add_promotion(self, proposal: KnowledgePromotionProposal) -> None:
        _require_owned(
            self.conn, "knowledge_items", proposal.knowledge_item_id,
            proposal.task_id, "promoted knowledge"
        )
        self.conn.execute(
            "INSERT INTO knowledge_promotions(id,task_id,knowledge_id,status,version,payload_json,created_at,reviewed_at) "
            "VALUES (?,?,?,?,1,?,?,?)",
            (
                proposal.id, proposal.task_id, proposal.knowledge_item_id, proposal.status,
                proposal.model_dump_json(), proposal.created_at, proposal.reviewed_at,
            ),
        )
        self.database._commit()

    def list_promotions(
        self, task_id: str, *, status: str | None = None
    ) -> list[KnowledgePromotionProposal]:
        rows = self.conn.execute(
            "SELECT payload_json FROM knowledge_promotions WHERE task_id=? "
            "AND (? IS NULL OR status=?) ORDER BY created_at,id",
            (task_id, status, status),
        ).fetchall()
        return [
            KnowledgePromotionProposal.model_validate_json(row["payload_json"])
            for row in rows
        ]


class SqliteSolverRepository:
    def __init__(self, database: Database):
        self.database = database
        self.conn = database.conn

    def get_solver(self, solver_id: str) -> SolverInstance | None:
        row = self.conn.execute(
            "SELECT payload_json FROM solver_instances WHERE id=?", (solver_id,)
        ).fetchone()
        return SolverInstance.model_validate_json(row["payload_json"]) if row else None

    def list_solvers(self, task_id: str) -> list[SolverInstance]:
        rows = self.conn.execute(
            "SELECT payload_json FROM solver_instances WHERE task_id=? ORDER BY created_at,id",
            (task_id,),
        ).fetchall()
        return [SolverInstance.model_validate_json(row["payload_json"]) for row in rows]

    def lease_fencing_token(self, solver_id: str) -> int | None:
        row = self.conn.execute(
            "SELECT fencing_token FROM solver_leases WHERE solver_id=?",
            (solver_id,),
        ).fetchone()
        return int(row["fencing_token"]) if row else None

    def add_solver(self, solver: SolverInstance) -> None:
        with self.database.transaction():
            self._add_solver(solver)

    def update_solver_status(
        self, solver_id: str, status: str
    ) -> SolverInstance:
        row = self.conn.execute(
            "SELECT payload_json,version FROM solver_instances WHERE id=?",
            (solver_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"solver not found: {solver_id}")
        current = SolverInstance.model_validate_json(row["payload_json"])
        target = SolverInstanceStatus(status)
        if current.status == target:
            return current
        if current.status in {
            SolverInstanceStatus.COMPLETED,
            SolverInstanceStatus.FAILED,
            SolverInstanceStatus.CANCELLED,
        }:
            raise PersistenceConflict(
                f"terminal solver status is immutable: {solver_id} ({current.status})"
            )
        now = utc_now()
        timestamps = current.timestamps.model_copy(update={"updated_at": now})
        if target == SolverInstanceStatus.RUNNING and timestamps.started_at is None:
            timestamps = timestamps.model_copy(update={"started_at": now})
        if target in {
            SolverInstanceStatus.COMPLETED,
            SolverInstanceStatus.FAILED,
            SolverInstanceStatus.CANCELLED,
        }:
            timestamps = timestamps.model_copy(update={"finished_at": now})
        replacement = current.model_copy(update={
            "status": target,
            "timestamps": timestamps,
        })
        cursor = self.conn.execute(
            "UPDATE solver_instances SET status=?,version=version+1,payload_json=?,updated_at=? "
            "WHERE id=? AND version=?",
            (
                target, replacement.model_dump_json(), now,
                solver_id, int(row["version"]),
            ),
        )
        if cursor.rowcount != 1:
            raise PersistenceConflict(f"solver status changed concurrently: {solver_id}")
        self.database._commit()
        return replacement

    def _add_solver(self, solver: SolverInstance) -> None:
        _require_task(self.conn, solver.task_id)
        if solver.assigned_intent_id:
            _require_owned(
                self.conn, "intents", solver.assigned_intent_id, solver.task_id, "solver intent"
            )
        if solver.parent_solver_id:
            _require_owned(
                self.conn, "solver_instances", solver.parent_solver_id, solver.task_id,
                "parent solver",
            )
        try:
            self.conn.execute(
                "INSERT INTO solver_instances(id,task_id,definition_id,assigned_intent_id,parent_solver_id,status,version,payload_json,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,1,?,?,?)",
                (
                    solver.id, solver.task_id, solver.definition_id, solver.assigned_intent_id,
                    solver.parent_solver_id, solver.status, solver.model_dump_json(),
                    solver.timestamps.created_at, solver.timestamps.updated_at,
                ),
            )
            self.conn.execute(
                "INSERT INTO solver_budgets(solver_id,task_id,version,budget_json,usage_json,updated_at) "
                "VALUES (?,?,1,?,?,?)",
                (
                    solver.id, solver.task_id, solver.budget.model_dump_json(),
                    SolverBudgetUsage().model_dump_json(), solver.timestamps.updated_at,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise PersistenceConflict(f"solver already exists or violates ownership: {solver.id}") from exc
        self.database._commit()

    def save_definition_snapshot(
        self, task_id: str, definition: SolverDefinition, *, created_at: str
    ) -> None:
        _require_task(self.conn, task_id)
        self.conn.execute(
            "INSERT OR IGNORE INTO solver_definitions_snapshot("
            "task_id,definition_id,definition_version,content_sha256,payload_json,created_at) "
            "VALUES (?,?,?,?,?,?)",
            (
                task_id, definition.id, definition.version, definition.content_sha256,
                definition.model_dump_json(), created_at,
            ),
        )
        self.database._commit()

    def get_definition_snapshot(
        self, task_id: str, definition_id: str, content_sha256: str
    ) -> SolverDefinition | None:
        row = self.conn.execute(
            "SELECT payload_json FROM solver_definitions_snapshot "
            "WHERE task_id=? AND definition_id=? AND content_sha256=? "
            "ORDER BY created_at DESC LIMIT 1",
            (task_id, definition_id, content_sha256),
        ).fetchone()
        return SolverDefinition.model_validate_json(row["payload_json"]) if row else None

    def save_solver_skill_snapshot(self, snapshot: SolverSkillSnapshot) -> None:
        _require_task(self.conn, snapshot.task_id)
        digest = __import__("hashlib").sha256(snapshot.model_dump_json().encode()).hexdigest()
        try:
            self.conn.execute(
                "INSERT INTO solver_skill_snapshots(solver_id,task_id,version,content_sha256,payload_json,created_at) "
                "VALUES (?,?,1,?,?,?)",
                (
                    snapshot.solver_id, snapshot.task_id, digest,
                    snapshot.model_dump_json(), snapshot.created_at,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise PersistenceConflict(
                f"solver Skill snapshot is immutable: {snapshot.solver_id}"
            ) from exc
        self.database._commit()

    def get_solver_skill_snapshot(self, solver_id: str) -> SolverSkillSnapshot | None:
        row = self.conn.execute(
            "SELECT payload_json FROM solver_skill_snapshots WHERE solver_id=?",
            (solver_id,),
        ).fetchone()
        return SolverSkillSnapshot.model_validate_json(row["payload_json"]) if row else None

    def save_skill_selection_decision(self, decision: SkillSelectionDecision) -> None:
        _require_task(self.conn, decision.task_id)
        current = self.conn.execute(
            "SELECT payload_json FROM skill_selection_decisions WHERE id=?",
            (decision.id,),
        ).fetchone()
        payload = decision.model_dump_json()
        if current is not None:
            if current["payload_json"] != payload:
                raise PersistenceConflict(f"SkillSelectionDecision is immutable: {decision.id}")
            return
        self.conn.execute(
            "INSERT INTO skill_selection_decisions(id,task_id,solver_id,intent_id,payload_json,created_at) "
            "VALUES (?,?,?,?,?,?)",
            (
                decision.id, decision.task_id, decision.solver_id, decision.intent_id,
                payload, decision.created_at,
            ),
        )
        self.database._commit()

    def get_skill_selection_decision(
        self, decision_id: str
    ) -> SkillSelectionDecision | None:
        row = self.conn.execute(
            "SELECT payload_json FROM skill_selection_decisions WHERE id=?",
            (decision_id,),
        ).fetchone()
        return SkillSelectionDecision.model_validate_json(row["payload_json"]) if row else None

    def list_skill_selection_decisions(
        self, task_id: str, *, solver_id: str | None = None
    ) -> list[SkillSelectionDecision]:
        rows = self.conn.execute(
            "SELECT payload_json FROM skill_selection_decisions "
            "WHERE task_id=? AND (? IS NULL OR solver_id=?) "
            "ORDER BY created_at,id",
            (task_id, solver_id, solver_id),
        ).fetchall()
        return [
            SkillSelectionDecision.model_validate_json(row["payload_json"])
            for row in rows
        ]

    def save_skill_activation(self, activation: SkillActivation) -> None:
        _require_task(self.conn, activation.task_id)
        current = self.conn.execute(
            "SELECT payload_json FROM skill_activations WHERE id=?", (activation.id,)
        ).fetchone()
        payload = activation.model_dump_json()
        if current is not None:
            if current["payload_json"] != payload:
                raise PersistenceConflict(f"SkillActivation is immutable: {activation.id}")
            return
        self.conn.execute(
            "INSERT INTO skill_activations(id,task_id,solver_id,skill_name,"
            "selection_decision_id,payload_json,created_at) VALUES (?,?,?,?,?,?,?)",
            (
                activation.id, activation.task_id, activation.solver_id,
                activation.skill_name, activation.selection_decision_id,
                payload, activation.activated_at,
            ),
        )
        self.database._commit()

    def list_skill_activations(self, solver_id: str) -> list[SkillActivation]:
        rows = self.conn.execute(
            "SELECT payload_json FROM skill_activations WHERE solver_id=? "
            "ORDER BY created_at,id", (solver_id,),
        ).fetchall()
        return [SkillActivation.model_validate_json(row["payload_json"]) for row in rows]

    def save_worker_result(self, result: WorkerResult, *, version: int = 1) -> str:
        _require_owned(self.conn, "solver_instances", result.solver_id, result.task_id, "worker result solver")
        _require_owned(self.conn, "intents", result.intent_id, result.task_id, "worker result intent")
        result_id = f"result_{result.solver_id}_{result.intent_id}_{version}"
        encoded = result.model_dump_json()
        existing = self.conn.execute(
            "SELECT payload_json FROM worker_results WHERE id=?", (result_id,)
        ).fetchone()
        if existing is not None:
            if WorkerResult.model_validate_json(existing["payload_json"]) == result:
                return result_id
            raise PersistenceConflict(f"WorkerResult is immutable: {result_id}")
        self.conn.execute(
            "INSERT INTO worker_results(id,task_id,solver_id,intent_id,status,version,payload_json,created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                result_id, result.task_id, result.solver_id, result.intent_id, result.status,
                version, encoded, utc_now(),
            ),
        )
        self.database._commit()
        return result_id

    def get_worker_result(self, result_id: str) -> WorkerResult | None:
        row = self.conn.execute(
            "SELECT payload_json FROM worker_results WHERE id=?", (result_id,)
        ).fetchone()
        return WorkerResult.model_validate_json(row["payload_json"]) if row else None

    def list_worker_results(self, task_id: str) -> list[WorkerResult]:
        rows = self.conn.execute(
            "SELECT payload_json FROM worker_results WHERE task_id=? ORDER BY created_at,id",
            (task_id,),
        ).fetchall()
        return [WorkerResult.model_validate_json(row["payload_json"]) for row in rows]

    def list_worker_result_records(self, task_id: str) -> list[tuple[str, WorkerResult]]:
        rows = self.conn.execute(
            "SELECT id,payload_json FROM worker_results WHERE task_id=? ORDER BY created_at,id",
            (task_id,),
        ).fetchall()
        return [
            (str(row["id"]), WorkerResult.model_validate_json(row["payload_json"]))
            for row in rows
        ]

    def acquire_lease(
        self, task_id: str, solver_id: str, owner_id: str, *, ttl_seconds: float,
        now: datetime | None = None,
    ) -> bool:
        return self.acquire_lease_handle(
            task_id, solver_id, owner_id, ttl_seconds=ttl_seconds, now=now
        ) is not None

    def acquire_lease_handle(
        self, task_id: str, solver_id: str, owner_id: str, *, ttl_seconds: float,
        now: datetime | None = None, max_active_workers: int | None = None,
    ) -> SolverLease | None:
        if ttl_seconds <= 0:
            raise ValueError("lease ttl must be positive")
        _require_owned(self.conn, "solver_instances", solver_id, task_id, "lease solver")
        instant = now or datetime.now(UTC)
        stamp = _timestamp(instant)
        expiry = _timestamp(instant + timedelta(seconds=ttl_seconds))
        with self.database.transaction():
            current = self.conn.execute(
                "SELECT * FROM solver_leases WHERE task_id=? AND solver_id=?",
                (task_id, solver_id),
            ).fetchone()
            if current is not None and (
                current["owner_id"] != owner_id and current["expires_at"] > stamp
            ):
                return None
            if max_active_workers is not None and (
                current is None or current["owner_id"] != owner_id
            ):
                active = int(self.conn.execute(
                    "SELECT COUNT(*) FROM solver_leases l "
                    "JOIN solver_instances s ON s.id=l.solver_id "
                    "WHERE l.task_id=? AND l.expires_at>? "
                    "AND s.payload_json LIKE '%\"orchestration_role\":\"worker\"%'",
                    (task_id, stamp),
                ).fetchone()[0])
                if active >= max_active_workers:
                    return None
            if current is None:
                token = 1
                self.conn.execute(
                    "INSERT INTO solver_leases(task_id,solver_id,owner_id,fencing_token,expires_at,renewed_at,version,updated_at) "
                    "VALUES (?,?,?,?,?,?,1,?)",
                    (task_id, solver_id, owner_id, token, expiry, stamp, stamp),
                )
            else:
                token = int(current["fencing_token"])
                if current["owner_id"] != owner_id:
                    token += 1
                cursor = self.conn.execute(
                    "UPDATE solver_leases SET owner_id=?,fencing_token=?,expires_at=?,"
                    "renewed_at=?,version=version+1,updated_at=? "
                    "WHERE task_id=? AND solver_id=? AND version=?",
                    (
                        owner_id, token, expiry, stamp, stamp, task_id, solver_id,
                        int(current["version"]),
                    ),
                )
                if cursor.rowcount != 1:
                    return None
            return SolverLease(
                task_id=task_id, solver_id=solver_id, owner_id=owner_id,
                fencing_token=token, expires_at=expiry, renewed_at=stamp,
            )

    def renew_lease(
        self, task_id: str, solver_id: str, owner_id: str, *, ttl_seconds: float,
        now: datetime | None = None,
    ) -> bool:
        instant = now or datetime.now(UTC)
        cursor = self.conn.execute(
            "UPDATE solver_leases SET expires_at=?,renewed_at=?,version=version+1,updated_at=? "
            "WHERE task_id=? AND solver_id=? AND owner_id=? AND expires_at>?",
            (
                _timestamp(instant + timedelta(seconds=ttl_seconds)), _timestamp(instant),
                _timestamp(instant),
                task_id, solver_id, owner_id, _timestamp(instant),
            ),
        )
        self.database._commit()
        return cursor.rowcount == 1

    def renew_lease_handle(
        self, lease: SolverLease, *, ttl_seconds: float,
        now: datetime | None = None,
    ) -> SolverLease | None:
        instant = now or datetime.now(UTC)
        stamp = _timestamp(instant)
        expiry = _timestamp(instant + timedelta(seconds=ttl_seconds))
        cursor = self.conn.execute(
            "UPDATE solver_leases SET expires_at=?,renewed_at=?,version=version+1,updated_at=? "
            "WHERE task_id=? AND solver_id=? AND owner_id=? AND fencing_token=? AND expires_at>?",
            (
                expiry, stamp, stamp, lease.task_id, lease.solver_id,
                lease.owner_id, lease.fencing_token, stamp,
            ),
        )
        self.database._commit()
        if cursor.rowcount != 1:
            return None
        return lease.model_copy(update={"expires_at": expiry, "renewed_at": stamp})

    def validate_lease(
        self, lease: SolverLease, *, now: datetime | None = None
    ) -> bool:
        stamp = _timestamp(now or datetime.now(UTC))
        return self.conn.execute(
            "SELECT 1 FROM solver_leases WHERE task_id=? AND solver_id=? "
            "AND owner_id=? AND fencing_token=? AND expires_at>?",
            (
                lease.task_id, lease.solver_id, lease.owner_id,
                lease.fencing_token, stamp,
            ),
        ).fetchone() is not None

    def release_lease_handle(self, lease: SolverLease) -> bool:
        cursor = self.conn.execute(
            "DELETE FROM solver_leases WHERE task_id=? AND solver_id=? "
            "AND owner_id=? AND fencing_token=?",
            (
                lease.task_id, lease.solver_id, lease.owner_id,
                lease.fencing_token,
            ),
        )
        self.database._commit()
        return cursor.rowcount == 1

    def revoke_task_leases(self, task_id: str) -> int:
        cursor = self.conn.execute(
            "DELETE FROM solver_leases WHERE task_id=?", (task_id,)
        )
        self.database._commit()
        return cursor.rowcount

    def release_lease(self, task_id: str, solver_id: str, owner_id: str) -> bool:
        cursor = self.conn.execute(
            "DELETE FROM solver_leases WHERE task_id=? AND solver_id=? AND owner_id=?",
            (task_id, solver_id, owner_id),
        )
        self.database._commit()
        return cursor.rowcount == 1

    def expire_leases(self, *, now: datetime | None = None) -> int:
        instant = now or datetime.now(UTC)
        cursor = self.conn.execute(
            "DELETE FROM solver_leases WHERE expires_at<=?", (_timestamp(instant),)
        )
        self.database._commit()
        return cursor.rowcount

    acquire = acquire_lease
    renew = renew_lease
    release = release_lease
    expire = expire_leases


class SqliteOrchestrationRepository:
    def __init__(self, database: Database):
        self.database = database
        self.conn = database.conn

    def get_state(self, task_id: str) -> TeamRuntimeState | None:
        row = self.conn.execute(
            "SELECT version,payload_json FROM task_orchestrator_states WHERE task_id=?",
            (task_id,),
        ).fetchone()
        if row is None:
            return None
        return TeamRuntimeState.model_validate_json(row["payload_json"]).model_copy(
            update={"version": int(row["version"])}
        )

    def save_state(self, state: TeamRuntimeState) -> TeamRuntimeState:
        current = self.conn.execute(
            "SELECT version,created_at FROM task_orchestrator_states WHERE task_id=?",
            (state.task_id,),
        ).fetchone()
        if current is None:
            if state.version != 1:
                raise PersistenceConflict(
                    "new TaskOrchestrator state must start at version 1"
                )
            self.conn.execute(
                "INSERT INTO task_orchestrator_states(task_id,supervisor_solver_id,status,version,payload_json,created_at,updated_at) "
                "VALUES (?,?,?,1,?,?,?)",
                (
                    state.task_id, state.supervisor_solver_id, state.status,
                    state.model_dump_json(), state.created_at, state.updated_at,
                ),
            )
        else:
            if int(current["version"]) != state.version:
                raise PersistenceConflict(
                    f"TaskOrchestrator state changed concurrently: {state.task_id}"
                )
            persisted = state.model_copy(update={"version": state.version + 1})
            cursor = self.conn.execute(
                "UPDATE task_orchestrator_states SET supervisor_solver_id=?,status=?,version=version+1,payload_json=?,updated_at=? "
                "WHERE task_id=? AND version=?",
                (
                    persisted.supervisor_solver_id, persisted.status,
                    persisted.model_dump_json(), persisted.updated_at,
                    persisted.task_id, state.version,
                ),
            )
            if cursor.rowcount != 1:
                raise PersistenceConflict(
                    f"TaskOrchestrator state changed concurrently: {state.task_id}"
                )
            state = persisted
        self.database._commit()
        return state

    def acquire_task_lease(
        self, task_id: str, owner_id: str, *, ttl_seconds: float,
        now: datetime | None = None,
    ) -> TaskOrchestratorLease | None:
        if ttl_seconds <= 0:
            raise ValueError("lease ttl must be positive")
        _require_task(self.conn, task_id)
        instant = now or datetime.now(UTC)
        stamp = _timestamp(instant)
        expiry = _timestamp(instant + timedelta(seconds=ttl_seconds))
        with self.database.transaction():
            current = self.conn.execute(
                "SELECT * FROM task_orchestrator_leases WHERE task_id=?",
                (task_id,),
            ).fetchone()
            if current is not None and (
                current["owner_id"] != owner_id and current["expires_at"] > stamp
            ):
                return None
            if current is None:
                token = 1
                self.conn.execute(
                    "INSERT INTO task_orchestrator_leases(task_id,owner_id,fencing_token,expires_at,renewed_at,version,updated_at) "
                    "VALUES (?,?,?,?,?,1,?)",
                    (task_id, owner_id, token, expiry, stamp, stamp),
                )
            else:
                token = int(current["fencing_token"])
                if current["owner_id"] != owner_id:
                    token += 1
                cursor = self.conn.execute(
                    "UPDATE task_orchestrator_leases SET owner_id=?,fencing_token=?,"
                    "expires_at=?,renewed_at=?,version=version+1,updated_at=? "
                    "WHERE task_id=? AND version=?",
                    (
                        owner_id, token, expiry, stamp, stamp, task_id,
                        int(current["version"]),
                    ),
                )
                if cursor.rowcount != 1:
                    return None
            return TaskOrchestratorLease(
                task_id=task_id, owner_id=owner_id, fencing_token=token,
                expires_at=expiry, renewed_at=stamp,
            )

    def renew_task_lease(
        self, lease: TaskOrchestratorLease, *, ttl_seconds: float,
        now: datetime | None = None,
    ) -> TaskOrchestratorLease | None:
        instant = now or datetime.now(UTC)
        stamp = _timestamp(instant)
        expiry = _timestamp(instant + timedelta(seconds=ttl_seconds))
        cursor = self.conn.execute(
            "UPDATE task_orchestrator_leases SET expires_at=?,renewed_at=?,"
            "version=version+1,updated_at=? WHERE task_id=? AND owner_id=? "
            "AND fencing_token=? AND expires_at>?",
            (
                expiry, stamp, stamp, lease.task_id, lease.owner_id,
                lease.fencing_token, stamp,
            ),
        )
        self.database._commit()
        if cursor.rowcount != 1:
            return None
        return lease.model_copy(update={"expires_at": expiry, "renewed_at": stamp})

    def validate_task_lease(
        self, lease: TaskOrchestratorLease, *, now: datetime | None = None
    ) -> bool:
        stamp = _timestamp(now or datetime.now(UTC))
        return self.conn.execute(
            "SELECT 1 FROM task_orchestrator_leases WHERE task_id=? AND owner_id=? "
            "AND fencing_token=? AND expires_at>?",
            (lease.task_id, lease.owner_id, lease.fencing_token, stamp),
        ).fetchone() is not None

    def release_task_lease(self, lease: TaskOrchestratorLease) -> bool:
        cursor = self.conn.execute(
            "DELETE FROM task_orchestrator_leases WHERE task_id=? AND owner_id=? "
            "AND fencing_token=?",
            (lease.task_id, lease.owner_id, lease.fencing_token),
        )
        self.database._commit()
        return cursor.rowcount == 1

    def get_assignment(self, assignment_id: str) -> SolverAssignment | None:
        row = self.conn.execute(
            "SELECT payload_json FROM solver_assignments WHERE id=?", (assignment_id,)
        ).fetchone()
        return SolverAssignment.model_validate_json(row["payload_json"]) if row else None

    def get_assignment_for_solver(self, solver_id: str) -> SolverAssignment | None:
        row = self.conn.execute(
            "SELECT payload_json FROM solver_assignments WHERE solver_id=? ORDER BY attempt DESC LIMIT 1",
            (solver_id,),
        ).fetchone()
        return SolverAssignment.model_validate_json(row["payload_json"]) if row else None

    def list_assignments(self, task_id: str) -> list[SolverAssignment]:
        rows = self.conn.execute(
            "SELECT payload_json FROM solver_assignments WHERE task_id=? ORDER BY created_at,id",
            (task_id,),
        ).fetchall()
        return [SolverAssignment.model_validate_json(row["payload_json"]) for row in rows]

    def save_assignment(self, assignment: SolverAssignment) -> SolverAssignment:
        existing = self.get_assignment(assignment.id)
        if existing is not None:
            if existing == assignment:
                return existing
            raise PersistenceConflict(f"SolverAssignment is immutable: {assignment.id}")
        self.conn.execute(
            "INSERT INTO solver_assignments(id,task_id,solver_id,intent_id,supervisor_solver_id,attempt,status,version,payload_json,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,1,?,?,?)",
            (
                assignment.id, assignment.task_id, assignment.solver_id,
                assignment.intent_id, assignment.assigned_by_solver_id,
                assignment.attempt, assignment.status, assignment.model_dump_json(),
                assignment.assigned_at, assignment.accepted_at or assignment.assigned_at,
            ),
        )
        self.database._commit()
        return assignment

    def complete_assignment(self, assignment_id: str, *, finished_at: str) -> SolverAssignment:
        current = self.get_assignment(assignment_id)
        if current is None:
            raise KeyError(f"assignment not found: {assignment_id}")
        if current.status == "completed":
            return current
        replacement = current.model_copy(update={"status": "completed", "finished_at": finished_at})
        cursor = self.conn.execute(
            "UPDATE solver_assignments SET status='completed',version=version+1,payload_json=?,updated_at=? "
            "WHERE id=? AND status='accepted'",
            (replacement.model_dump_json(), finished_at, assignment_id),
        )
        if cursor.rowcount != 1:
            raise PersistenceConflict(f"assignment is not active: {assignment_id}")
        self.database._commit()
        return replacement

    def cancel_assignment(self, assignment_id: str, *, finished_at: str) -> SolverAssignment:
        current = self.get_assignment(assignment_id)
        if current is None:
            raise KeyError(f"assignment not found: {assignment_id}")
        if current.status == "cancelled":
            return current
        if current.status in {"released", "completed"}:
            raise PersistenceConflict(f"assignment is already terminal: {assignment_id}")
        replacement = current.model_copy(
            update={"status": "cancelled", "finished_at": finished_at}
        )
        cursor = self.conn.execute(
            "UPDATE solver_assignments SET status='cancelled',version=version+1,payload_json=?,updated_at=? "
            "WHERE id=? AND status IN ('proposed','accepted')",
            (replacement.model_dump_json(), finished_at, assignment_id),
        )
        if cursor.rowcount != 1:
            raise PersistenceConflict(f"assignment is not cancellable: {assignment_id}")
        self.database._commit()
        return replacement

    def create_solver_run(self, run: SolverRun) -> SolverRun:
        existing = self.get_solver_run(run.id)
        if existing is not None:
            if existing == run:
                return existing
            raise PersistenceConflict(f"SolverRun is immutable at creation: {run.id}")
        _require_owned(self.conn, "solver_instances", run.solver_id, run.task_id, "run solver")
        if run.assignment_id:
            _require_owned(
                self.conn, "solver_assignments", run.assignment_id, run.task_id,
                "run assignment",
            )
        if run.intent_id:
            _require_owned(self.conn, "intents", run.intent_id, run.task_id, "run intent")
        try:
            self.conn.execute(
                "INSERT INTO solver_runs("
                "id,task_id,solver_id,assignment_id,intent_id,orchestration_role,state,"
                "attempt,turn_count,max_turns,lease_owner,fencing_token,lease_expires_at,heartbeat_at,result_id,"
                "error_code,version,payload_json,created_at,updated_at"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?)",
                (
                    run.id, run.task_id, run.solver_id, run.assignment_id, run.intent_id,
                    run.orchestration_role, run.state, run.attempt, run.turn_count,
                    run.max_turns, run.lease_owner,
                    run.fencing_token, run.lease_expires_at, run.heartbeat_at, run.result_id,
                    run.error_code, run.model_dump_json(), run.created_at, run.updated_at,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise PersistenceConflict(f"SolverRun creation conflict: {run.id}") from exc
        self.database._commit()
        return run

    def get_solver_run(self, run_id: str) -> SolverRun | None:
        row = self.conn.execute(
            "SELECT payload_json FROM solver_runs WHERE id=?", (run_id,)
        ).fetchone()
        return SolverRun.model_validate_json(row["payload_json"]) if row else None

    def list_solver_runs(self, task_id: str) -> list[SolverRun]:
        rows = self.conn.execute(
            "SELECT payload_json FROM solver_runs WHERE task_id=? ORDER BY created_at,id",
            (task_id,),
        ).fetchall()
        return [SolverRun.model_validate_json(row["payload_json"]) for row in rows]

    def active_run_fencing_token(self, solver_id: str) -> int | None:
        row = self.conn.execute(
            "SELECT fencing_token FROM solver_runs WHERE solver_id=? "
            "AND state IN ('leased','running') ORDER BY updated_at DESC LIMIT 1",
            (solver_id,),
        ).fetchone()
        return int(row["fencing_token"]) if row else None

    def claim_solver_run(
        self, run_id: str, owner_id: str, *, ttl_seconds: float,
        expected_version: int, max_active_workers: int | None = None,
        now: datetime | None = None,
    ) -> SolverRun | None:
        if ttl_seconds <= 0:
            raise ValueError("run lease ttl must be positive")
        instant = now or datetime.now(UTC)
        stamp = _timestamp(instant)
        expiry = _timestamp(instant + timedelta(seconds=ttl_seconds))
        try:
            with self.database.transaction():
                current = self.get_solver_run(run_id)
                if current is None or current.version != expected_version:
                    return None
                if current.state not in {"queued", "retry_queued"}:
                    return None
                if max_active_workers is not None and current.orchestration_role == "worker":
                    active = int(self.conn.execute(
                        "SELECT COUNT(*) FROM solver_runs WHERE task_id=? "
                        "AND orchestration_role='worker' AND state IN ('leased','running')",
                        (current.task_id,),
                    ).fetchone()[0])
                    if active >= max_active_workers:
                        return None
                replacement = SolverRun.model_validate(current.model_dump() | {
                    "state": "leased",
                    "lease_owner": owner_id,
                    "fencing_token": current.fencing_token + 1,
                    "lease_expires_at": expiry,
                    "heartbeat_at": stamp,
                    "updated_at": stamp,
                    "version": current.version + 1,
                })
                cursor = self.conn.execute(
                    "UPDATE solver_runs SET state='leased',lease_owner=?,fencing_token=?,"
                    "lease_expires_at=?,heartbeat_at=?,version=?,payload_json=?,updated_at=? "
                    "WHERE id=? AND version=? AND state IN ('queued','retry_queued')",
                    (
                        owner_id, replacement.fencing_token, expiry, stamp,
                        replacement.version, replacement.model_dump_json(), stamp,
                        run_id, expected_version,
                    ),
                )
                if cursor.rowcount != 1:
                    return None
        except sqlite3.IntegrityError as exc:
            raise PersistenceConflict(
                f"Intent already has an active SolverRun: {current.intent_id}"
            ) from exc
        return replacement

    def start_solver_run(
        self, run_id: str, owner_id: str, fencing_token: int,
        *, now: datetime | None = None,
    ) -> SolverRun:
        instant = now or datetime.now(UTC)
        stamp = _timestamp(instant)
        current = self.get_solver_run(run_id)
        if current is None:
            raise KeyError(f"SolverRun not found: {run_id}")
        replacement = SolverRun.model_validate(current.model_dump() | {
            "state": "running", "started_at": current.started_at or stamp,
            "heartbeat_at": stamp, "updated_at": stamp, "version": current.version + 1,
        })
        cursor = self.conn.execute(
            "UPDATE solver_runs SET state='running',heartbeat_at=?,version=?,payload_json=?,"
            "updated_at=? WHERE id=? AND state='leased' AND lease_owner=? "
            "AND fencing_token=? AND lease_expires_at>? AND version=?",
            (
                stamp, replacement.version, replacement.model_dump_json(), stamp,
                run_id, owner_id, fencing_token, stamp, current.version,
            ),
        )
        if cursor.rowcount != 1:
            raise PersistenceConflict(f"SolverRun lease is no longer valid: {run_id}")
        self.database._commit()
        return replacement

    def renew_solver_run(
        self, run_id: str, owner_id: str, fencing_token: int, *, ttl_seconds: float,
        now: datetime | None = None,
    ) -> SolverRun | None:
        if ttl_seconds <= 0:
            raise ValueError("run lease ttl must be positive")
        instant = now or datetime.now(UTC)
        stamp = _timestamp(instant)
        expiry = _timestamp(instant + timedelta(seconds=ttl_seconds))
        current = self.get_solver_run(run_id)
        if current is None or current.state not in {"leased", "running"}:
            return None
        replacement = SolverRun.model_validate(current.model_dump() | {
            "lease_expires_at": expiry, "heartbeat_at": stamp,
            "updated_at": stamp, "version": current.version + 1,
        })
        cursor = self.conn.execute(
            "UPDATE solver_runs SET lease_expires_at=?,heartbeat_at=?,version=?,payload_json=?,"
            "updated_at=? WHERE id=? AND state IN ('leased','running') AND lease_owner=? "
            "AND fencing_token=? AND lease_expires_at>? AND version=?",
            (
                expiry, stamp, replacement.version, replacement.model_dump_json(), stamp,
                run_id, owner_id, fencing_token, stamp, current.version,
            ),
        )
        if cursor.rowcount != 1:
            return None
        self.database._commit()
        return replacement

    def finish_solver_run(
        self, run_id: str, owner_id: str, fencing_token: int, *, state: str,
        result_id: str | None = None, error_code: str | None = None,
        error_message: str | None = None, now: datetime | None = None,
    ) -> SolverRun:
        if state not in {"completed", "failed", "cancelled"}:
            raise ValueError("invalid terminal SolverRun state")
        stamp = _timestamp(now or datetime.now(UTC))
        current = self.get_solver_run(run_id)
        if current is None:
            raise KeyError(f"SolverRun not found: {run_id}")
        if current.state == state and current.result_id == result_id:
            return current
        replacement = SolverRun.model_validate(current.model_dump() | {
            "state": state, "result_id": result_id, "error_code": error_code,
            "error_message": error_message, "finished_at": stamp,
            "updated_at": stamp, "version": current.version + 1,
        })
        cursor = self.conn.execute(
            "UPDATE solver_runs SET state=?,result_id=?,error_code=?,version=?,payload_json=?,"
            "updated_at=? WHERE id=? AND state IN ('leased','running') AND lease_owner=? "
            "AND fencing_token=? AND lease_expires_at>? AND version=?",
            (
                state, result_id, error_code, replacement.version,
                replacement.model_dump_json(), stamp, run_id, owner_id,
                fencing_token, stamp, current.version,
            ),
        )
        if cursor.rowcount != 1:
            raise PersistenceConflict(f"late SolverRun result rejected: {run_id}")
        self.database._commit()
        return replacement

    def reserve_solver_run_turn(
        self,
        run_id: str,
        owner_id: str,
        fencing_token: int,
        *,
        now: datetime | None = None,
    ) -> SolverRun | None:
        """Atomically reserve one model turn from one fenced SolverRun."""
        instant = now or datetime.now(UTC)
        stamp = _timestamp(instant)
        with self.database.transaction():
            current = self.get_solver_run(run_id)
            if current is None or current.state != "running":
                return None
            if (
                current.lease_owner != owner_id
                or current.fencing_token != fencing_token
                or not current.lease_expires_at
                or current.lease_expires_at <= stamp
                or current.turn_count >= current.max_turns
            ):
                return None
            session_cursor = self.conn.execute(
                "UPDATE sessions SET turn_count=turn_count+1 "
                "WHERE task_id=? AND status='running' AND turn_count<max_turns",
                (current.task_id,),
            )
            if session_cursor.rowcount != 1:
                return None
            replacement = SolverRun.model_validate(current.model_dump() | {
                "turn_count": current.turn_count + 1,
                "updated_at": stamp,
                "version": current.version + 1,
            })
            cursor = self.conn.execute(
                "UPDATE solver_runs SET turn_count=?,version=?,payload_json=?,updated_at=? "
                "WHERE id=? AND state='running' AND lease_owner=? AND fencing_token=? "
                "AND lease_expires_at>? AND turn_count<? AND version=?",
                (
                    replacement.turn_count, replacement.version,
                    replacement.model_dump_json(), stamp, run_id, owner_id,
                    fencing_token, stamp, current.max_turns, current.version,
                ),
            )
            return replacement if cursor.rowcount == 1 else None

    def validate_solver_run_authority(
        self, run_id: str, owner_id: str, fencing_token: int,
        *, now: datetime | None = None,
    ) -> bool:
        stamp = _timestamp(now or datetime.now(UTC))
        return self.conn.execute(
            "SELECT 1 FROM solver_runs WHERE id=? AND state='running' "
            "AND lease_owner=? AND fencing_token=? AND lease_expires_at>?",
            (run_id, owner_id, fencing_token, stamp),
        ).fetchone() is not None

    def suspend_solver_run_for_approval(
        self, run_id: str, owner_id: str, fencing_token: int,
        *, now: datetime | None = None,
    ) -> SolverRun:
        stamp = _timestamp(now or datetime.now(UTC))
        current = self.get_solver_run(run_id)
        if current is None:
            raise KeyError(f"SolverRun not found: {run_id}")
        replacement = SolverRun.model_validate(current.model_dump() | {
            "state": "waiting_approval",
            "lease_owner": None,
            "lease_expires_at": None,
            "heartbeat_at": None,
            "updated_at": stamp,
            "version": current.version + 1,
        })
        cursor = self.conn.execute(
            "UPDATE solver_runs SET state='waiting_approval',lease_owner=NULL,"
            "lease_expires_at=NULL,heartbeat_at=NULL,version=?,payload_json=?,updated_at=? "
            "WHERE id=? AND state='running' AND lease_owner=? AND fencing_token=? "
            "AND lease_expires_at>? AND version=?",
            (
                replacement.version, replacement.model_dump_json(), stamp, run_id,
                owner_id, fencing_token, stamp, current.version,
            ),
        )
        if cursor.rowcount != 1:
            raise PersistenceConflict(f"SolverRun lease is no longer valid: {run_id}")
        self.database._commit()
        return replacement

    def resume_solver_run_after_approval(
        self, solver_id: str, intent_id: str | None,
        *, now: datetime | None = None,
    ) -> SolverRun | None:
        stamp = _timestamp(now or datetime.now(UTC))
        row = self.conn.execute(
            "SELECT payload_json FROM solver_runs WHERE solver_id=? "
            "AND intent_id IS ? AND state='waiting_approval' "
            "ORDER BY attempt DESC,created_at DESC LIMIT 1",
            (solver_id, intent_id),
        ).fetchone()
        if row is None:
            return None
        current = SolverRun.model_validate_json(row["payload_json"])
        replacement = SolverRun.model_validate(current.model_dump() | {
            "state": "retry_queued",
            "updated_at": stamp,
            "version": current.version + 1,
        })
        cursor = self.conn.execute(
            "UPDATE solver_runs SET state='retry_queued',version=?,payload_json=?,updated_at=? "
            "WHERE id=? AND state='waiting_approval' AND version=?",
            (
                replacement.version, replacement.model_dump_json(), stamp,
                current.id, current.version,
            ),
        )
        if cursor.rowcount != 1:
            raise PersistenceConflict(
                f"SolverRun approval continuation changed concurrently: {current.id}"
            )
        self.database._commit()
        return replacement

    def expire_solver_runs(self, *, now: datetime | None = None) -> list[SolverRun]:
        stamp = _timestamp(now or datetime.now(UTC))
        rows = self.conn.execute(
            "SELECT payload_json FROM solver_runs WHERE state IN ('leased','running') "
            "AND lease_expires_at<=? ORDER BY created_at,id", (stamp,),
        ).fetchall()
        expired: list[SolverRun] = []
        with self.database.transaction():
            for row in rows:
                current = SolverRun.model_validate_json(row["payload_json"])
                replacement = SolverRun.model_validate(current.model_dump() | {
                    "state": "expired", "finished_at": stamp,
                    "error_code": "SOLVER_RUN_LEASE_EXPIRED",
                    "error_message": "SolverRun heartbeat lease expired",
                    "updated_at": stamp, "version": current.version + 1,
                })
                cursor = self.conn.execute(
                    "UPDATE solver_runs SET state='expired',error_code=?,version=?,payload_json=?,"
                    "updated_at=? WHERE id=? AND state IN ('leased','running') "
                    "AND lease_expires_at<=? AND version=?",
                    (
                        replacement.error_code, replacement.version,
                        replacement.model_dump_json(), stamp, current.id, stamp,
                        current.version,
                    ),
                )
                if cursor.rowcount == 1:
                    expired.append(replacement)
        return expired

    def cancel_task_solver_runs(
        self, task_id: str, *, now: datetime | None = None
    ) -> list[SolverRun]:
        stamp = _timestamp(now or datetime.now(UTC))
        active_states = {"queued", "leased", "running", "waiting_approval", "retry_queued"}
        cancelled: list[SolverRun] = []
        with self.database.transaction():
            for current in self.list_solver_runs(task_id):
                if current.state not in active_states:
                    continue
                replacement = SolverRun.model_validate(current.model_dump() | {
                    "state": "cancelled",
                    "finished_at": stamp,
                    "error_code": "TASK_CANCELLED",
                    "error_message": "Task cancellation invalidated the SolverRun",
                    "updated_at": stamp,
                    "version": current.version + 1,
                })
                cursor = self.conn.execute(
                    "UPDATE solver_runs SET state='cancelled',error_code=?,version=?,"
                    "payload_json=?,updated_at=? WHERE id=? AND version=? AND state IN "
                    "('queued','leased','running','waiting_approval','retry_queued')",
                    (
                        replacement.error_code, replacement.version,
                        replacement.model_dump_json(), stamp, current.id, current.version,
                    ),
                )
                if cursor.rowcount == 1:
                    cancelled.append(replacement)
        return cancelled

    def cancel_solver_runs(
        self,
        task_id: str,
        solver_id: str,
        *,
        reason: str = "SOLVER_CANCELLED",
        now: datetime | None = None,
    ) -> list[SolverRun]:
        stamp = _timestamp(now or datetime.now(UTC))
        active_states = {"queued", "leased", "running", "waiting_approval", "retry_queued"}
        cancelled: list[SolverRun] = []
        with self.database.transaction():
            for current in self.list_solver_runs(task_id):
                if current.solver_id != solver_id or current.state not in active_states:
                    continue
                replacement = SolverRun.model_validate(current.model_dump() | {
                    "state": "cancelled",
                    "finished_at": stamp,
                    "error_code": reason,
                    "error_message": "Solver control invalidated the SolverRun",
                    "updated_at": stamp,
                    "version": current.version + 1,
                })
                cursor = self.conn.execute(
                    "UPDATE solver_runs SET state='cancelled',error_code=?,version=?,"
                    "payload_json=?,updated_at=? WHERE id=? AND version=? AND state IN "
                    "('queued','leased','running','waiting_approval','retry_queued')",
                    (
                        replacement.error_code,
                        replacement.version,
                        replacement.model_dump_json(),
                        stamp,
                        current.id,
                        current.version,
                    ),
                )
                if cursor.rowcount == 1:
                    cancelled.append(replacement)
        return cancelled

    def is_worker_result_merged(self, result_id: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM worker_result_merges WHERE worker_result_id=?", (result_id,)
        ).fetchone() is not None

    def mark_worker_result_merged(
        self, result_id: str, *, task_id: str, intent_id: str,
        supervisor_solver_id: str, merged_at: str,
    ) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO worker_result_merges(worker_result_id,task_id,intent_id,merged_by_solver_id,merged_at) "
            "VALUES (?,?,?,?,?)",
            (result_id, task_id, intent_id, supervisor_solver_id, merged_at),
        )
        self.database._commit()

    def save_review_result(self, result: ReviewResult) -> str:
        result_id = f"review_result_{result.solver_id}"
        self._save_role_result("review_results", result_id, result)
        return result_id

    def save_report_result(self, result: ReportResult) -> str:
        result_id = f"report_result_{result.solver_id}"
        self._save_role_result("report_results", result_id, result)
        return result_id

    def _save_role_result(self, table: str, result_id: str, result: Any) -> None:
        existing = self.conn.execute(
            f"SELECT payload_json FROM {table} WHERE id=?", (result_id,)
        ).fetchone()
        if existing is not None:
            if json.loads(existing["payload_json"]) == result.model_dump(mode="json"):
                return
            raise PersistenceConflict(f"role result is immutable: {result_id}")
        self.conn.execute(
            f"INSERT INTO {table}(id,task_id,solver_id,status,payload_json,created_at) VALUES (?,?,?,?,?,?)",
            (
                result_id, result.task_id, result.solver_id, result.status,
                result.model_dump_json(), utc_now(),
            ),
        )
        self.database._commit()


class SqliteTranscriptRepository:
    def __init__(self, database: Database):
        self.database = database
        self.conn = database.conn

    def list_messages(self, task_id: str, solver_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT seq,role,payload_json,created_at FROM transcript_messages "
            "WHERE task_id=? AND solver_id=? ORDER BY seq", (task_id, solver_id)
        ).fetchall()
        return [
            {
                "seq": row["seq"], "role": row["role"],
                **json.loads(row["payload_json"]), "created_at": row["created_at"],
            }
            for row in rows
        ]

    def append_message(self, task_id: str, solver_id: str, message: dict[str, Any]) -> None:
        with self.database.transaction():
            self._append_message(task_id, solver_id, message)

    def _append_message(self, task_id: str, solver_id: str, message: dict[str, Any]) -> None:
        _require_task(self.conn, task_id)
        now = utc_now()
        row = self.conn.execute(
            "INSERT INTO transcript_metadata(task_id,solver_id,version,next_seq,created_at,updated_at) "
            "VALUES (?,?,1,2,?,?) ON CONFLICT(task_id,solver_id) DO UPDATE SET "
            "version=transcript_metadata.version+1,next_seq=transcript_metadata.next_seq+1,"
            "updated_at=excluded.updated_at RETURNING next_seq-1",
            (task_id, solver_id, now, now),
        ).fetchone()
        seq = int(row[0])
        role = str(message.get("role") or "assistant")
        payload = {key: value for key, value in message.items() if key != "role"}
        self.conn.execute(
            "INSERT INTO transcript_messages(task_id,solver_id,seq,role,payload_json,created_at) "
            "VALUES (?,?,?,?,?,?)", (task_id, solver_id, seq, role, _json(payload), now),
        )
        self.database._commit()


class SqliteEventRepository:
    def __init__(self, database: Database):
        self.database = database
        self.conn = database.conn

    def append_agent_event(
        self, task_id: str, type: str, payload: dict[str, Any], *,
        solver_id: str | None = None, intent_id: str | None = None,
    ) -> AgentEvent:
        with self.database.transaction():
            return self._append_agent_event(
                task_id, type, payload, solver_id=solver_id, intent_id=intent_id
            )

    def _append_agent_event(
        self, task_id: str, type: str, payload: dict[str, Any], *,
        solver_id: str | None = None, intent_id: str | None = None,
    ) -> AgentEvent:
        from tga.domain.events import normalize_event_payload

        _require_task(self.conn, task_id)
        # Event payloads are an evolvable audit envelope.  Persisting optional
        # fields as JSON null made clients reject an entire snapshot when a
        # control event had no action_id.  Omit absent values at the single
        # write boundary instead; concrete false/zero values remain intact.
        payload = normalize_event_payload(type, _compact_event_payload(payload))
        now = utc_now()
        seq = int(
            self.conn.execute(
                "INSERT INTO agent_event_sequences(task_id,next_seq) VALUES (?,2) "
                "ON CONFLICT(task_id) DO UPDATE SET next_seq=agent_event_sequences.next_seq+1 "
                "RETURNING next_seq-1", (task_id,),
            ).fetchone()[0]
        )
        event = AgentEvent(
            schema_version=6, id=f"evt_{uuid4().hex}", task_id=task_id,
            solver_id=solver_id, intent_id=intent_id,
            seq=seq, type=type, payload=payload, created_at=now,
        )
        self.conn.execute(
            "INSERT INTO agent_events(id,schema_version,task_id,solver_id,intent_id,seq,type,payload_json,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                event.id, 6, task_id, solver_id, intent_id, seq, type,
                _json(payload), now,
            ),
        )
        self.database._commit()
        from tga.infrastructure.events import runtime_event_bus

        runtime_event_bus.publish(event)
        return event

    def list_agent_events(
        self, task_id: str, *, after_seq: int = 0, limit: int | None = 200
    ) -> list[AgentEvent]:
        bounded = 200 if limit is None else max(1, min(limit, 1000))
        rows = self.conn.execute(
            "SELECT * FROM agent_events WHERE task_id=? AND seq>? ORDER BY seq LIMIT ?",
            (task_id, after_seq, bounded),
        ).fetchall()
        return [
            AgentEvent(
                schema_version=row["schema_version"], id=row["id"], task_id=row["task_id"],
                solver_id=row["solver_id"], intent_id=row["intent_id"],
                seq=row["seq"], type=row["type"],
                payload=json.loads(row["payload_json"]), created_at=row["created_at"],
            )
            for row in rows
        ]


def _require_task(conn: sqlite3.Connection, task_id: str) -> None:
    if conn.execute("SELECT 1 FROM tasks WHERE id=?", (task_id,)).fetchone() is None:
        raise OwnershipError(f"task does not exist: {task_id}")


def _compact_event_payload(value: Any) -> Any:
    """Drop absent (None) values so the audit envelope stays forward-compatible."""
    if isinstance(value, dict):
        return {
            str(key): _compact_event_payload(item)
            for key, item in value.items()
            if item is not None
        }
    if isinstance(value, list):
        return [_compact_event_payload(item) for item in value]
    return value


def _require_owned(
    conn: sqlite3.Connection, table: str, record_id: str, task_id: str, label: str
) -> None:
    if table not in {
        "intents",
        "artifacts",
        "evidence_claims",
        "solver_instances",
        "knowledge_items",
        "solver_assignments",
    }:
        raise ValueError("unsupported ownership table")
    row = conn.execute(f"SELECT task_id FROM {table} WHERE id=?", (record_id,)).fetchone()
    if row is None or row["task_id"] != task_id:
        raise OwnershipError(f"{label} does not belong to task")


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


__all__ = [
    "ArtifactImmutableError", "IntentClaimConflict", "OwnershipError",
    "PersistenceConflict", "PlanVersionConflict", "SqliteEvidenceRepository",
    "SqliteEventRepository", "SqliteKnowledgeRepository", "SqlitePlanRepository",
    "SqliteOrchestrationRepository", "SqliteSolverRepository", "SqliteTaskRepository",
    "SqliteTranscriptRepository",
]
