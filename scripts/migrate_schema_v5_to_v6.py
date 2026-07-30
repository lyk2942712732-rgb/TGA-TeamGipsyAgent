"""Explicit, offline, backup-first migration from task schema 5 to schema 6."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from tga.domain.task.models import TGATask
from tga.domain.task.spec import TaskSpec
from tga.infrastructure.persistence import PersistenceBundle


SOURCE_SCHEMA = 5
TARGET_SCHEMA = 6


class MigrationError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str) -> MigrationError:
    return MigrationError(code, message)


def _read_task(path: Path) -> TGATask:
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        rows = connection.execute("SELECT payload_json FROM tasks").fetchall()
    except sqlite3.Error as exc:
        raise _fail("INVALID_DATABASE", "database has no readable tasks table") from exc
    finally:
        connection.close()
    if len(rows) != 1:
        raise _fail("INVALID_TASK_COUNT", "migration requires exactly one task per database")
    try:
        return TGATask.model_validate_json(rows[0][0])
    except Exception as exc:
        raise _fail("INVALID_TASK", "task payload failed validation") from exc


def _legacy_counts(path: Path) -> dict[str, int]:
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        result: dict[str, int] = {}
        for name in ("memory_entries", "strategy_cards", "artifacts", "findings", "agent_events"):
            result[name] = int(connection.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]) if name in tables else 0
        return result
    finally:
        connection.close()


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def migrate_database(
    db_path: str | Path,
    *,
    apply: bool = False,
    report_path: str | Path | None = None,
    fault_hook: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Plan or migrate one stopped task database; dry-run is the default."""

    database = Path(db_path).resolve()
    if not database.is_file():
        raise _fail("DATABASE_NOT_FOUND", "database file does not exist")
    task = _read_task(database)
    report = (
        Path(report_path).resolve()
        if report_path is not None
        else database.with_name(f"{database.name}.migration-report.json")
    )
    counts = _legacy_counts(database)
    if task.schema_version == TARGET_SCHEMA:
        result = {
            "database": str(database), "backup": None, "task_id": task.id,
            "source_schema": TARGET_SCHEMA, "target_schema": TARGET_SCHEMA,
            "dry_run": not apply, "would_migrate": False, "migrated": False,
            "task_json_backup": None, "report": str(report), "status": "no_op",
            "legacy_counts": counts,
        }
        preserve_applied_report = False
        if report.is_file():
            try:
                previous = json.loads(report.read_text(encoding="utf-8"))
                preserve_applied_report = (
                    previous.get("status") == "applied"
                    and previous.get("task_id") == task.id
                    and previous.get("target_schema") == TARGET_SCHEMA
                )
            except (OSError, json.JSONDecodeError):
                preserve_applied_report = False
        if not preserve_applied_report:
            _write_json_atomic(report, result)
        return result
    if task.schema_version != SOURCE_SCHEMA:
        raise _fail(
            "UNSUPPORTED_SOURCE_SCHEMA",
            f"expected schema {SOURCE_SCHEMA}, found schema {task.schema_version}",
        )

    planned = {
        "database": str(database), "backup": None, "task_id": task.id,
        "source_schema": SOURCE_SCHEMA, "target_schema": TARGET_SCHEMA,
        "dry_run": not apply, "would_migrate": True, "migrated": False,
        "task_json_backup": None, "report": str(report),
        "status": "planned", "legacy_counts": counts,
        "evidence_policy": "legacy evidence and findings remain candidate/legacy; confirmation is never inferred",
        "rollback": "source is atomically replaced only after validation; backups are retained",
    }
    if not apply:
        _write_json_atomic(report, planned)
        return planned

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    backup = database.with_name(
        f"{database.name}.v5-backup-{stamp}-{uuid4().hex[:8]}"
    )
    task_json_backup = database.with_name(
        f"{database.name}.task-json-v5-backup-{stamp}-{uuid4().hex[:8]}.json"
    )
    temporary = database.with_name(f".{database.name}.schema-v6-{uuid4().hex}.tmp")
    published = False
    try:
        # Migration is documented as offline. A byte-for-byte copy gives the
        # operator a simple, independently verifiable rollback artifact.
        shutil.copy2(database, backup)
        _write_json_atomic(task_json_backup, task.model_dump(mode="json"))
        if fault_hook:
            fault_hook("after_backup")
        shutil.copy2(database, temporary)
        bundle = PersistenceBundle.open(temporary)
        try:
            migrated_task = task.model_copy(update={"schema_version": TARGET_SCHEMA})
            with bundle.transaction():
                bundle.tasks.update_task(migrated_task)
                bundle.database.conn.execute(
                    "UPDATE sessions SET schema_version=? WHERE task_id=?",
                    (TARGET_SCHEMA, task.id),
                )
                bundle.tasks.save_task_spec(
                    TaskSpec(
                        task_id=task.id,
                        objective=task.goal,
                        resources=[],
                        legacy_import=True,
                        provenance={
                            "legacy_schema": SOURCE_SCHEMA,
                            "formal_directives_inferred": False,
                            "legacy_session_input_preserved_on_task": True,
                        },
                    )
                )
                bundle.events.append_agent_event(
                    task.id,
                    "SCHEMA_MIGRATED",
                    {
                        "source_schema": SOURCE_SCHEMA,
                        "target_schema": TARGET_SCHEMA,
                        "legacy_runtime_writes_disabled": True,
                    },
                )
            check = bundle.database.conn.execute("PRAGMA integrity_check").fetchone()[0]
            if check != "ok":
                raise _fail("MIGRATED_DB_INVALID", "integrity check failed")
        finally:
            bundle.close()
        if fault_hook:
            fault_hook("before_publish")
        validated = _read_task(temporary)
        if validated.schema_version != TARGET_SCHEMA:
            raise _fail("MIGRATED_DB_INVALID", "task did not reach schema 6")
        os.replace(temporary, database)
        published = True
        if fault_hook:
            fault_hook("after_publish")
    except Exception:
        temporary.unlink(missing_ok=True)
        if published and backup.is_file():
            shutil.copy2(backup, database)
        failure_report = {
            **planned,
            "dry_run": False,
            "status": "failed_rolled_back",
            "backup": str(backup) if backup.is_file() else None,
            "task_json_backup": str(task_json_backup) if task_json_backup.is_file() else None,
        }
        try:
            _write_json_atomic(report, failure_report)
        except OSError:
            pass
        raise

    result = {
        "database": str(database), "backup": str(backup), "task_id": task.id,
        "source_schema": SOURCE_SCHEMA, "target_schema": TARGET_SCHEMA,
        "dry_run": False, "would_migrate": True, "migrated": True,
        "task_json_backup": str(task_json_backup), "report": str(report),
        "status": "applied", "legacy_counts": counts,
        "evidence_policy": planned["evidence_policy"],
        "rollback": planned["rollback"],
    }
    _write_json_atomic(report, result)
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Explicitly migrate one offline TGA evidence.db from schema 5 to schema 6."
    )
    parser.add_argument("--db", required=True, type=Path, help="path to evidence.db")
    parser.add_argument(
        "--apply", action="store_true",
        help="publish the migration; without this flag the command is a dry-run",
    )
    parser.add_argument(
        "--report", type=Path,
        help="migration report path (default: <db>.migration-report.json)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = migrate_database(args.db, apply=args.apply, report_path=args.report)
    except MigrationError as exc:
        print(f"migration failed [{exc.code}]: {exc}", file=sys.stderr)
        return 1
    except Exception:
        print("migration failed [INTERNAL_ERROR]: source database was not replaced", file=sys.stderr)
        return 1
    if result["dry_run"] and result["would_migrate"]:
        print(
            f"DRY RUN: task={result['task_id']} schema=5->6 "
            f"report={result['report']} (rerun with --apply to publish)"
        )
    elif result["migrated"]:
        print(
            f"migration complete: task={result['task_id']} schema=5->6 "
            f"backup={result['backup']} task_json_backup={result['task_json_backup']} "
            f"report={result['report']}"
        )
    else:
        print(f"migration skipped: task={result['task_id']} already schema 6")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
