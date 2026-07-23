"""Immutable task-owned input storage and bounded retrieval helpers."""

from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import re
import shutil
import stat
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from tga.contracts import MediaKind, ResourceProvenance, ResourceRef, ResourceRole, SessionFile, SessionInput
from tga.evidence.store import utc_now


_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10)),
}
_SAFE_ID = re.compile(r"^(?:input|hint)_[A-Za-z0-9_-]{1,64}$")
MAX_MODEL_IMAGE_BYTES = 20 * 1024 * 1024
DEFAULT_STAGING_TTL_SECONDS = 24 * 60 * 60


@dataclass(frozen=True)
class InputLimits:
    max_file_bytes: int = 32 * 1024 * 1024
    max_total_bytes: int = 128 * 1024 * 1024
    max_files: int = 64
    max_extract_files: int = 512
    max_extract_bytes: int = 256 * 1024 * 1024
    max_compression_ratio: int = 200

    @classmethod
    def from_environment(cls) -> "InputLimits":
        def bounded(name: str, default: int, maximum: int) -> int:
            try:
                return max(1, min(int(os.environ.get(name, str(default))), maximum))
            except ValueError:
                return default
        return cls(
            max_file_bytes=bounded("TGA_INPUT_MAX_FILE_BYTES", cls.max_file_bytes, 1024 * 1024 * 1024),
            max_total_bytes=bounded("TGA_INPUT_MAX_TOTAL_BYTES", cls.max_total_bytes, 4 * 1024 * 1024 * 1024),
            max_files=bounded("TGA_INPUT_MAX_FILES", cls.max_files, 4096),
            max_extract_files=bounded("TGA_INPUT_MAX_EXTRACT_FILES", cls.max_extract_files, 100_000),
            max_extract_bytes=bounded("TGA_INPUT_MAX_EXTRACT_BYTES", cls.max_extract_bytes, 8 * 1024 * 1024 * 1024),
            max_compression_ratio=bounded("TGA_INPUT_MAX_COMPRESSION_RATIO", cls.max_compression_ratio, 10_000),
        )


def safe_original_name(value: str) -> str:
    name = value.strip()
    if not name or name in {".", ".."} or name != Path(name).name:
        raise ValueError("unsafe input filename")
    if any(ord(char) < 32 for char in name) or any(char in name for char in '<>:"/\\|?*'):
        raise ValueError("unsafe input filename")
    if name.rstrip(" .") != name or name.split(".", 1)[0].upper() in _WINDOWS_RESERVED:
        raise ValueError("unsafe input filename")
    return name


def infer_resource_kind(filename: str, mime_type: str | None = None) -> str:
    mime = (mime_type or "").casefold()
    suffix = Path(filename).suffix.casefold()
    if mime.startswith("image/") or suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}:
        return "image"
    if suffix in {".zip", ".tar", ".tgz", ".gz", ".7z", ".rar"}:
        return "archive"
    return "file"


def detect_mime_type(path: Path, original_name: str) -> str:
    """Detect common formats from bytes, falling back to the safe filename."""

    with path.open("rb") as stream:
        head = stream.read(16)
    signatures = (
        (b"\x89PNG\r\n\x1a\n", "image/png"),
        (b"\xff\xd8\xff", "image/jpeg"),
        (b"GIF87a", "image/gif"),
        (b"GIF89a", "image/gif"),
        (b"PK\x03\x04", "application/zip"),
        (b"\x7fELF", "application/x-elf"),
        (b"MZ", "application/vnd.microsoft.portable-executable"),
        (b"%PDF-", "application/pdf"),
    )
    for signature, mime in signatures:
        if head.startswith(signature):
            return mime
    guessed = mimetypes.guess_type(original_name)[0]
    if guessed:
        return guessed
    if head and b"\x00" not in head:
        try:
            head.decode("utf-8")
            return "text/plain"
        except UnicodeDecodeError:
            pass
    return "application/octet-stream"


def media_kind_for(mime_type: str, original_name: str) -> MediaKind:
    mime = mime_type.casefold()
    suffix = Path(original_name).suffix.casefold()
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("text/") or mime in {"application/json", "application/xml", "application/javascript"}:
        return "text"
    if mime in {"application/pdf", "application/msword", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}:
        return "document"
    if mime in {"application/zip", "application/gzip", "application/x-tar", "application/x-7z-compressed", "application/vnd.rar"} or suffix in {".zip", ".gz", ".tgz", ".tar", ".7z", ".rar"}:
        return "archive"
    if mime == "application/octet-stream" or mime in {"application/x-elf", "application/vnd.microsoft.portable-executable"}:
        return "binary"
    return "other"


