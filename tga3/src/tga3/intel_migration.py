"""Offline, backed-up conversion of old per-Intel files; not an agent tool."""

import shutil
from pathlib import Path
from uuid import UUID

from .errors import ContractError
from .scout_intel import INTEL_FILENAME, legacy_intel_id, read_intels, save_intels


def migrate_task_intels(task_root: Path, backup_root: Path, task_id: UUID) -> int:
    import json

    task_root = task_root.resolve()
    count = 0
    for merged in (False, True):
        directory = task_root / "intels" / "scout" / ("merged" if merged else "raw")
        legacy = [path for path in directory.glob("*.json") if path.name != INTEL_FILENAME]
        if not merged:
            legacy.extend(directory.parent.glob("*.json"))
        if not legacy:
            continue
        collection = read_intels(directory, task_id, merged=merged)
        if collection is None:
            raise ContractError("Legacy Intel collection disappeared")
        ids = {item.id for item in collection.intels}
        for path in legacy:
            if path.is_symlink() or not path.resolve().is_relative_to(task_root):
                raise ContractError("Legacy Intel path escapes its task")
            value = json.loads(path.read_bytes())
            key = value.get("input", value)["key"]
            if legacy_intel_id(task_id, collection.stage, key) not in ids:
                raise ContractError("Existing aggregate does not cover every legacy Intel")
            destination = backup_root / path.relative_to(task_root)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.exists():
                shutil.copy2(path, destination)
        save_intels(directory, collection)
        verified = read_intels(directory, task_id, merged=merged)
        if verified != collection:
            raise ContractError("Intel aggregate failed read-back verification")
        for path in legacy:
            path.unlink()
        count += len(legacy)
    return count
