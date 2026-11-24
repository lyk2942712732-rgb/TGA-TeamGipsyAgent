"""File-outbox question importer: Scout collects freely; system validates and imports deterministically."""

from __future__ import annotations

import asyncio
import json
import os
from collections import OrderedDict, defaultdict
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from pydantic import ValidationError

from .aoe_models import AOEQuestionInput
from .errors import ConflictError, ContractError
from .flag_signals import declared_format

OUTBOX_DIR = "outbox"
ATTACHMENTS_DIR = "attachments"
IMPORTED_DIR = "imported"
REJECTED_DIR = "rejected"
MAX_OUTBOX_FILE_BYTES = 2 * 1024 * 1024
MAX_RECENT_ERROR_GROUPS = 8
MAX_SAMPLE_FILES = 3

REQUEST_OBJECT_HINT = (
    ' (must be a request object like {"method":"POST","url":"https://...","headers":{},"params":{},"body":{}}, '
    'not a bare URL string; or set target.mode="unknown" if no static API contract exists)'
)


def _archive_path(directory: Path, name: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / name
    if not target.exists():
        return target
    stem = Path(name).stem
    suffix = Path(name).suffix
    return directory / f"{stem}-{uuid4().hex[:8]}{suffix}"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{uuid4().hex[:8]}.tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def aggregate_errors(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse repeated identical rejection messages so models see clear grouped feedback."""
    groups: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for entry in errors:
        message = str(entry.get("error", "unknown error")).strip()
        filename = Path(str(entry.get("file", ""))).name or "unknown.json"
        group = groups.get(message)
        if group is None:
            group = {"count": 0, "error": message, "sample_files": []}
            groups[message] = group
        group["count"] += 1
        if len(group["sample_files"]) < MAX_SAMPLE_FILES and filename not in group["sample_files"]:
            group["sample_files"].append(filename)
    ordered = sorted(groups.values(), key=lambda item: (-item["count"], item["error"]))
    return ordered[:MAX_RECENT_ERROR_GROUPS]


def _normalize_item(item: Any) -> AOEQuestionInput:
    if not isinstance(item, dict):
        raise ContractError("Each question entry must be a JSON object")
    data = dict(item)
    if data.get("attachments") is None:
        data.pop("attachments", None)
    if data.get("tags") is None:
        data.pop("tags", None)
    raw = data.get("raw_source") if isinstance(data.get("raw_source"), dict) else data.get("source")
    if not data.get("flag_format"):
        detected = declared_format(data) or (declared_format(raw) if isinstance(raw, dict) else "")
        if detected:
            data["flag_format"] = detected
    try:
        return AOEQuestionInput.model_validate(data)
    except ValidationError as exc:
        parts: list[str] = []
        needs_request_hint = False
        for err in exc.errors():
            loc = ".".join(str(part) for part in err.get("loc", ())) or "question"
            msg = err.get("msg", "invalid")
            if loc in {"target.start", "target.stop", "target.status", "submission.request"} and "dictionary" in msg:
                needs_request_hint = True
            parts.append(f"{loc}: {msg}")
        detail = "; ".join(parts)
        if needs_request_hint:
            detail += REQUEST_OBJECT_HINT
        raise ContractError(detail) from exc


def _extract_specs(payload: Any) -> list[AOEQuestionInput]:
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict) and set(payload) == {"questions"} and isinstance(payload["questions"], list):
        items = payload["questions"]
    elif isinstance(payload, dict):
        items = [payload]
    else:
        raise ContractError("Outbox file must contain a question JSON object, a list, or {'questions': [...]}")
    if not items:
        raise ContractError("Outbox file contains no questions")
    return [_normalize_item(item) for item in items]


class QuestionImporter:
    """Consume staged outbox JSON files from Scout's workspace into the single source of truth."""

    def __init__(self, aoe) -> None:
        self.aoe = aoe
        self.locks: defaultdict[UUID, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._import_seq: defaultdict[UUID, int] = defaultdict(int)
        self._rejected_total: defaultdict[UUID, int] = defaultdict(int)
        self._last_batch: dict[UUID, dict[str, int]] = {}
        self._recent_errors: dict[UUID, list[dict[str, Any]]] = {}

    def workspace(self, parent_id: UUID) -> Path:
        return self.aoe.config.task_path(parent_id, "workspaces", "scout")

    def outbox_dir(self, parent_id: UUID) -> Path:
        return self.workspace(parent_id) / OUTBOX_DIR

    def ensure_directories(self, parent_id: UUID) -> None:
        root = self.workspace(parent_id)
        for name in (OUTBOX_DIR, ATTACHMENTS_DIR, IMPORTED_DIR, REJECTED_DIR):
            (root / name).mkdir(parents=True, exist_ok=True)

    def state_summary(self, parent_id: UUID) -> dict[str, Any]:
        return {
            "import_seq": self._import_seq.get(parent_id, 0),
            "rejected_count": self._rejected_total.get(parent_id, 0),
            "last_batch": self._last_batch.get(
                parent_id, {"imported": 0, "skipped_existing": 0, "rejected": 0}
            ),
            "recent_errors": list(self._recent_errors.get(parent_id, [])),
        }

    def clear_state(self, parent_id: UUID) -> None:
        self._import_seq.pop(parent_id, None)
        self._rejected_total.pop(parent_id, None)
        self._last_batch.pop(parent_id, None)
        self._recent_errors.pop(parent_id, None)

    def _resolve_targets(self, parent_id: UUID, supplied: str | None) -> list[Path]:
        root = self.workspace(parent_id).resolve()
        outbox = (root / OUTBOX_DIR).resolve()
        if not supplied:
            if not outbox.is_dir():
                return []
            candidates = [
                p
                for p in outbox.iterdir()
                if not p.name.startswith(".") and p.suffix.lower() == ".json" and not p.name.endswith(".tmp")
            ]
            return sorted(candidates, key=lambda p: (p.stat().st_mtime_ns if p.exists() else 0, p.name))
        cleaned = supplied.strip()
        if cleaned.startswith("/workspace/"):
            candidate = (root / cleaned.removeprefix("/workspace/")).resolve()
        elif cleaned.startswith("/"):
            raise ContractError("Import path must stay inside /workspace (typically /workspace/outbox/)")
        else:
            direct = (root / cleaned).resolve()
            in_outbox = (outbox / cleaned).resolve()
            candidate = direct if direct.exists() else in_outbox
        if not candidate.is_relative_to(root):
            raise ContractError("Import path escapes /workspace")
        if candidate.is_symlink():
            raise ContractError("Import path must be a regular file or directory, not a symlink")
        if candidate.is_dir():
            files = [
                p
                for p in candidate.iterdir()
                if not p.name.startswith(".") and p.suffix.lower() == ".json" and not p.name.endswith(".tmp")
            ]
            return sorted(files, key=lambda p: (p.stat().st_mtime_ns if p.exists() else 0, p.name))
        if not candidate.is_file():
            raise ContractError(f"Outbox file not found: {supplied}")
        return [candidate]

    def has_pending(self, parent_id: UUID) -> bool:
        outbox = self.outbox_dir(parent_id)
        if not outbox.is_dir():
            return False
        return any(
            not p.name.startswith(".") and p.suffix.lower() == ".json" and not p.name.endswith(".tmp")
            for p in outbox.iterdir()
        )

    async def import_outbox(self, parent_id: UUID, path: str | None = None) -> dict[str, Any]:
        async with self.locks[parent_id]:
            parent = await self.aoe.storage.get_task(parent_id)
            if parent.metadata.get("collection_complete"):
                questions = await self.aoe.storage.list_questions(parent_id)
                keys = sorted(q.metadata["source_key"] for q in questions)
                return {
                    "saved": False,
                    "imported": [],
                    "skipped_existing": [],
                    "errors": [],
                    "error_summary": list(self._recent_errors.get(parent_id, [])),
                    "import_seq": self._import_seq.get(parent_id, 0),
                    "imported_count": len(questions),
                    "imported_keys": keys,
                    "rejected_count": self._rejected_total.get(parent_id, 0),
                    "collection_complete": True,
                    "status": parent.metadata.get("collection_status", "complete"),
                }
            self.ensure_directories(parent_id)
            workspace_root = self.workspace(parent_id).resolve()
            outbox_root = (workspace_root / OUTBOX_DIR).resolve()
            imported_dir = workspace_root / IMPORTED_DIR
            rejected_dir = workspace_root / REJECTED_DIR

            targets = self._resolve_targets(parent_id, path)
            registered_keys = {
                q.metadata["source_key"]
                for q in await self.aoe.storage.list_questions(parent_id)
                if q.metadata.get("registration_complete")
            }
            imported: list[dict[str, Any]] = []
            skipped_existing: list[dict[str, Any]] = []
            errors: list[dict[str, Any]] = []
            new_questions_count = 0
            skipped_questions_count = 0

            for file_path in targets:
                in_outbox = file_path.parent.resolve() == outbox_root
                display = (
                    "/workspace/" + file_path.resolve().relative_to(workspace_root).as_posix()
                    if file_path.exists() and not file_path.is_symlink()
                    else f"/workspace/{OUTBOX_DIR}/{file_path.name}"
                )
                try:
                    if file_path.is_symlink():
                        raise ContractError("Outbox entry must be a regular file, not a symlink")
                    if not file_path.is_file():
                        continue
                    if file_path.stat().st_size > MAX_OUTBOX_FILE_BYTES:
                        raise ContractError(
                            f"Outbox JSON exceeds maximum size ({MAX_OUTBOX_FILE_BYTES} bytes)"
                        )
                    raw_text = file_path.read_text(encoding="utf-8")
                    try:
                        payload = json.loads(raw_text)
                    except ValueError as exc:
                        raise ContractError(f"Invalid JSON syntax: {exc}") from exc
                    specs = _extract_specs(payload)
                    new_specs = [spec for spec in specs if spec.source_key not in registered_keys]
                    already_keys = [spec.source_key for spec in specs if spec.source_key in registered_keys]
                    if not new_specs:
                        archived_to = None
                        if in_outbox:
                            dest = _archive_path(imported_dir, file_path.name)
                            os.replace(file_path, dest)
                            archived_to = "/workspace/" + dest.resolve().relative_to(workspace_root).as_posix()
                        skipped_questions_count += len(already_keys)
                        skipped_existing.append(
                            {
                                "file": display,
                                "status": "skipped_existing",
                                "archived_to": archived_to,
                                "source_keys": already_keys,
                            }
                        )
                        continue
                    receipt = await self.aoe.register_questions(parent_id, new_specs)
                    registered_keys.update(spec.source_key for spec in new_specs)
                    new_questions_count += len(new_specs)
                    skipped_questions_count += len(already_keys)
                    archived_to = None
                    if in_outbox:
                        dest = _archive_path(imported_dir, file_path.name)
                        os.replace(file_path, dest)
                        archived_to = "/workspace/" + dest.resolve().relative_to(workspace_root).as_posix()
                    imported.append(
                        {
                            "file": display,
                            "status": "imported",
                            "archived_to": archived_to,
                            "questions": receipt["questions"],
                            **({"skipped_existing": already_keys} if already_keys else {}),
                        }
                    )
                except (ContractError, ConflictError, ValueError, OSError) as exc:
                    message = str(exc)
                    rejected_to = None
                    error_file = None
                    if in_outbox and file_path.exists() and not file_path.is_symlink():
                        dest = _archive_path(rejected_dir, file_path.name)
                        os.replace(file_path, dest)
                        rejected_to = "/workspace/" + dest.resolve().relative_to(workspace_root).as_posix()
                        err_path = dest.with_suffix(".error.json")
                        _write_json(err_path, {"file": display, "rejected_to": rejected_to, "error": message})
                        error_file = "/workspace/" + err_path.resolve().relative_to(workspace_root).as_posix()
                    errors.append(
                        {
                            "file": display,
                            "status": "error",
                            "error": message,
                            "rejected_to": rejected_to,
                            "error_file": error_file,
                        }
                    )

            questions = await self.aoe.storage.list_questions(parent_id)
            keys = sorted(q.metadata["source_key"] for q in questions)
            parent = await self.aoe.storage.get_task(parent_id)
            error_summary = aggregate_errors(errors)

            if imported or skipped_existing or errors:
                self._import_seq[parent_id] += 1
                self._rejected_total[parent_id] += len(errors)
                self._last_batch[parent_id] = {
                    "imported": new_questions_count,
                    "skipped_existing": skipped_questions_count,
                    "rejected": len(errors),
                }
                if error_summary:
                    self._recent_errors[parent_id] = error_summary
                elif new_questions_count > 0:
                    # Clear stale errors once a clean batch succeeds.
                    self._recent_errors[parent_id] = []
                await self.aoe.refresh_collection_state(parent_id, parent=parent, questions=questions)

            return {
                "saved": bool(imported),
                "imported": imported[:20],
                "imported_files_count": len(imported),
                "skipped_existing": skipped_existing[:10],
                "skipped_count": skipped_questions_count,
                "errors": errors[:10],
                "error_summary": error_summary,
                "import_seq": self._import_seq.get(parent_id, 0),
                "imported_count": len(questions),
                "imported_keys": keys,
                "rejected_count": self._rejected_total.get(parent_id, 0),
                "collection_complete": bool(parent.metadata.get("collection_complete", False)),
                "status": parent.metadata.get("collection_status", "collecting"),
            }


QuestionCollection = QuestionImporter

__all__ = [
    "ATTACHMENTS_DIR",
    "IMPORTED_DIR",
    "MAX_OUTBOX_FILE_BYTES",
    "OUTBOX_DIR",
    "REJECTED_DIR",
    "QuestionCollection",
    "QuestionImporter",
    "aggregate_errors",
]