def task_artifact_root(task_root: str | Path, task: Any) -> Path:
    """Return the durable ArtifactStore root for a persisted task schema."""

    root = Path(task_root).resolve()
    schema_version = int(getattr(task, "schema_version", 0) or 0)
    relative = Path("workspace") / "artifacts" if schema_version >= 4 else Path("artifacts")
    return (root / relative).resolve()


def cleanup_expired_staged_inputs(
    staging_root: str | Path,
    *,
    now: datetime | None = None,
    ttl_seconds: int | None = None,
) -> int:
    """Remove expired, unclaimed staging directories and return the count."""

    root = Path(staging_root).resolve()
    if not root.is_dir():
        return 0
    current = now or datetime.now(UTC)
    if ttl_seconds is None:
        try:
            ttl_seconds = int(os.environ.get("TGA_INPUT_STAGING_TTL_SECONDS", str(DEFAULT_STAGING_TTL_SECONDS)))
        except ValueError:
            ttl_seconds = DEFAULT_STAGING_TTL_SECONDS
    ttl_seconds = max(60, min(ttl_seconds, 30 * 24 * 60 * 60))
    cutoff = current - timedelta(seconds=ttl_seconds)
    removed = 0
    for stage in root.iterdir():
        if not stage.is_dir() or not re.fullmatch(r"[a-f0-9]{32}", stage.name):
            continue
        try:
            metadata = json.loads((stage / "manifest.json").read_text(encoding="utf-8"))
            created_at = datetime.fromisoformat(str(metadata["created_at"]).replace("Z", "+00:00"))
        except (KeyError, OSError, ValueError, json.JSONDecodeError):
            created_at = datetime.fromtimestamp(stage.stat().st_mtime, tz=UTC)
        if created_at <= cutoff:
            shutil.rmtree(stage, ignore_errors=True)
            removed += int(not stage.exists())
    return removed


