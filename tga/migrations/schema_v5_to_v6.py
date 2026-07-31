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

from tga.domain.task.models import TGATask
from tga.domain.task.spec import TaskSpec
from tga.migrations.skill_bundles import legacy_skill_bundle_to_task_common
from tga.infrastructure.persistence import PersistenceBundle
from tga.skills.models import SkillBundleSnapshot


SOURCE_SCHEMA = 5
TARGET_SCHEMA = 6
LEGACY_RUNTIME_TABLES = (
    "solvers", "memory_entries", "actions", "strategy_cards", "events",
    "action_results",
)


class MigrationError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str) -> MigrationError:
    return MigrationError(code, message)


def _read_task_payload(path: Path) -> dict[str, Any]:
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
        payload = json.loads(rows[0][0])
        if not isinstance(payload, dict):
            raise ValueError("task payload must be an object")
        return payload
    except Exception as exc:
        raise _fail("INVALID_TASK", "task payload failed validation") from exc


def _read_task(path: Path) -> TGATask:
    payload = _read_task_payload(path)
    if int(payload.get("schema_version") or 0) == SOURCE_SCHEMA:
        payload.pop("skill_bundle_snapshot", None)
    try:
        return TGATask.model_validate(payload)
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
        for name in (*LEGACY_RUNTIME_TABLES, "artifacts", "findings", "agent_events"):
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


