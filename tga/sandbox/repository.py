"""SQLite persistence for SolverRun-scoped sandbox desired state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from tga.evidence.database import utc_now
from tga.sandbox.models import SandboxHandle, SandboxState


@dataclass(slots=True)
class SandboxInstanceRepository:
    database: object

    def get_active(
        self, *, task_id: str, solver_id: str, solver_run_id: str
    ) -> SandboxHandle | None:
        row = self.database.conn.execute(
            "SELECT * FROM sandbox_instances WHERE task_id=? AND solver_id=? "
            "AND solver_run_id=? AND state IN ('acquiring','ready') "
            "ORDER BY created_at DESC LIMIT 1",
            (task_id, solver_id, solver_run_id),
        ).fetchone()
        return self._handle(row) if row else None

    def list_active(
        self, *, task_id: str, solver_run_id: str | None = None
    ) -> tuple[SandboxHandle, ...]:
        sql = (
            "SELECT * FROM sandbox_instances WHERE task_id=? "
            "AND state IN ('acquiring','ready')"
        )
        params: tuple[str, ...] = (task_id,)
        if solver_run_id is not None:
            sql += " AND solver_run_id=?"
            params += (solver_run_id,)
        rows = self.database.conn.execute(sql + " ORDER BY created_at", params).fetchall()
        return tuple(self._handle(row) for row in rows)

    def put(self, handle: SandboxHandle) -> None:
        now = utc_now()
        self.database.conn.execute(
            "INSERT INTO sandbox_instances("
            "instance_id,task_id,solver_id,solver_run_id,profile_id,provider,config_digest,"
            "image_digest,toolset_digest,fencing_token,state,created_at,updated_at"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(instance_id) DO UPDATE SET "
            "solver_id=excluded.solver_id,profile_id=excluded.profile_id,"
            "fencing_token=excluded.fencing_token,state=excluded.state,updated_at=excluded.updated_at",
            (
                handle.instance_id,
                handle.task_id,
                handle.solver_id,
                handle.solver_run_id,
                handle.profile_id,
                handle.provider,
                handle.config_digest,
                handle.image_digest,
                handle.toolset_digest,
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

    def release_orphans(self, now: str | None = None) -> tuple[SandboxHandle, ...]:
        """Release sandboxes whose owning SolverRun can no longer use them.

        This is the crash-recovery path.  A normal worker releases its sandbox
        in a ``finally`` block, but a killed API process cannot run that block.
        Waiting-for-approval runs deliberately retain their sandbox; expired
        leases and every terminal state do not.
        """
        now = now or datetime.now(UTC).isoformat().replace("+00:00", "Z")
        rows = self.database.conn.execute(
            "SELECT sandbox_instances.* FROM sandbox_instances "
            "LEFT JOIN solver_runs ON solver_runs.id=sandbox_instances.solver_run_id "
            "WHERE sandbox_instances.state IN ('acquiring','ready') AND ("
            "solver_runs.id IS NULL OR "
            "solver_runs.state NOT IN ('leased','running','waiting_approval','retry_queued') OR "
            "(solver_runs.state IN ('leased','running') AND ("
            "solver_runs.lease_expires_at IS NULL OR solver_runs.lease_expires_at<=?))) "
            "ORDER BY sandbox_instances.created_at",
            (now,),
        ).fetchall()
        handles = tuple(self._handle(row) for row in rows)
        if handles:
            with self.database.transaction():
                for handle in handles:
                    self.database.conn.execute(
                        "UPDATE sandbox_instances SET state='released',destroy_after=?,updated_at=? "
                        "WHERE instance_id=? AND state IN ('acquiring','ready')",
                        (now, now, handle.instance_id),
                    )
        return handles

    def list_managed(self) -> tuple[SandboxHandle, ...]:
        rows = self.database.conn.execute(
            "SELECT * FROM sandbox_instances "
            "WHERE state IN ('acquiring','ready','released','destroying') "
            "ORDER BY created_at"
        ).fetchall()
        return tuple(self._handle(row) for row in rows)

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
        legacy_identity = f"legacy-{row['instance_id']}"[:128]
        return SandboxHandle(
            instance_id=row["instance_id"],
            task_id=row["task_id"],
            solver_id=row["solver_id"],
            solver_run_id=row["solver_run_id"] or legacy_identity,
            profile_id=row["profile_id"],
            provider=row["provider"],
            config_digest=row["config_digest"],
            image_digest=row["image_digest"] or "0" * 64,
            toolset_digest=row["toolset_digest"],
            fencing_token=row["fencing_token"],
            state=row["state"],
        )
