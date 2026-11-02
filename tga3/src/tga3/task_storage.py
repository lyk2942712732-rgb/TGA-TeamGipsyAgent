"""Offline, journalled migration from type-first to task-first storage."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from uuid import UUID

from .config import TGA3Config

KINDS = {"inputs": "input_root", "workspaces": "workspace_root", "logs": "artifact_root",
         "intels": "intel_root", "writeups": "writeup_root"}
TABLES = ("input_files", "artifacts", "writeups")


def migration_plan(config: TGA3Config) -> list[tuple[Path, Path]]:
    plan = []
    root = config.resolve_path(config.runtime.task_root)
    for kind, attribute in KINDS.items():
        legacy = config.resolve_path(getattr(config.runtime, attribute))
        if legacy == root or legacy.is_relative_to(root) or root.is_relative_to(legacy):
            raise ValueError(f"legacy and task roots overlap: {legacy}")
        if not legacy.exists():
            continue
        for source in sorted(legacy.iterdir()):
            try:
                task_id = UUID(source.name)
            except ValueError:
                raise ValueError(f"unexpected legacy storage entry: {source}") from None
            if source.is_symlink() or not source.is_dir() or not source.resolve().is_relative_to(legacy):
                raise ValueError(f"unsafe legacy task directory: {source}")
            target = config.task_path(task_id, kind)
            if target.exists():
                raise FileExistsError(f"refusing to merge existing task data: {target}")
            plan.append((source, target))
    # Upgrade the previous task-first layout without rewriting historical log contents.
    if root.exists():
        for task in sorted(root.iterdir()):
            task_id = UUID(task.name)
            source = task / "artifacts"
            if not source.exists():
                continue
            if task.is_symlink() or source.is_symlink() or not source.is_dir():
                raise ValueError(f"unsafe legacy task logs: {source}")
            target = config.task_path(task_id, "logs")
            if target.exists() or any(destination == target for _, destination in plan):
                raise FileExistsError(f"refusing to merge existing task data: {target}")
            plan.append((source, target))
    return plan


def relocate_path(raw: str, plan: list[tuple[Path, Path]]) -> str:
    path = Path(raw)
    for source, target in plan:
        if path.is_relative_to(source):
            return str(target / path.relative_to(source))
    return raw


async def migrate(config: TGA3Config, journal: Path) -> dict:
    import asyncpg

    plan = migration_plan(config)
    if journal.exists():
        raise FileExistsError(f"migration journal already exists: {journal}")
    connection = await asyncpg.connect(config.runtime.postgres_dsn)
    moved = []
    try:
        rows = {table: [dict(row) for row in await connection.fetch(f"SELECT id, storage_path FROM {table}")]
                for table in TABLES}
        state = {"status": "planned", "moves": [[str(a), str(b)] for a, b in plan], "original_paths": rows}
        journal.parent.mkdir(parents=True, exist_ok=True)

        def save():
            temporary = journal.with_suffix(".tmp")
            temporary.write_text(json.dumps(state, default=str, indent=2) + "\n", encoding="utf-8")
            temporary.replace(journal)

        save()
        try:
            async with connection.transaction():
                # The service must be stopped; reject an accidental online migration.
                active = await connection.fetchval(
                    "SELECT count(*) FROM task_runs WHERE state NOT IN ('completed','failed','cancelled')"
                )
                if active:
                    raise RuntimeError(f"{active} nonterminal tasks exist; migration requires idle storage")
                for source, target in plan:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    source.rename(target)
                    moved.append((source, target))
                    state["moved_count"] = len(moved)
                    save()
                updated = 0
                for table, items in rows.items():
                    for row in items:
                        path = relocate_path(row["storage_path"], plan)
                        if path != row["storage_path"]:
                            await connection.execute(
                                f"UPDATE {table} SET storage_path=$1 WHERE id=$2", path, row["id"]
                            )
                            updated += 1
                task_ids = await connection.fetch("SELECT id FROM task_runs")
                for task in task_ids:
                    config.ensure_task_directories(task["id"])
        except Exception:
            for source, target in reversed(moved):
                target.rename(source)
            state["status"] = "rolled_back"
            save()
            raise
        state.update(status="completed", moved_count=len(moved), updated_paths=updated, tasks=len(task_ids))
        save()
        return {key: state[key] for key in ("status", "moved_count", "updated_paths", "tasks")}
    finally:
        await connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", required=True)
    parser.add_argument("--journal", required=True)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(migrate(TGA3Config(args.config_dir), Path(args.journal)))))


if __name__ == "__main__":
    main()
