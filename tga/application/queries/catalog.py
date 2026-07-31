"""Bounded read-only catalogs for product configuration and global indexes."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from tga.infrastructure.skills.catalog import FileSkillCatalog
from tga.infrastructure.solver_definitions.registry import SolverDefinitionRegistry
from tga.infrastructure.team_templates.registry import TeamTemplateRegistry


class CatalogQueries:
    _MAX_DATABASES = 200
    _MAX_ROWS_PER_DATABASE = 200

    def __init__(self, *, run_root: str | Path) -> None:
        self.run_root = Path(run_root)

    def catalog(
        self, kind: str, *, query: str = "", offset: int = 0, limit: int = 100
    ) -> dict[str, Any]:
        builders = {
            "teams": self._teams,
            "solvers": self._solvers,
            "skills": self._skills,
            "policies": self._policies,
            "resources": self._resources,
            "reports": self._reports,
            "knowledge-bases": self._knowledge_bases,
        }
        builder = builders.get(kind)
        if builder is None:
            raise KeyError(kind)
        errors: list[dict[str, str]] = []
        try:
            items = builder(errors)
        except (OSError, ValueError) as exc:
            return {
                "schema_version": 1, "kind": kind, "supported": False,
                "reason": f"catalog source is unavailable: {type(exc).__name__}",
                "items": [], "errors": [], "offset": offset, "limit": limit,
                "total": 0, "next_offset": None,
            }
        needle = query.strip().casefold()
        if needle:
            items = [item for item in items if needle in json.dumps(item, ensure_ascii=False).casefold()]
        bounded_limit = max(1, min(limit, 200))
        bounded_offset = max(0, offset)
        page = items[bounded_offset:bounded_offset + bounded_limit]
        next_offset = bounded_offset + len(page)
        return {
            "schema_version": 1, "kind": kind, "supported": True,
            "reason": None, "items": page, "errors": errors[:50],
            "offset": bounded_offset, "limit": bounded_limit,
            "total": len(items),
            "next_offset": next_offset if next_offset < len(items) else None,
        }

    @staticmethod
    def _teams(_errors):
        definitions = SolverDefinitionRegistry.builtin()
        return [item.model_dump(mode="json") for item in TeamTemplateRegistry.builtin(definitions=definitions).all()]

    @staticmethod
    def _solvers(_errors):
        return [item.model_dump(mode="json") for item in SolverDefinitionRegistry.builtin().all()]

    @staticmethod
    def _skills(_errors):
        return [item.model_dump(mode="json") for item in FileSkillCatalog.builtin().all()]

    @staticmethod
    def _policies(_errors):
        return [{"id": "task-execution-policy", "type": "execution", "status": "available", "source": "Task creation contract", "editable": False}]

    def _resources(self, errors):
        items: list[dict[str, Any]] = []
        for db_path in self._databases():
            task_id = db_path.parent.name
            connection = self._open_catalog_database(db_path, task_id, errors)
            if connection is None:
                continue
            try:
                for table, kind, title_column, require_v6 in (
                    ("artifacts", "artifacts", "kind", True),
                    ("evidence_claims", "evidence", "statement", False),
                    ("findings", "findings", "title", True),
                    ("knowledge_items", "knowledge", "subject", False),
                ):
                    if not self._has_table(connection, table):
                        continue
                    columns = self._columns(connection, table)
                    required = {"id", "task_id", "payload_json"}
                    if require_v6:
                        required.add("schema_version")
                    if not required <= columns:
                        self._database_error(errors, task_id, f"{table} has an incompatible schema")
                        continue
                    schema_filter = " AND schema_version=6" if require_v6 else ""
                    rows = connection.execute(
                        f"SELECT id,payload_json FROM {table} WHERE task_id=?{schema_filter} "
                        "ORDER BY id LIMIT ?",
                        (task_id, self._MAX_ROWS_PER_DATABASE),
                    ).fetchall()
                    for row in rows:
                        payload = self._record_payload(row, task_id, errors)
                        if payload is None:
                            continue
                        items.append({
                            "id": row["id"], "task_id": task_id, "kind": kind,
                            "title": str(payload.get(title_column) or row["id"]),
                            "status": payload.get("status"), "raw": payload,
                        })
            finally:
                connection.close()
        return items

    def _reports(self, errors):
        items = []
        for db_path in self._databases():
            task_id = db_path.parent.name
            connection = self._open_catalog_database(db_path, task_id, errors)
            if connection is None:
                continue
            connection.close()
            report = db_path.parent / "reports" / "report.md"
            if report.is_file():
                items.append({"id": f"report:{task_id}", "task_id": task_id, "status": "exported", "title": report.name, "updated_at": report.stat().st_mtime})
        return items

    def _knowledge_bases(self, errors):
        items_by_id: dict[str, dict[str, Any]] = {}
        for db_path in self._databases():
            task_id = db_path.parent.name
            connection = self._open_catalog_database(db_path, task_id, errors)
            if connection is None:
                continue
            try:
                if self._has_table(connection, "knowledge_bases"):
                    columns = self._columns(connection, "knowledge_bases")
                    if not {"id", "payload_json", "created_at"} <= columns:
                        self._database_error(errors, task_id, "knowledge_bases has an incompatible schema")
                        continue
                    rows = connection.execute(
                        "SELECT id,payload_json FROM knowledge_bases ORDER BY created_at,id LIMIT ?",
                        (self._MAX_ROWS_PER_DATABASE,),
                    ).fetchall()
                    for row in rows:
                        payload = self._record_payload(row, task_id, errors)
                        if payload is not None:
                            items_by_id.setdefault(str(row["id"]), payload)
            finally:
                connection.close()
        return list(items_by_id.values())

    def _databases(self):
        if not self.run_root.is_dir():
            return []
        paths = [path / "evidence.db" for path in self.run_root.iterdir() if path.is_dir() and not path.name.startswith(".") and (path / "evidence.db").is_file()]
        return sorted(paths, key=lambda path: path.stat().st_mtime, reverse=True)[:self._MAX_DATABASES]

    @staticmethod
    def _open(path: Path):
        connection = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        return connection

    @staticmethod
    def _has_table(connection, name: str) -> bool:
        return connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None

    @staticmethod
    def _columns(connection, table: str) -> set[str]:
        return {str(row["name"]) for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}

    def _open_catalog_database(
        self, path: Path, task_id: str, errors: list[dict[str, str]]
    ) -> sqlite3.Connection | None:
        try:
            connection = self._open(path)
            if not self._has_table(connection, "tasks"):
                raise sqlite3.DatabaseError("tasks table is missing")
            row = connection.execute(
                "SELECT payload_json FROM tasks WHERE id=?", (task_id,)
            ).fetchone()
            if row is None:
                raise sqlite3.DatabaseError("task record is missing")
            payload = json.loads(row["payload_json"])
            if not isinstance(payload, dict):
                raise ValueError("task payload is not an object")
            if int(payload.get("schema_version") or 0) != 6:
                connection.close()
                return None
            return connection
        except (OSError, sqlite3.Error, json.JSONDecodeError, TypeError, ValueError):
            self._database_error(errors, task_id, "database is not a readable schema-v6 task store")
            try:
                connection.close()
            except UnboundLocalError:
                pass
            return None

    @staticmethod
    def _record_payload(row, task_id: str, errors: list[dict[str, str]]) -> dict[str, Any] | None:
        try:
            payload = json.loads(row["payload_json"])
            if not isinstance(payload, dict):
                raise ValueError
            return payload
        except (json.JSONDecodeError, TypeError, ValueError):
            errors.append({
                "code": "CATALOG_RECORD_INVALID", "task_id": task_id,
                "message": f"record {row['id']} contains invalid JSON",
            })
            return None

    @staticmethod
    def _database_error(errors: list[dict[str, str]], task_id: str, message: str) -> None:
        errors.append({
            "code": "CATALOG_DATABASE_INVALID", "task_id": task_id,
            "message": message,
        })


__all__ = ["CatalogQueries"]
