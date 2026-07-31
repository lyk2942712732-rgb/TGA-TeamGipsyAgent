"""SQLite persistence for task-scoped sandbox desired state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from tga.evidence.database import utc_now
from tga.sandbox.models import SandboxHandle, SandboxState


@dataclass(slots=True)
class SandboxInstanceRepository:
    database: object

    def get_active(self, task_id: str) -> SandboxHandle | None:
        row = self.database.conn.execute(
            "SELECT * FROM sandbox_instances WHERE task_id=? "
            "AND state IN ('acquiring','ready','released') ORDER BY created_at DESC LIMIT 1",
            (task_id,),
        ).fetchone()
        return self._handle(row) if row else None

    def put(self, handle: SandboxHandle) -> None:
        now = utc_now()
        self.database.conn.execute(
            "INSERT INTO sandbox_instances("
            "instance_id,task_id,solver_id,profile_id,provider,config_digest,"
            "fencing_token,state,created_at,updated_at"
            ") VALUES (?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(instance_id) DO UPDATE SET "
            "solver_id=excluded.solver_id,profile_id=excluded.profile_id,"
            "fencing_token=excluded.fencing_token,state=excluded.state,updated_at=excluded.updated_at",
            (
                handle.instance_id,
                handle.task_id,
                handle.solver_id,
                handle.profile_id,
                handle.provider,
                handle.config_digest,
                handle.fencing_token,
                handle.state.value,
                now,
                now,
            ),
        )
        self.database._commit()

    def transition(
        self, instance_id: str, state: SandboxState, *, destroy_after: str | None = None
    ) -> None:
        self.database.conn.execute(
            "UPDATE sandbox_instances SET state=?,destroy_after=?,updated_at=? WHERE instance_id=?",
            (state.value, destroy_after, utc_now(), instance_id),
        )
        self.database._commit()

    def due_for_destroy(self, now: str | None = None) -> tuple[SandboxHandle, ...]:
        now = now or datetime.now(UTC).isoformat().replace("+00:00", "Z")
        rows = self.database.conn.execute(
            "SELECT * FROM sandbox_instances WHERE state='released' "
            "AND destroy_after IS NOT NULL AND destroy_after<=? ORDER BY destroy_after",
            (now,),
        ).fetchall()
        return tuple(self._handle(row) for row in rows)

    def active_instance_ids(self) -> tuple[str, ...]:
        rows = self.database.conn.execute(
            "SELECT instance_id FROM sandbox_instances "
            "WHERE state IN ('acquiring','ready','released','destroying')"
        ).fetchall()
        return tuple(row["instance_id"] for row in rows)

    @staticmethod
    def _handle(row) -> SandboxHandle:
        return SandboxHandle(
            instance_id=row["instance_id"],
            task_id=row["task_id"],
            solver_id=row["solver_id"],
            profile_id=row["profile_id"],
            provider=row["provider"],
            config_digest=row["config_digest"],
            fencing_token=row["fencing_token"],
            state=row["state"],
        )