class SessionWorkspace:
    """Own the single persistent workspace shared by runtime and local MCPs."""

    def __init__(self, task_root: str | Path, *, limits: InputLimits | None = None):
        self.task_root = Path(task_root).resolve()
        self.root = (self.task_root / "workspace").resolve()
        self.limits = limits or InputLimits.from_environment()

    def ensure(self) -> Path:
        for relative in ("inputs/task", "inputs/hints", "artifacts", "evidence", "tool-results", "state"):
            path = (self.root / relative).resolve()
            self._inside(path, self.root)
            path.mkdir(parents=True, exist_ok=True)
        return self.root

    def claim_staged(
        self,
        *,
        staging_root: str | Path,
        task_asset_ids: list[str],
        hint_text: str | None,
        hint_asset_ids: list[str],
    ) -> tuple[SessionInput, list[Path]]:
        ids = [*task_asset_ids, *hint_asset_ids]
        if len(ids) != len(set(ids)):
            raise ValueError("asset ids must be unique")
        if len(ids) > self.limits.max_files:
            raise ValueError("input file count limit exceeded")
        staging = Path(staging_root).resolve()
        records: list[tuple[str, str, Path, dict[str, Any]]] = []
        total = 0
        for kind, asset_ids in (("task", task_asset_ids), ("hint", hint_asset_ids)):
            for asset_id in asset_ids:
                if not re.fullmatch(r"asset_[a-f0-9]{32}", asset_id):
                    raise ValueError(f"invalid asset id: {asset_id}")
                token = asset_id.removeprefix("asset_")
                stage = (staging / token).resolve()
                self._inside(stage, staging)
                try:
                    metadata = json.loads((stage / "manifest.json").read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    raise ValueError(f"staged asset is unavailable: {asset_id}") from exc
                if metadata.get("asset_id") != asset_id:
                    raise ValueError(f"staged asset identity mismatch: {asset_id}")
                size = int(metadata.get("size") or 0)
                if size > self.limits.max_file_bytes:
                    raise ValueError("input exceeds per-file size limit")
                total += size
                records.append((kind, asset_id, stage, metadata))
        if total > self.limits.max_total_bytes:
            raise ValueError("input total size limit exceeded")

        self.ensure()
        files: list[SessionFile] = []
        cleanup: list[Path] = []
        created: list[Path] = []
        try:
            for kind, asset_id, stage, metadata in records:
                source = stage / "source"
                original_name = safe_original_name(str(metadata.get("original_name") or ""))
                suffix = Path(original_name).suffix.casefold()
                safe_suffix = suffix if re.fullmatch(r"\.[a-z0-9]{1,16}", suffix) else ""
                stored_name = f"{asset_id.removeprefix('asset_')}{safe_suffix}"
                folder = "task" if kind == "task" else "hints"
                relative_path = f"inputs/{folder}/{stored_name}"
                destination = (self.root / relative_path).resolve()
                self._inside(destination, self.root)
                digest = hashlib.sha256()
                size = 0
                with source.open("rb") as incoming, destination.open("xb") as output:
                    while chunk := incoming.read(1024 * 1024):
                        size += len(chunk)
                        digest.update(chunk)
                        output.write(chunk)
                created.append(destination)
                if size != int(metadata.get("size") or -1) or digest.hexdigest() != metadata.get("sha256"):
                    raise ValueError(f"staged asset checksum mismatch: {asset_id}")
                mime_type = detect_mime_type(destination, original_name)
                files.append(SessionFile(
                    id=asset_id,
                    originalName=original_name,
                    storedName=stored_name,
                    relativePath=relative_path,
                    mimeType=mime_type,
                    size=size,
                    sha256=digest.hexdigest(),
                    kind=kind,
                    mediaKind=media_kind_for(mime_type, original_name),
                ))
                cleanup.append(stage)
        except Exception:
            for path in created:
                path.unlink(missing_ok=True)
            raise
        session_input = SessionInput(
            taskFiles=[item for item in files if item.kind == "task"],
            hint={"text": hint_text, "files": [item for item in files if item.kind == "hint"]},
        )
        manifest = self.root / "state" / "input-manifest.json"
        manifest.write_text(session_input.model_dump_json(indent=2, by_alias=True), encoding="utf-8")
        return session_input, cleanup

    def path_for(self, item: SessionFile) -> Path:
        path = (self.root / item.relative_path).resolve()
        self._inside(path, self.root)
        return path

    def verified_bytes(self, item: SessionFile) -> bytes:
        path = self.path_for(item)
        if not path.is_file() or path.is_symlink():
            raise FileNotFoundError(item.id)
        raw = path.read_bytes()
        if len(raw) != item.size or hashlib.sha256(raw).hexdigest() != item.sha256:
            raise ValueError("immutable Session input checksum mismatch")
        return raw

    def read(self, item: SessionFile, *, offset: int = 0, limit: int = 16_384) -> dict[str, Any]:
        if offset < 0 or limit < 1 or limit > 262_144:
            raise ValueError("invalid input read range")
        raw = self.verified_bytes(item)
        if item.media_kind not in {"text", "document"} or b"\x00" in raw[:8192]:
            raise ValueError("binary input must be analyzed with a binary-aware tool")
        text = raw.decode("utf-8", errors="replace")
        excerpt = text[offset: offset + limit]
        return {
            "input_id": item.id,
            "offset": offset,
            "next_offset": offset + len(excerpt),
            "eof": offset + len(excerpt) >= len(text),
            "content": excerpt,
        }

    def search(self, item: SessionFile, *, query: str, limit: int = 20) -> dict[str, Any]:
        if not query or len(query) > 256:
            raise ValueError("invalid input search query")
        text = self.read(item, offset=0, limit=262_144)["content"]
        matches = [
            {"line": number, "text": line[:1000]}
            for number, line in enumerate(text.splitlines(), start=1)
            if query.casefold() in line.casefold()
        ][:max(1, min(limit, 100))]
        return {"input_id": item.id, "query": query, "matches": matches}

    def image_block(self, item: SessionFile) -> dict[str, Any]:
        if item.media_kind != "image" or not item.mime_type.startswith("image/"):
            raise ValueError("input is not an image")
        if item.size > min(self.limits.max_file_bytes, MAX_MODEL_IMAGE_BYTES):
            raise ValueError("image exceeds model content limit")
        raw = self.verified_bytes(item)
        return {
            "type": "image_url",
            "image_url": {"url": f"data:{item.mime_type};base64,{base64.b64encode(raw).decode('ascii')}"},
        }

    @staticmethod
    def _inside(path: Path, root: Path) -> None:
        try:
            path.resolve().relative_to(root.resolve())
        except ValueError as exc:
            raise ValueError("workspace path escapes Session root") from exc


class TaskInputStore:
    def __init__(self, task_root: str | Path, *, limits: InputLimits | None = None):
        self.task_root = Path(task_root).resolve()
        self.root = (self.task_root / "inputs").resolve()
        self.objects = self.root / "objects"
        self.materialized = self.root / "materialized"
        self.limits = limits or InputLimits.from_environment()

    def save_upload(
        self, *, input_id: str, role: ResourceRole, filename: str, data: bytes,
        mime_type: str | None = None, kind: str | None = None, label: str | None = None,
        artifact_id: str | None = None, provenance: ResourceProvenance | None = None,
    ) -> ResourceRef:
        if not _SAFE_ID.fullmatch(input_id):
            raise ValueError("invalid input id")
        expected_prefix = "input_" if role == "target" else "hint_"
        if not input_id.startswith(expected_prefix):
            raise ValueError("input id does not match resource role")
        original_name = safe_original_name(filename)
        if len(data) > self.limits.max_file_bytes:
            raise ValueError("input exceeds per-file size limit")
        existing = list(self.objects.glob("*/manifest.json")) if self.objects.exists() else []
        if len(existing) >= self.limits.max_files:
            raise ValueError("input file count limit exceeded")
        total = sum(int(json.loads(item.read_text(encoding="utf-8")).get("size") or 0) for item in existing)
        if total + len(data) > self.limits.max_total_bytes:
            raise ValueError("input total size limit exceeded")

        object_dir = (self.objects / input_id).resolve()
        self._inside(object_dir, self.objects.resolve())
        if object_dir.exists():
            raise ValueError("input id already exists")
        object_dir.mkdir(parents=True, exist_ok=False)
        payload_path = object_dir / "source"
        try:
            with payload_path.open("xb") as handle:
                handle.write(data)
            digest = hashlib.sha256(data).hexdigest()
            detected_mime = (mime_type or mimetypes.guess_type(original_name)[0] or "application/octet-stream").split(";", 1)[0]
            resource = ResourceRef(
                id=input_id,
                role=role,
                kind=kind or infer_resource_kind(original_name, detected_mime),
                label=label or original_name,
                uri=f"input://{input_id}",
                mime_type=detected_mime,
                size=len(data),
                sha256=digest,
                provenance=provenance or ResourceProvenance(
                    source="user_upload", created_at=utc_now(), original_name=original_name,
                ),
                status="available",
                metadata={"storage_relpath": f"objects/{input_id}/source", "indexed": False},
                summary=f"Immutable uploaded {detected_mime} file ({len(data)} bytes).",
                artifact_id=artifact_id,
            )
            (object_dir / "manifest.json").write_text(resource.model_dump_json(indent=2), encoding="utf-8")
            return resource
        except Exception:
            shutil.rmtree(object_dir, ignore_errors=True)
            raise

    def load(self, resource: ResourceRef) -> bytes:
        relpath = str(resource.metadata.get("storage_relpath") or "")
        if not relpath:
            raise ValueError("resource is not a task-owned file")
        path = (self.root / relpath).resolve()
        self._inside(path, self.root)
        if not path.is_file() or path.is_symlink():
            raise FileNotFoundError(resource.id)
        data = path.read_bytes()
        if resource.sha256 and hashlib.sha256(data).hexdigest() != resource.sha256.casefold():
            raise ValueError("immutable input checksum mismatch")
        return data

    def read(self, resource: ResourceRef, *, offset: int = 0, limit: int = 16_384) -> dict[str, Any]:
        if offset < 0 or limit < 1 or limit > 262_144:
            raise ValueError("invalid input read range")
        if resource.kind == "text" and resource.text is not None:
            text = resource.text
        else:
            raw = self.load(resource)
            if b"\x00" in raw[:8192] and not (resource.mime_type or "").startswith("text/"):
                raise ValueError("binary input must be materialized or analyzed with a binary-aware tool")
            text = raw.decode("utf-8", errors="replace")
        excerpt = text[offset: offset + limit]
        return {
            "input_id": resource.id,
            "offset": offset,
            "next_offset": offset + len(excerpt),
            "eof": offset + len(excerpt) >= len(text),
            "content": excerpt,
            "provenance": resource.provenance.model_dump(mode="json"),
        }

    def search(self, resource: ResourceRef, *, query: str, limit: int = 20) -> dict[str, Any]:
        if not query or len(query) > 256:
            raise ValueError("invalid input search query")
        text = self.read(resource, offset=0, limit=262_144)["content"]
        folded = query.casefold()
        matches = []
        for number, line in enumerate(text.splitlines(), start=1):
            if folded in line.casefold():
                matches.append({"line": number, "text": line[:1000]})
                if len(matches) >= max(1, min(limit, 100)):
                    break
        return {"input_id": resource.id, "query": query, "matches": matches, "provenance": resource.provenance.model_dump(mode="json")}

    def image_block(self, resource: ResourceRef) -> dict[str, Any]:
        if resource.kind != "image" or not (resource.mime_type or "").startswith("image/"):
            raise ValueError("input is not an image")
        raw = self.load(resource)
        if len(raw) > min(self.limits.max_file_bytes, 20 * 1024 * 1024):
            raise ValueError("image exceeds model content limit")
        return {
            "input_id": resource.id,
            "content_block": {
                "type": "image_url",
                "image_url": {"url": f"data:{resource.mime_type};base64,{base64.b64encode(raw).decode('ascii')}"},
            },
            "provenance": resource.provenance.model_dump(mode="json"),
        }

    def materialize(self, resource: ResourceRef, workspace: str | Path) -> tuple[Path, str]:
        raw = self.load(resource)
        workspace_root = Path(workspace).resolve()
        name = safe_original_name(resource.provenance.original_name or resource.label)
        destination_dir = (workspace_root / "inputs" / resource.id).resolve()
        self._inside(destination_dir, workspace_root)
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination = (destination_dir / name).resolve()
        self._inside(destination, destination_dir)
        if destination.exists():
            if destination.is_symlink() or hashlib.sha256(destination.read_bytes()).hexdigest() != (resource.sha256 or ""):
                raise ValueError("materialized input collision")
        else:
            with destination.open("xb") as handle:
                handle.write(raw)
        return destination, hashlib.sha256(raw).hexdigest()

    def extract_zip(self, resource: ResourceRef, workspace: str | Path) -> list[Path]:
        raw_path, _ = self.materialize(resource, workspace)
        destination = (Path(workspace).resolve() / "inputs" / resource.id / "extracted").resolve()
        self._inside(destination, Path(workspace).resolve())
        destination.mkdir(parents=True, exist_ok=True)
        extracted: list[Path] = []
        total = 0
        with zipfile.ZipFile(raw_path) as archive:
            members = archive.infolist()
            if len(members) > self.limits.max_extract_files:
                raise ValueError("archive file count limit exceeded")
            for member in members:
                pure = PurePosixPath(member.filename)
                if pure.is_absolute() or ".." in pure.parts or not pure.parts:
                    raise ValueError("archive contains an unsafe path")
                unix_mode = member.external_attr >> 16
                if stat.S_ISLNK(unix_mode):
                    raise ValueError("archive symlinks are forbidden")
                total += member.file_size
                if total > self.limits.max_extract_bytes:
                    raise ValueError("archive expanded size limit exceeded")
                if member.compress_size and member.file_size / member.compress_size > self.limits.max_compression_ratio:
                    raise ValueError("archive compression ratio limit exceeded")
                target = (destination / Path(*pure.parts)).resolve()
                self._inside(target, destination)
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if target.exists():
                    raise ValueError("archive extraction would overwrite a file")
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
                extracted.append(target)
        return extracted

    @staticmethod
    def _inside(path: Path, root: Path) -> None:
        try:
            path.resolve().relative_to(root.resolve())
        except ValueError as exc:
            raise ValueError("input path escapes task-owned root") from exc


def resource_by_id(resources: Iterable[ResourceRef], input_id: str) -> ResourceRef:
    for resource in resources:
        if resource.id == input_id:
            return resource
    raise KeyError(input_id)
