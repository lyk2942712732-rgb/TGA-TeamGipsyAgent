"""Read-only operational queries for Dashboard and global approvals."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from tga.application.projections.models import DashboardResponse, GlobalApprovalPage
from tga.contracts import TGATask
from tga.models.bootstrap import model_config_status


ACTIVE_STATUSES = {
    "created", "queued", "ready", "running", "waiting", "paused",
    "awaiting_approval", "awaiting_input", "awaiting_user_input", "blocked",
}
SUPPORTED_APPROVAL_STATUSES = {"pending", "approved", "rejected", "expired"}


class OperationalQueries:
    """Scan isolated task databases with one bounded read query per database."""

    def __init__(
        self, *, run_root: str | Path,
        model_status: Callable[[], dict[str, Any]] = model_config_status,
    ) -> None:
        self.run_root = Path(run_root)
        self._model_status = model_status

    def dashboard(self) -> DashboardResponse:
        tasks: list[dict[str, Any]] = []
        attention: list[dict[str, Any]] = []
        readable = 0
        database_errors = 0
        for db_path in self._task_databases():
            try:
                rows = self._dashboard_rows(db_path)
                if not rows:
                    continue
                if _task(str(rows[0]["task_payload"])).schema_version != 6:
                    continue
                readable += 1
                summary = _dashboard_task(rows[0])
                tasks.append(summary)
                for row in rows:
                    approval = _dashboard_approval_attention(row, summary)
                    if approval is not None:
                        attention.append(approval)
                if summary["status"] in {"awaiting_input", "awaiting_user_input"}:
                    attention.append(_task_attention(summary, "user_input"))
                elif summary["status"] == "blocked":
                    attention.append(_task_attention(summary, "blocked"))
            except (OSError, sqlite3.Error, ValueError, KeyError):
                # A payload that does not validate as the current task contract is
                # unreadable operational data, not a reason to revive old-schema
                # projection logic in the live service.
                database_errors += 1
                continue

        tasks.sort(key=lambda item: item["updated_at"], reverse=True)
        attention.sort(key=lambda item: item["updated_at"], reverse=True)
        metrics: dict[str, int | None] = {
            "running_tasks": sum(item["status"] == "running" for item in tasks),
            "pending_approvals": sum(item["pending_approvals"] for item in tasks),
            "awaiting_user_input": sum(
                item["status"] in {"awaiting_input", "awaiting_user_input"}
                for item in tasks
            ),
            "blocked_tasks": sum(item["status"] == "blocked" for item in tasks),
            "active_solvers": sum(item["active_solvers"] for item in tasks),
        }
        active = [item for item in tasks if item["status"] in ACTIVE_STATUSES][:8]
        completed = [item for item in tasks if item["status"] == "completed"][:6]
        model = self._safe_model_status()
        storage_available = self.run_root.exists() and self.run_root.is_dir()
        statuses = [
            _system_status("api", "API", "healthy", "聚合查询可用", True),
            _system_status(
                "model", "Model Provider",
                "available" if model.get("configured") else "unavailable",
                "已配置并可供任务使用" if model.get("configured") else "尚未配置模型",
                bool(model.get("configured")),
            ),
            _system_status(
                "task_storage", "Task Storage",
                "available" if storage_available else "unavailable",
                f"已读取 {readable} 个任务数据库" if storage_available else "任务存储目录不可用",
                storage_available,
            ),
            _system_status(
                "sqlite", "SQLite",
                "healthy" if database_errors == 0 else "degraded",
                "只读检查通过" if database_errors == 0 else f"{database_errors} 个任务数据库不可读",
                database_errors == 0,
            ),
            _system_status("scheduler", "Scheduler", "unavailable", "暂不可用：没有全局只读健康契约", False),
            _system_status("mcp", "MCP", "unavailable", "暂不可用：未在 Dashboard 聚合中探测", False),
        ]
        return DashboardResponse.model_validate({
            "schema_version": 1,
            "generated_at": _utc_now(),
            "metrics": metrics,
            "needs_attention": attention[:12],
            "active_tasks": active,
            "recent_completed": completed,
            "system_status": statuses,
            "unavailable_metrics": [],
        })

    def approvals(
        self, *, status: str | None = None, task_id: str | None = None,
        solver_id: str | None = None, intent_id: str | None = None,
        risk: str | None = None, capability: str | None = None,
        deadline: str | None = None, offset: int = 0, limit: int = 50,
    ) -> GlobalApprovalPage:
        values: list[dict[str, Any]] = []
        for db_path in self._task_databases():
            if task_id and db_path.parent.name != task_id:
                continue
            try:
                rows = self._approval_rows(
                    db_path, status=status, solver_id=solver_id,
                    intent_id=intent_id, risk=risk, capability=capability,
                )
                if rows and _task(str(rows[0]["task_payload"])).schema_version != 6:
                    continue
                values.extend(_global_approval(row) for row in rows)
            except (OSError, sqlite3.Error, ValueError, KeyError):
                continue

        now = datetime.now(UTC)
        if deadline:
            values = [item for item in values if _deadline_matches(item["expires_at"], deadline, now)]
        values.sort(key=lambda item: (item["updated_at"], item["approval_id"]), reverse=True)
        bounded_offset = max(0, int(offset))
        bounded_limit = max(1, min(int(limit), 200))
        selected = values[bounded_offset:bounded_offset + bounded_limit]
        return GlobalApprovalPage.model_validate({
            "schema_version": 1,
            "offset": bounded_offset,
            "limit": bounded_limit,
            "total": len(values),
            "next_offset": (
                bounded_offset + len(selected)
                if bounded_offset + len(selected) < len(values) else None
            ),
            "items": selected,
            "filters": {
                "status": status, "task_id": task_id, "solver_id": solver_id,
                "intent_id": intent_id, "risk": risk, "capability": capability,
                "deadline": deadline,
            },
        })

    def _task_databases(self) -> list[Path]:
        if not self.run_root.is_dir():
            return []
        return [
            child / "evidence.db"
            for child in self.run_root.iterdir()
            if child.is_dir() and not child.name.startswith(".")
            and (child / "evidence.db").is_file()
        ]

    @staticmethod
    def _open(db_path: Path) -> sqlite3.Connection:
        connection = sqlite3.connect(
            f"file:{db_path.resolve().as_posix()}?mode=ro", uri=True
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        return connection

    def _dashboard_rows(self, db_path: Path) -> list[sqlite3.Row]:
        connection = self._open(db_path)
        try:
            tables = _tables(connection)
            if not {"tasks", "sessions"}.issubset(tables):
                return []
            approval_join = ""
            approval_columns = "NULL approval_id,NULL approval_status,NULL approval_payload,NULL action_id,NULL action_risk,NULL action_capability,NULL action_updated_at"
            if {"approvals", "governed_actions"}.issubset(tables):
                approval_join = (
                    "LEFT JOIN approvals p ON p.task_id=t.id AND p.status='pending' "
                    "LEFT JOIN governed_actions a ON a.id=p.action_id "
                )
                approval_columns = (
                    "p.id approval_id,p.status approval_status,p.payload_json approval_payload,"
                    "a.id action_id,json_extract(a.payload_json,'$.risk') action_risk,"
                    "a.capability action_capability,a.updated_at action_updated_at"
                )
            count = lambda table, where="": (
                f"(SELECT COUNT(*) FROM {table} x WHERE x.task_id=t.id{where})"
                if table in tables else "0"
            )
            active_solver_count = count(
                "solver_instances",
                " AND x.status IN ('created','queued','ready','running','waiting','awaiting_approval')",
            )
            event_columns = (
                "(SELECT seq FROM agent_events e WHERE e.task_id=t.id ORDER BY seq DESC LIMIT 1) latest_seq,"
                "(SELECT type FROM agent_events e WHERE e.task_id=t.id ORDER BY seq DESC LIMIT 1) latest_type,"
                "(SELECT created_at FROM agent_events e WHERE e.task_id=t.id ORDER BY seq DESC LIMIT 1) latest_event_at"
                if "agent_events" in tables else
                "NULL latest_seq,NULL latest_type,NULL latest_event_at"
            )
            sql = f"""
                SELECT t.id task_id,t.payload_json task_payload,t.created_at task_created_at,
                    CASE
                        WHEN s.status='awaiting_approval' THEN s.status
                        ELSE COALESCE(os.status,s.status)
                    END status,s.turn_count,s.max_turns,
                    {active_solver_count} active_solvers,
                    {count('approvals', " AND x.status='pending'")} pending_approvals,
                    {count('intents')} intent_total,
                    {count('intents', " AND x.status='completed'")} intent_completed,
                    {count('findings', " AND x.status='confirmed'")} findings,
                    {count('artifacts')} artifacts,
                    {event_columns},{approval_columns}
                FROM tasks t JOIN sessions s ON s.task_id=t.id
                {"LEFT JOIN task_orchestrator_states os ON os.task_id=t.id" if "task_orchestrator_states" in tables else "LEFT JOIN sessions os ON 1=0"}
                {approval_join}
                ORDER BY COALESCE(action_updated_at,latest_event_at,t.created_at) DESC
            """
            return connection.execute(sql).fetchall()
        finally:
            connection.close()

    def _approval_rows(
        self, db_path: Path, *, status: str | None, solver_id: str | None,
        intent_id: str | None, risk: str | None, capability: str | None,
    ) -> list[sqlite3.Row]:
        connection = self._open(db_path)
        try:
            if not {"tasks", "approvals", "governed_actions"}.issubset(_tables(connection)):
                return []
            clauses = ["p.status IN ('pending','approved','rejected','expired')"]
            parameters: list[Any] = []
            for column, value in (
                ("p.status", status), ("p.solver_id", solver_id),
                ("p.intent_id", intent_id),
                ("json_extract(a.payload_json,'$.risk')", risk),
                ("a.capability", capability),
            ):
                if value:
                    clauses.append(f"{column}=?")
                    parameters.append(value)
            return connection.execute(f"""
                SELECT p.id approval_id,p.task_id,p.solver_id,p.intent_id,p.action_id,
                    p.status approval_status,p.payload_json approval_payload,
                    p.created_at approval_created_at,p.updated_at approval_updated_at,
                    t.payload_json task_payload,a.tool_class action_kind,a.capability,
                    json_extract(a.payload_json,'$.resolved_target') target,
                    json_extract(a.payload_json,'$.risk') risk,
                    json_extract(a.payload_json,'$.effect') effect_json,
                    json_extract(a.payload_json,'$.rationale') rationale,
                    json_extract(a.payload_json,'$.expected_outcome') expected_outcome,
                    json_extract(a.payload_json,'$.alternative_analysis') alternative_analysis,
                    NULL approval_expires_at,a.created_at action_created_at,
                    a.updated_at action_updated_at
                FROM approvals p JOIN governed_actions a ON a.id=p.action_id
                JOIN tasks t ON t.id=p.task_id
                WHERE {' AND '.join(clauses)}
                ORDER BY p.updated_at DESC,p.id DESC
            """, parameters).fetchall()
        finally:
            connection.close()

    def _safe_model_status(self) -> dict[str, Any]:
        try:
            return self._model_status()
        except Exception:
            return {"configured": False}


def _tables(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0]) for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }


def _task(payload: str) -> TGATask:
    return TGATask.model_validate_json(payload)


def _dashboard_task(row: sqlite3.Row) -> dict[str, Any]:
    task = _task(str(row["task_payload"]))
    updated_at = str(row["latest_event_at"] or row["task_created_at"] or "")
    return {
        "task_id": task.id, "name": task.name or task.id, "mode": str(task.mode),
        "status": str(row["status"]), "updated_at": updated_at,
        "active_solvers": int(row["active_solvers"] or 0),
        "pending_approvals": int(row["pending_approvals"] or 0),
        "intent_total": int(row["intent_total"] or 0),
        "intent_completed": int(row["intent_completed"] or 0),
        "findings": int(row["findings"] or 0),
        "artifacts": int(row["artifacts"] or 0),
        "turn_count": int(row["turn_count"] or 0),
        "max_turns": int(row["max_turns"] or 0),
        "needs_attention": str(row["status"]) in {
            "awaiting_approval", "awaiting_input", "awaiting_user_input", "blocked",
        } or int(row["pending_approvals"] or 0) > 0,
        "latest_event": ({
            "seq": int(row["latest_seq"]), "type": str(row["latest_type"]),
            "created_at": str(row["latest_event_at"]),
        } if row["latest_seq"] is not None else None),
    }


def _dashboard_approval_attention(
    row: sqlite3.Row, task: dict[str, Any]
) -> dict[str, Any] | None:
    if row["approval_id"] is None:
        return None
    payload = json.loads(str(row["approval_payload"] or "{}"))
    capability = str(row["action_capability"] or "受控操作")
    return {
        "id": str(row["approval_id"]), "kind": "approval",
        "task_id": task["task_id"], "task_name": task["name"],
        "title": f"审批 {capability}",
        "description": str(payload.get("reason") or "高影响操作等待决策"),
        "status": "pending", "risk": str(row["action_risk"] or payload.get("risk") or "passive"),
        "action_id": str(row["action_id"]),
        "updated_at": str(row["action_updated_at"] or task["updated_at"]),
    }


def _task_attention(task: dict[str, Any], kind: str) -> dict[str, Any]:
    user_input = kind == "user_input"
    return {
        "id": f"{kind}:{task['task_id']}", "kind": kind,
        "task_id": task["task_id"], "task_name": task["name"],
        "title": "等待用户输入" if user_input else "任务已阻塞",
        "description": "任务需要用户提供输入后继续" if user_input else "打开任务详情查看阻塞原因",
        "status": task["status"], "risk": None, "action_id": None,
        "updated_at": task["updated_at"],
    }


def _global_approval(row: sqlite3.Row) -> dict[str, Any]:
    task = _task(str(row["task_payload"]))
    payload = json.loads(str(row["approval_payload"] or "{}"))
    effect = json.loads(str(row["effect_json"] or "{}"))
    expires_at = str(payload.get("expires_at") or row["approval_expires_at"] or "") or None
    status = str(row["approval_status"])
    expired_now = bool(expires_at and _parse_time(expires_at) <= datetime.now(UTC))
    decision_allowed = status == "pending" and not expired_now
    return {
        "approval_id": str(row["approval_id"]), "task_id": task.id,
        "task_name": task.name or task.id, "solver_id": str(row["solver_id"] or ""),
        "intent_id": str(row["intent_id"]) if row["intent_id"] else None,
        "action_id": str(row["action_id"]), "action_kind": str(row["action_kind"]),
        "capability": str(row["capability"]), "target": str(row["target"]),
        "risk": str(row["risk"] or payload.get("risk") or "passive"),
        "effect": effect, "rationale": str(row["rationale"] or payload.get("reason") or ""),
        "expected_outcome": str(row["expected_outcome"] or ""),
        "alternative_analysis": str(row["alternative_analysis"] or ""),
        "alternatives": [str(item) for item in payload.get("alternatives", [])],
        "reversibility": str(effect.get("reversibility") or "not_applicable"),
        "expires_at": expires_at, "status": status,
        "decision_allowed": decision_allowed,
        "decision_block_reason": (
            "审批已过期" if status == "pending" and expired_now
            else "审批已完成" if status != "pending" else None
        ),
        "created_at": str(row["approval_created_at"] or row["action_created_at"] or ""),
        "updated_at": str(row["approval_updated_at"] or row["action_updated_at"] or ""),
    }


def _deadline_matches(value: str | None, deadline: str, now: datetime) -> bool:
    if deadline == "none":
        return not value
    if not value:
        return False
    parsed = _parse_time(value)
    if deadline == "overdue":
        return parsed <= now
    horizon = now + (timedelta(hours=24) if deadline == "24h" else timedelta(days=7))
    return now < parsed <= horizon


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _system_status(
    id_: str, label: str, status: str, detail: str, available: bool
) -> dict[str, Any]:
    return {"id": id_, "label": label, "status": status, "detail": detail, "available": available}


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


__all__ = ["OperationalQueries"]
