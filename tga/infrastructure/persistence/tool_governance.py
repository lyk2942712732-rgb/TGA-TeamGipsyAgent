"""SQLite implementation of governed Action lifecycle primitives."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from tga.evidence.database import Database, utc_now
from tga.infrastructure.persistence.errors import (
    ActionTransitionConflict,
    PersistenceConflict,
)


ACTION_TRANSITIONS = {
    "proposed": {"validated", "denied", "cancelled"},
    "validated": {"denied", "pending_approval", "queued", "cancelled"},
    "pending_approval": {"approved", "rejected", "expired", "cancelled"},
    "approved": {"queued", "cancelled"},
    "queued": {"running", "cancelled"},
    "running": {"succeeded", "failed", "blocked", "cancelled"},
    "denied": set(),
    "succeeded": set(),
    "failed": set(),
    "blocked": set(),
    "rejected": set(),
    "expired": set(),
    "cancelled": set(),
}


class SqliteToolGovernanceRepository:
    def __init__(self, database: Database):
        self.database = database
        self.conn = database.conn

    def add_action(self, action: Any) -> None:
        payload = action.model_dump(mode="json")
        context = payload["context"]
        try:
            self.conn.execute(
                "INSERT INTO governed_actions(id,task_id,solver_id,intent_id,tool_call_id,tool_class,capability,execution_profile_id,sandbox_config_digest,attempt,status,semantic_fingerprint,idempotency_key,resource_lock_key,payload_json,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    action.id, context["task_id"], context["solver_id"],
                    context.get("intent_id"), action.tool_call_id, action.tool_class,
                    action.capability, action.execution_profile_id,
                    action.sandbox_config_digest, action.attempt, action.status,
                    action.semantic_fingerprint,
                    action.idempotency_key, action.resource_lock_key,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    action.created_at, action.updated_at,
                ),
            )
            self.conn.execute(
                "INSERT INTO governed_action_transitions(action_id,seq,from_status,to_status,created_at) VALUES (?,1,NULL,?,?)",
                (action.id, action.status, action.created_at),
            )
        except Exception as exc:
            raise PersistenceConflict(f"governed action already exists: {action.id}") from exc
        self.database._commit()

    def get_action(self, action_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM governed_actions WHERE id=?", (action_id,)
        ).fetchone()
        if row is None:
            return None
        return {
            **dict(row),
            "payload": json.loads(row["payload_json"]),
            "result": json.loads(row["result_json"]) if row["result_json"] else None,
        }

    def find_by_tool_call(self, task_id: str, solver_id: str, tool_call_id: str):
        row = self.conn.execute(
            "SELECT id FROM governed_actions WHERE task_id=? AND solver_id=? AND tool_call_id=?",
            (task_id, solver_id, tool_call_id),
        ).fetchone()
        return self.get_action(row["id"]) if row else None

    def transition(self, action_id: str, status: str, *, expected_status: str) -> dict[str, Any]:
        if status not in ACTION_TRANSITIONS.get(expected_status, set()):
            raise ActionTransitionConflict(
                f"invalid governed Action transition: {expected_status} -> {status}"
            )
        row = self.conn.execute(
            "SELECT payload_json,version,status FROM governed_actions WHERE id=?",
            (action_id,),
        ).fetchone()
        if row is None or row["status"] != expected_status:
            actual = row["status"] if row else "missing"
            raise ActionTransitionConflict(
                f"governed Action expected {expected_status}, found {actual}: {action_id}"
            )
        now = utc_now()
        payload = json.loads(row["payload_json"])
        payload["status"] = status
        payload["updated_at"] = now
        cursor = self.conn.execute(
            "UPDATE governed_actions SET status=?,version=version+1,payload_json=?,updated_at=? WHERE id=? AND version=? AND status=?",
            (
                status, json.dumps(payload, ensure_ascii=False, sort_keys=True), now,
                action_id, int(row["version"]), expected_status,
            ),
        )
        if cursor.rowcount != 1:
            raise ActionTransitionConflict(f"governed Action changed concurrently: {action_id}")
        seq = int(self.conn.execute(
            "SELECT COALESCE(MAX(seq),0)+1 FROM governed_action_transitions WHERE action_id=?",
            (action_id,),
        ).fetchone()[0])
        self.conn.execute(
            "INSERT INTO governed_action_transitions(action_id,seq,from_status,to_status,created_at) VALUES (?,?,?,?,?)",
            (action_id, seq, expected_status, status, now),
        )
        self.database._commit()
        return self.get_action(action_id) or {}

    def save_result(self, action_id: str, result: Any) -> None:
        payload = result.model_dump(mode="json") if hasattr(result, "model_dump") else result
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        row = self.conn.execute(
            "SELECT result_json FROM governed_actions WHERE id=?", (action_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"governed action not found: {action_id}")
        if row["result_json"] is not None:
            if row["result_json"] == encoded:
                return
            raise PersistenceConflict(f"governed Action result is immutable: {action_id}")
        self.conn.execute(
            "UPDATE governed_actions SET result_json=?,updated_at=? WHERE id=?",
            (encoded, utc_now(), action_id),
        )
        self.conn.execute(
            "UPDATE tool_idempotency_keys SET result_json=?,status='completed',updated_at=? WHERE action_id=?",
            (encoded, utc_now(), action_id),
        )
        self.database._commit()

    def find_semantic(self, action: Any) -> str | None:
        if not action.semantic_fingerprint:
            return None
        row = self.conn.execute(
            "SELECT id FROM governed_actions WHERE task_id=? AND solver_id=? AND semantic_fingerprint=? AND id<>? "
            "AND status IN ('validated','pending_approval','approved','queued','running','succeeded','failed','blocked') ORDER BY created_at DESC LIMIT 1",
            (
                action.context.task_id, action.context.solver_id,
                action.semantic_fingerprint, action.id,
            ),
        ).fetchone()
        return str(row["id"]) if row else None

    def reserve_idempotency(self, action: Any) -> tuple[bool, str, dict[str, Any] | None]:
        if not action.idempotency_key:
            return True, action.id, None
        existing = self.lookup_idempotency(action.idempotency_key)
        if existing is not None:
            return False, str(existing["action_id"]), existing["result"]
        now = utc_now()
        try:
            self.conn.execute(
                "INSERT INTO tool_idempotency_keys(idempotency_key,task_id,action_id,status,created_at,updated_at) VALUES (?,?,?,'reserved',?,?)",
                (action.idempotency_key, action.context.task_id, action.id, now, now),
            )
            self.database._commit()
            return True, action.id, None
        except Exception:
            # A concurrent process may have won the durable unique-key race.
            existing = self.lookup_idempotency(action.idempotency_key)
            if existing is None:
                raise
            return False, str(existing["action_id"]), existing["result"]

    def lookup_idempotency(self, idempotency_key: str | None) -> dict[str, Any] | None:
        if not idempotency_key:
            return None
        row = self.conn.execute(
            "SELECT action_id,result_json FROM tool_idempotency_keys WHERE idempotency_key=?",
            (idempotency_key,),
        ).fetchone()
        if row is None:
            return None
        return {
            "action_id": str(row["action_id"]),
            "result": json.loads(row["result_json"]) if row["result_json"] else None,
        }

    def acquire_lock(self, action: Any, *, ttl_seconds: float) -> bool:
        if not action.resource_lock_key:
            return True
        now = datetime.now(UTC)
        expires = now + timedelta(seconds=ttl_seconds)
        cursor = self.conn.execute(
            "INSERT INTO tool_resource_locks(lock_key,task_id,action_id,expires_at,created_at,updated_at) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(lock_key) DO UPDATE SET task_id=excluded.task_id,action_id=excluded.action_id,expires_at=excluded.expires_at,updated_at=excluded.updated_at "
            "WHERE tool_resource_locks.expires_at<=? OR tool_resource_locks.action_id=excluded.action_id",
            (
                action.resource_lock_key, action.context.task_id, action.id,
                expires.isoformat(), now.isoformat(), now.isoformat(), now.isoformat(),
            ),
        )
        self.database._commit()
        return cursor.rowcount == 1

    def release_lock(self, action: Any) -> None:
        if action.resource_lock_key:
            self.conn.execute(
                "DELETE FROM tool_resource_locks WHERE lock_key=? AND action_id=?",
                (action.resource_lock_key, action.id),
            )
            self.database._commit()

    def reserve_budget(self, action: Any, *, tool_calls: int, artifacts: int) -> dict[str, Any]:
        with self.database.transaction():
            existing = self.conn.execute(
                "SELECT * FROM tool_budget_reservations WHERE action_id=?", (action.id,)
            ).fetchone()
            if existing:
                return dict(existing)
            self._enforce_task_and_intent_budgets(
                action, tool_calls=tool_calls, artifacts=artifacts
            )
            budget_row = self.conn.execute(
                "SELECT budget_json,usage_json FROM solver_budgets WHERE solver_id=?",
                (action.context.solver_id,),
            ).fetchone()
            if budget_row is None:
                raise PersistenceConflict("SolverBudget is missing")
            budget = json.loads(budget_row["budget_json"])
            usage = json.loads(budget_row["usage_json"])
            held = self.conn.execute(
                "SELECT COALESCE(SUM(tool_calls),0),COALESCE(SUM(artifacts),0) FROM tool_budget_reservations WHERE solver_id=? AND status='reserved'",
                (action.context.solver_id,),
            ).fetchone()
            if usage.get("tool_calls", 0) + int(held[0]) + tool_calls > budget["max_tool_calls"]:
                raise PersistenceConflict("SolverBudget tool-call limit exceeded")
            if usage.get("artifacts", 0) + int(held[1]) + artifacts > budget["max_artifacts"]:
                raise PersistenceConflict("SolverBudget artifact limit exceeded")
            reservation_id = f"budget_{uuid4().hex}"
            now = utc_now()
            self.conn.execute(
                "INSERT INTO tool_budget_reservations(id,task_id,solver_id,intent_id,action_id,status,tool_calls,artifacts,created_at,updated_at) VALUES (?,?,?,?,?,'reserved',?,?,?,?)",
                (
                    reservation_id, action.context.task_id, action.context.solver_id,
                    action.context.intent_id, action.id, tool_calls, artifacts, now, now,
                ),
            )
            return dict(self.conn.execute(
                "SELECT * FROM tool_budget_reservations WHERE id=?", (reservation_id,)
            ).fetchone())

    def record_runtime_usage(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        solver_id: str,
        intent_id: str | None = None,
        turns: int = 0,
        input_tokens: int = 0,
        output_tokens: int = 0,
        artifact_bytes: int = 0,
        network_requests: int = 0,
    ) -> dict[str, Any]:
        values = {
            "turns": turns,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "artifact_bytes": artifact_bytes,
            "network_requests": network_requests,
        }
        if any(int(value) < 0 for value in values.values()):
            raise ValueError("budget usage values must be non-negative")
        with self.database.transaction():
            existing = self.conn.execute(
                "SELECT * FROM runtime_budget_usage WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
            if existing is not None:
                return dict(existing)
            task_row = self.conn.execute(
                "SELECT payload_json FROM tasks WHERE id=?", (task_id,)
            ).fetchone()
            solver_row = self.conn.execute(
                "SELECT budget_json FROM solver_budgets WHERE solver_id=? AND task_id=?",
                (solver_id, task_id),
            ).fetchone()
            if task_row is None or solver_row is None:
                raise PersistenceConflict("runtime budget owner is missing")
            scopes = [
                (
                    "TaskBudget",
                    json.loads(task_row["payload_json"]).get("execution_budget") or {},
                    "task_id=?",
                    (task_id,),
                ),
                (
                    "SolverBudget",
                    json.loads(solver_row["budget_json"]),
                    "solver_id=?",
                    (solver_id,),
                ),
            ]
            if intent_id:
                intent_row = self.conn.execute(
                    "SELECT payload_json FROM intents WHERE id=? AND task_id=?",
                    (intent_id, task_id),
                ).fetchone()
                if intent_row is None:
                    raise PersistenceConflict("IntentBudget owner is missing")
                scopes.append((
                    "IntentBudget",
                    json.loads(intent_row["payload_json"]).get("budget") or {},
                    "intent_id=?",
                    (intent_id,),
                ))
            for label, budget, where, parameters in scopes:
                used = self.conn.execute(
                    "SELECT COALESCE(SUM(turns),0) AS turns,"
                    "COALESCE(SUM(input_tokens),0) AS input_tokens,"
                    "COALESCE(SUM(output_tokens),0) AS output_tokens,"
                    "COALESCE(SUM(artifact_bytes),0) AS artifact_bytes,"
                    "COALESCE(SUM(network_requests),0) AS network_requests "
                    f"FROM runtime_budget_usage WHERE {where}",
                    parameters,
                ).fetchone()
                self._enforce_runtime_limits(label, budget, used, values)
            now = utc_now()
            self.conn.execute(
                "INSERT INTO runtime_budget_usage(idempotency_key,task_id,solver_id,intent_id,turns,input_tokens,output_tokens,artifact_bytes,network_requests,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    idempotency_key, task_id, solver_id, intent_id, turns,
                    input_tokens, output_tokens, artifact_bytes,
                    network_requests, now,
                ),
            )
            return dict(self.conn.execute(
                "SELECT * FROM runtime_budget_usage WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone())

    def acquire_network_permit(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        solver_id: str,
        intent_id: str | None = None,
        ttl_seconds: float = 120,
    ) -> dict[str, Any]:
        if ttl_seconds <= 0:
            raise ValueError("network permit ttl must be positive")
        with self.database.transaction():
            existing = self.conn.execute(
                "SELECT * FROM network_budget_permits WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
            if existing is not None:
                return dict(existing)
            task_row = self.conn.execute(
                "SELECT payload_json FROM tasks WHERE id=?", (task_id,)
            ).fetchone()
            if task_row is None:
                raise PersistenceConflict("TaskBudget owner is missing")
            budget = json.loads(task_row["payload_json"]).get("execution_budget") or {}
            now = datetime.now(UTC)
            stamp = now.isoformat().replace("+00:00", "Z")
            expires = (now + timedelta(seconds=ttl_seconds)).isoformat().replace(
                "+00:00", "Z"
            )
            self.conn.execute(
                "UPDATE network_budget_permits SET status='expired' "
                "WHERE task_id=? AND status='active' AND expires_at<=?",
                (task_id, stamp),
            )
            active = int(self.conn.execute(
                "SELECT COUNT(*) FROM network_budget_permits "
                "WHERE task_id=? AND status='active' AND expires_at>?",
                (task_id, stamp),
            ).fetchone()[0])
            concurrency = budget.get("max_network_concurrency")
            if concurrency is not None and active >= int(concurrency):
                raise PersistenceConflict("TaskBudget network concurrency limit exceeded")
            window_start = (now - timedelta(minutes=1)).isoformat().replace(
                "+00:00", "Z"
            )
            recent = int(self.conn.execute(
                "SELECT COALESCE(SUM(network_requests),0) FROM runtime_budget_usage "
                "WHERE task_id=? AND created_at>=?",
                (task_id, window_start),
            ).fetchone()[0])
            rate = budget.get("max_network_requests_per_minute")
            if rate is not None and recent >= int(rate):
                raise PersistenceConflict("TaskBudget network request rate limit exceeded")
            self.record_runtime_usage(
                idempotency_key=f"network:{idempotency_key}",
                task_id=task_id,
                solver_id=solver_id,
                intent_id=intent_id,
                network_requests=1,
            )
            self.conn.execute(
                "INSERT INTO network_budget_permits(idempotency_key,task_id,solver_id,intent_id,status,acquired_at,expires_at) "
                "VALUES (?,?,?,?,'active',?,?)",
                (idempotency_key, task_id, solver_id, intent_id, stamp, expires),
            )
            return dict(self.conn.execute(
                "SELECT * FROM network_budget_permits WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone())

    def release_network_permit(self, idempotency_key: str) -> bool:
        cursor = self.conn.execute(
            "UPDATE network_budget_permits SET status='released',released_at=? "
            "WHERE idempotency_key=? AND status='active'",
            (utc_now(), idempotency_key),
        )
        self.database._commit()
        return cursor.rowcount == 1

    @staticmethod
    def _enforce_runtime_limits(
        scope: str, budget: dict[str, Any], used: Any, requested: dict[str, int]
    ) -> None:
        limits = {
            "turns": budget.get("max_turns", budget.get("turns")),
            "input_tokens": budget.get("max_input_tokens"),
            "output_tokens": budget.get("max_output_tokens"),
            "artifact_bytes": budget.get("max_artifact_bytes"),
            "network_requests": budget.get("max_network_requests"),
        }
        total_limit = budget.get("max_total_tokens")
        if total_limit is not None and (
            int(used["input_tokens"])
            + int(used["output_tokens"])
            + requested["input_tokens"]
            + requested["output_tokens"]
            > int(total_limit)
        ):
            raise PersistenceConflict(f"{scope} total-token limit exceeded")
        for name, limit in limits.items():
            if limit is not None and int(used[name]) + requested[name] > int(limit):
                raise PersistenceConflict(
                    f"{scope} {name.replace('_', '-')} limit exceeded"
                )

    def _enforce_task_and_intent_budgets(
        self, action: Any, *, tool_calls: int, artifacts: int
    ) -> None:
        task_row = self.conn.execute(
            "SELECT payload_json FROM tasks WHERE id=?", (action.context.task_id,)
        ).fetchone()
        if task_row is None:
            raise PersistenceConflict("TaskBudget owner is missing")
        task_budget = json.loads(task_row["payload_json"]).get("execution_budget") or {}
        task_used = self.conn.execute(
            "SELECT COALESCE(SUM(tool_calls),0),COALESCE(SUM(artifacts),0) "
            "FROM tool_budget_reservations WHERE task_id=? AND status IN ('reserved','settled')",
            (action.context.task_id,),
        ).fetchone()
        self._enforce_scope_limit(
            "TaskBudget", task_budget, task_used, tool_calls=tool_calls, artifacts=artifacts
        )

        if not action.context.intent_id:
            return
        intent_row = self.conn.execute(
            "SELECT payload_json FROM intents WHERE id=? AND task_id=?",
            (action.context.intent_id, action.context.task_id),
        ).fetchone()
        if intent_row is None:
            raise PersistenceConflict("IntentBudget owner is missing")
        intent_budget = json.loads(intent_row["payload_json"]).get("budget") or {}
        intent_used = self.conn.execute(
            "SELECT COALESCE(SUM(tool_calls),0),COALESCE(SUM(artifacts),0) "
            "FROM tool_budget_reservations WHERE task_id=? AND intent_id=? "
            "AND status IN ('reserved','settled')",
            (action.context.task_id, action.context.intent_id),
        ).fetchone()
        self._enforce_scope_limit(
            "IntentBudget", intent_budget, intent_used,
            tool_calls=tool_calls, artifacts=artifacts,
        )

    @staticmethod
    def _enforce_scope_limit(
        scope: str, budget: dict[str, Any], used: Any, *, tool_calls: int, artifacts: int
    ) -> None:
        tool_limit = budget.get("max_tool_calls", budget.get("tool_calls"))
        artifact_limit = budget.get("max_artifacts", budget.get("artifacts"))
        if tool_limit is not None and int(used[0]) + tool_calls > int(tool_limit):
            raise PersistenceConflict(f"{scope} tool-call limit exceeded")
        if artifact_limit is not None and int(used[1]) + artifacts > int(artifact_limit):
            raise PersistenceConflict(f"{scope} artifact limit exceeded")

    def settle_budget(self, reservation_id: str, *, artifacts: int | None = None) -> dict[str, Any]:
        with self.database.transaction():
            row = self.conn.execute(
                "SELECT * FROM tool_budget_reservations WHERE id=?", (reservation_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"budget reservation not found: {reservation_id}")
            if row["status"] == "settled":
                return dict(row)
            if row["status"] != "reserved":
                raise PersistenceConflict("budget reservation is not active")
            actual_artifacts = max(0, int(artifacts if artifacts is not None else row["artifacts"]))
            artifact_delta = actual_artifacts - int(row["artifacts"])
            budget_row = self.conn.execute(
                "SELECT budget_json,usage_json FROM solver_budgets WHERE solver_id=?",
                (row["solver_id"],),
            ).fetchone()
            budget = json.loads(budget_row["budget_json"])
            usage = json.loads(budget_row["usage_json"])
            if artifact_delta > 0:
                owner = SimpleNamespace(context=SimpleNamespace(
                    task_id=row["task_id"],
                    solver_id=row["solver_id"],
                    intent_id=row["intent_id"],
                ))
                self._enforce_task_and_intent_budgets(
                    owner, tool_calls=0, artifacts=artifact_delta
                )
                held = self.conn.execute(
                    "SELECT COALESCE(SUM(artifacts),0) FROM tool_budget_reservations "
                    "WHERE solver_id=? AND status='reserved'",
                    (row["solver_id"],),
                ).fetchone()
                if usage.get("artifacts", 0) + int(held[0]) + artifact_delta > budget["max_artifacts"]:
                    raise PersistenceConflict("SolverBudget artifact limit exceeded")
            usage["tool_calls"] = usage.get("tool_calls", 0) + int(row["tool_calls"])
            usage["artifacts"] = usage.get("artifacts", 0) + actual_artifacts
            now = utc_now()
            self.conn.execute(
                "UPDATE solver_budgets SET usage_json=?,version=version+1,updated_at=? WHERE solver_id=?",
                (json.dumps(usage), now, row["solver_id"]),
            )
            self.conn.execute(
                "UPDATE tool_budget_reservations SET status='settled',artifacts=?,version=version+1,updated_at=? WHERE id=? AND status='reserved'",
                (actual_artifacts, now, reservation_id),
            )
            return dict(self.conn.execute(
                "SELECT * FROM tool_budget_reservations WHERE id=?", (reservation_id,)
            ).fetchone())

    def release_budget(self, reservation_id: str) -> None:
        cursor = self.conn.execute(
            "UPDATE tool_budget_reservations SET status='released',version=version+1,updated_at=? WHERE id=? AND status='reserved'",
            (utc_now(), reservation_id),
        )
        if cursor.rowcount not in {0, 1}:
            raise PersistenceConflict("budget release failed")
        self.database._commit()

    def save_approval(self, approval: Any) -> None:
        try:
            self.conn.execute(
                "INSERT INTO approvals(id,task_id,solver_id,intent_id,action_id,status,payload_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?, ?,?)",
                (
                    approval.id, approval.task_id, approval.solver_id,
                    approval.intent_id, approval.action_id, approval.status,
                    approval.model_dump_json(), approval.created_at, approval.updated_at,
                ),
            )
        except Exception as exc:
            raise PersistenceConflict(f"approval already exists: {approval.id}") from exc
        self.database._commit()

    def get_approval_for_action(self, action_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM approvals WHERE action_id=? ORDER BY created_at DESC LIMIT 1",
            (action_id,),
        ).fetchone()
        if row is None:
            return None
        return {**dict(row), "payload": json.loads(row["payload_json"])}

    def decide_approval(self, action_id: str, status: str, *, expected_status: str = "pending") -> None:
        row = self.conn.execute(
            "SELECT id,payload_json,version,status FROM approvals WHERE action_id=? ORDER BY created_at DESC LIMIT 1",
            (action_id,),
        ).fetchone()
        if row is None or row["status"] != expected_status:
            raise PersistenceConflict(f"approval decision rejected for action: {action_id}")
        payload = json.loads(row["payload_json"])
        now = utc_now()
        payload["status"] = status
        payload["updated_at"] = now
        cursor = self.conn.execute(
            "UPDATE approvals SET status=?,version=version+1,payload_json=?,updated_at=? WHERE id=? AND version=? AND status=?",
            (
                status, json.dumps(payload, ensure_ascii=False, sort_keys=True), now,
                row["id"], int(row["version"]), expected_status,
            ),
        )
        if cursor.rowcount != 1:
            raise PersistenceConflict(f"approval changed concurrently: {action_id}")
        self.database._commit()

    def legacy_approval_expiry(self, action_id: str) -> str | None:
        row = self.conn.execute(
            "SELECT approval_expires_at FROM actions WHERE id=?", (action_id,)
        ).fetchone()
        if row is None:
            return None
        return str(row["approval_expires_at"] or "") or None


__all__ = ["ACTION_TRANSITIONS", "SqliteToolGovernanceRepository"]