def _archive_legacy_runtime(path: Path, destination: Path) -> None:
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        tables = {
            str(row[0]) for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        archived = {
            name: [dict(row) for row in connection.execute(f"SELECT * FROM {name}")]
            if name in tables else []
            for name in LEGACY_RUNTIME_TABLES
        }
    finally:
        connection.close()
    _write_json_atomic(destination, {
        "source_schema": SOURCE_SCHEMA,
        "tables": archived,
        "counts": {name: len(rows) for name, rows in archived.items()},
    })


def _mark_temporary_database_v6(path: Path, task: TGATask) -> None:
    migrated = task.model_copy(update={"schema_version": TARGET_SCHEMA})
    connection = sqlite3.connect(path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "UPDATE tasks SET payload_json=? WHERE id=?",
            (migrated.model_dump_json(), migrated.id),
        )
        connection.execute(
            "UPDATE sessions SET schema_version=? WHERE task_id=?",
            (TARGET_SCHEMA, migrated.id),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _prepare_single_file_publication(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.execute("PRAGMA journal_mode=DELETE").fetchone()
    finally:
        connection.close()


def _copy_database(source: Path, destination: Path) -> None:
    source_connection = sqlite3.connect(
        f"file:{source.as_posix()}?mode=ro", uri=True
    )
    destination_connection = sqlite3.connect(destination)
    try:
        source_connection.backup(destination_connection)
    finally:
        destination_connection.close()
        source_connection.close()


def _drop_legacy_runtime_tables(connection: sqlite3.Connection) -> None:
    for table in LEGACY_RUNTIME_TABLES:
        connection.execute(f"DROP TABLE IF EXISTS {table}")


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
    source_task_payload = _read_task_payload(database)
    task = _read_task(database)
    legacy_skill_payload = source_task_payload.get("skill_bundle_snapshot")
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
            "task_json_backup": None, "legacy_runtime_archive": None,
            "report": str(report), "status": "no_op",
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
        "task_json_backup": None, "legacy_runtime_archive": None,
        "report": str(report),
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
    legacy_runtime_archive = database.with_name(
        f"{database.name}.runtime-v5-archive-{stamp}-{uuid4().hex[:8]}.json"
    )
    temporary = database.with_name(f".{database.name}.schema-v6-{uuid4().hex}.tmp")
    published = False
    try:
        # Migration is documented as offline. A byte-for-byte copy gives the
        # operator a simple, independently verifiable rollback artifact.
        shutil.copy2(database, backup)
        _write_json_atomic(task_json_backup, source_task_payload)
        _archive_legacy_runtime(database, legacy_runtime_archive)
        if fault_hook:
            fault_hook("after_backup")
        shutil.copy2(database, temporary)
        _mark_temporary_database_v6(temporary, task)
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
                if legacy_skill_payload is not None:
                    bundle.tasks.save_task_common_skill_snapshot(
                        legacy_skill_bundle_to_task_common(
                            SkillBundleSnapshot.model_validate(legacy_skill_payload),
                            task_id=task.id,
                            created_at=datetime.now(UTC).isoformat().replace(
                                "+00:00", "Z"
                            ),
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
                _drop_legacy_runtime_tables(bundle.database.conn)
            check = bundle.database.conn.execute("PRAGMA integrity_check").fetchone()[0]
            if check != "ok":
                raise _fail("MIGRATED_DB_INVALID", "integrity check failed")
        finally:
            bundle.close()
        _prepare_single_file_publication(temporary)
        if fault_hook:
            fault_hook("before_publish")
        validated = _read_task(temporary)
        if validated.schema_version != TARGET_SCHEMA:
            raise _fail("MIGRATED_DB_INVALID", "task did not reach schema 6")
        _copy_database(temporary, database)
        published = True
        if fault_hook:
            fault_hook("after_publish")
    except Exception:
        temporary.unlink(missing_ok=True)
        if published and backup.is_file():
            _copy_database(backup, database)
        failure_report = {
            **planned,
            "dry_run": False,
            "status": "failed_rolled_back",
            "backup": str(backup) if backup.is_file() else None,
            "task_json_backup": str(task_json_backup) if task_json_backup.is_file() else None,
            "legacy_runtime_archive": (
                str(legacy_runtime_archive) if legacy_runtime_archive.is_file() else None
            ),
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
        "legacy_runtime_archive": str(legacy_runtime_archive),
        "status": "applied", "legacy_counts": counts,
        "evidence_policy": planned["evidence_policy"],
        "rollback": planned["rollback"],
    }
    _write_json_atomic(report, result)
    return result


def verify_database(db_path: str | Path) -> dict[str, Any]:
    """Verify one published schema-v6 database without modifying it."""

    database = Path(db_path).resolve()
    if not database.is_file():
        raise _fail("DATABASE_NOT_FOUND", "database file does not exist")
    task = _read_task(database)
    if task.schema_version != TARGET_SCHEMA:
        raise _fail(
            "VERIFY_SCHEMA_MISMATCH",
            f"expected schema {TARGET_SCHEMA}, found schema {task.schema_version}",
        )
    connection = sqlite3.connect(
        f"file:{database.as_posix()}?mode=ro", uri=True
    )
    try:
        connection.execute("PRAGMA query_only=ON")
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        if integrity != "ok":
            raise _fail("VERIFY_INTEGRITY_FAILED", "database integrity check failed")
        foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_key_errors:
            raise _fail("VERIFY_FOREIGN_KEYS_FAILED", "database foreign keys are invalid")
        tables = {
            str(row[0]) for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        required_tables = {
            "tasks", "sessions", "task_specs", "schema_metadata",
            "solver_instances", "global_plans", "evidence_claims",
            "knowledge_items", "governed_actions", "agent_events",
        }
        missing = sorted(required_tables - tables)
        if missing:
            raise _fail(
                "VERIFY_SCHEMA_INCOMPLETE",
                f"required schema-v6 tables are missing: {', '.join(missing)}",
            )
        retained = sorted(set(LEGACY_RUNTIME_TABLES) & tables)
        if retained:
            raise _fail(
                "VERIFY_LEGACY_RUNTIME_RETAINED",
                f"legacy Runtime tables remain: {', '.join(retained)}",
            )
        session_versions = {
            int(row[0]) for row in connection.execute(
                "SELECT schema_version FROM sessions WHERE task_id=?", (task.id,)
            ).fetchall()
        }
        if session_versions != {TARGET_SCHEMA}:
            raise _fail(
                "VERIFY_SESSION_SCHEMA_MISMATCH",
                "task must have exactly one schema-v6 session",
            )
        task_spec_count = int(connection.execute(
            "SELECT COUNT(*) FROM task_specs WHERE task_id=?", (task.id,)
        ).fetchone()[0])
        if task_spec_count != 1:
            raise _fail(
                "VERIFY_TASK_SPEC_MISSING",
                "task must have exactly one schema-v6 TaskSpec",
            )
        metadata = connection.execute(
            "SELECT content_sha256 FROM schema_metadata WHERE version=?",
            (TARGET_SCHEMA,),
        ).fetchone()
        if metadata is None or not str(metadata[0]):
            raise _fail(
                "VERIFY_SCHEMA_METADATA_MISSING",
                "schema-v6 metadata is missing",
            )
    except sqlite3.Error as exc:
        raise _fail("VERIFY_DATABASE_INVALID", "database verification query failed") from exc
    finally:
        connection.close()
    return {
        "database": str(database), "task_id": task.id,
        "source_schema": TARGET_SCHEMA, "target_schema": TARGET_SCHEMA,
        "status": "verified", "verified": True, "integrity": "ok",
        "foreign_key_errors": 0,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Explicitly migrate one offline TGA evidence.db from schema 5 to schema 6."
    )
    parser.add_argument("--db", required=True, type=Path, help="path to evidence.db")
    operation = parser.add_mutually_exclusive_group(required=True)
    operation.add_argument("--dry-run", action="store_true", help="write only the migration plan")
    operation.add_argument("--apply", action="store_true", help="back up and publish the migration")
    operation.add_argument("--verify", action="store_true", help="verify a published schema-v6 database")
    parser.add_argument(
        "--backup", action="store_true",
        help="state the backup-first requirement explicitly; apply always creates backups",
    )
    parser.add_argument(
        "--report", type=Path,
        help="migration report path (default: <db>.migration-report.json)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = (
            verify_database(args.db)
            if args.verify
            else migrate_database(
                args.db, apply=args.apply, report_path=args.report
            )
        )
    except MigrationError as exc:
        print(f"migration failed [{exc.code}]: {exc}", file=sys.stderr)
        return 1
    except Exception:
        print("migration failed [INTERNAL_ERROR]: source database was not replaced", file=sys.stderr)
        return 1
    if result.get("verified"):
        print(
            f"verification complete: task={result['task_id']} schema=6 "
            f"integrity={result['integrity']}"
        )
    elif result["dry_run"] and result["would_migrate"]:
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
