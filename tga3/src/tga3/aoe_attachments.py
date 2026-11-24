"""AOE question attachment management: local file ingestion and lazy remote downloads."""

from __future__ import annotations

import asyncio
import hashlib
import mimetypes
import re
from collections import defaultdict
from collections.abc import Awaitable, Callable
from pathlib import Path
from urllib.parse import unquote, urlsplit
from uuid import UUID, uuid4, uuid5

import httpx

from .aoe_models import AttachmentDownloadSpec
from .blackboard import Blackboard
from .config import TGA3Config
from .domain import SYSTEM_ACTOR, InputFile, PublishRequest, TaskRun
from .errors import ContractError
from .evidence import evidence_files
from .storage import Storage

MAX_REMOTE_ATTACHMENT_BYTES = 256 * 1024 * 1024


class AOEAttachmentManager:
    """Owns per-question attachment caching, local ingestion, and on-demand remote downloads."""

    def __init__(self, config: TGA3Config, storage: Storage, board: Blackboard) -> None:
        self.config = config
        self.storage = storage
        self.board = board
        self.client: httpx.AsyncClient | None = None
        self._input_files: dict[UUID, list[InputFile]] = {}
        self._locks: defaultdict[UUID, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def list_for(self, question_id: UUID) -> list[InputFile]:
        if question_id not in self._input_files:
            files = await self.storage.list_input_files(question_id)
            self._input_files[question_id] = sorted(files, key=lambda f: (f.created_at, f.id))
        return self._input_files[question_id]

    def invalidate(self, question_id: UUID) -> None:
        self._input_files.pop(question_id, None)

    def clear_all(self) -> None:
        self._input_files.clear()

    async def reconcile(
        self,
        parent_id: UUID,
        questions: list[TaskRun],
        *,
        on_changed: Callable[[UUID], None] | None = None,
    ) -> None:
        """One batch query recovers missed input notifications without N+1 reads."""
        grouped: dict[UUID, list[InputFile]] = defaultdict(list)
        for item in await self.storage.list_question_input_files(parent_id):
            grouped[item.task_id].append(item)
        for question in questions:
            files = sorted(grouped[question.id], key=lambda f: (f.created_at, f.id))
            if files != self._input_files.get(question.id):
                if on_changed is not None:
                    on_changed(question.id)
                self._input_files[question.id] = files

    def resolve_source_file(self, parent_id: UUID, supplied: str) -> Path:
        if supplied.startswith("/inputs/"):
            root = self.config.task_path(parent_id, "inputs")
            relative = supplied.removeprefix("/inputs/")
        else:
            root = self.config.task_path(parent_id, "workspaces", "scout")
            relative = supplied.removeprefix("/workspace/")
        return next(iter(evidence_files(root, [relative]).values()))

    async def copy_local_input(self, question: TaskRun, source: Path, supplied: str) -> None:
        ident = uuid5(question.id, "attachment:" + supplied)
        path = self.config.task_path(question.id, "inputs") / f"{ident}-{source.name}"

        def copy() -> tuple[str, int]:
            digest, size = hashlib.sha256(), 0
            temporary = path.with_suffix(f".{uuid4().hex}.tmp")
            try:
                with source.open("rb") as src, temporary.open("wb") as out:
                    while chunk := src.read(1024 * 1024):
                        out.write(chunk)
                        digest.update(chunk)
                        size += len(chunk)
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
            return digest.hexdigest(), size

        item = next((file for file in await self.storage.list_input_files(question.id) if file.id == ident), None)
        if item is None:
            digest, size = await asyncio.to_thread(copy)
            item = await self.storage.register_input_file(
                InputFile(
                    id=ident,
                    task_id=question.id,
                    name=source.name,
                    storage_path=str(path),
                    media_type=mimetypes.guess_type(source.name)[0] or "application/octet-stream",
                    sha256=digest,
                    size_bytes=size,
                )
            )
        await self.board.publish(
            question.id,
            SYSTEM_ACTOR,
            PublishRequest(
                kind="user_file",
                topic="input",
                idempotency_key="question-file:" + str(ident),
                body={
                    "input_file_id": str(item.id),
                    "name": item.name,
                    "media_type": item.media_type,
                    "sha256": item.sha256,
                    "description": f"AOE question attachment: /inputs/{path.name}",
                },
            ),
        )

    @staticmethod
    def remote_filename(spec: AttachmentDownloadSpec, content_disposition: str = "") -> str:
        candidate = spec.filename.strip()
        if not candidate and content_disposition:
            match = re.search(r"filename\*=(?:UTF-8''|utf-8'')([^;\r\n]+)", content_disposition)
            if match:
                candidate = unquote(match[1].strip().strip("\"'"))
            else:
                match = re.search(r'filename="?([^";\r\n]+)"?', content_disposition)
                if match:
                    candidate = match[1].strip()
        if not candidate:
            candidate = unquote(Path(urlsplit(spec.url).path).name)
        candidate = Path(candidate.replace("\\", "/")).name.strip()
        candidate = re.sub(r"[\x00-\x1f<>:\"|?*]+", "_", candidate)
        return candidate or "attachment.bin"

    async def prepare_remote(
        self,
        question: TaskRun,
        *,
        on_updated: Callable[[TaskRun], Awaitable[None]] | None = None,
    ) -> bool:
        """Download declared remote attachments on demand when a Worker claims this question."""
        raw_specs = question.metadata.get("remote_attachments") or []
        if not raw_specs or question.metadata.get("pre_solved"):
            return False
        async with self._locks[question.id]:
            specs = [AttachmentDownloadSpec.model_validate(item) for item in raw_specs]
            existing_ids = {file.id for file in await self.storage.list_input_files(question.id)}
            pending = []
            for spec in specs:
                ident = uuid5(question.id, f"remote-attachment:{spec.method}:{spec.url}:{spec.filename}")
                if ident not in existing_ids:
                    pending.append((ident, spec))
            if not pending:
                return False
            inputs_root = self.config.task_path(question.id, "inputs")
            inputs_root.mkdir(parents=True, exist_ok=True)
            client = self.client
            owns = client is None
            if owns:
                client = httpx.AsyncClient(follow_redirects=True, timeout=60.0)
            changed = False
            try:
                for ident, spec in pending:
                    last_error = ""
                    saved_item = None
                    for _attempt in range(2):
                        temporary = inputs_root / f".{ident}.{uuid4().hex[:8]}.tmp"
                        try:
                            kwargs: dict = {"headers": spec.headers, "params": spec.params}
                            if isinstance(spec.body, (dict, list)):
                                kwargs["json"] = spec.body
                            elif spec.body is not None:
                                kwargs["content"] = str(spec.body).encode("utf-8")
                            async with client.stream(spec.method, spec.url, **kwargs) as response:
                                if response.status_code >= 400:
                                    last_error = f"HTTP {response.status_code}"
                                    continue
                                name = self.remote_filename(spec, response.headers.get("content-disposition", ""))
                                digest, size = hashlib.sha256(), 0
                                with temporary.open("wb") as out:
                                    async for chunk in response.aiter_bytes(1024 * 1024):
                                        size += len(chunk)
                                        if size > MAX_REMOTE_ATTACHMENT_BYTES:
                                            raise ContractError(
                                                f"Remote attachment exceeds {MAX_REMOTE_ATTACHMENT_BYTES} bytes"
                                            )
                                        out.write(chunk)
                                        digest.update(chunk)
                                target = inputs_root / f"{ident}-{name}"
                                temporary.replace(target)
                                saved_item = await self.storage.register_input_file(
                                    InputFile(
                                        id=ident,
                                        task_id=question.id,
                                        name=name,
                                        storage_path=str(target),
                                        media_type=mimetypes.guess_type(name)[0] or "application/octet-stream",
                                        sha256=digest.hexdigest(),
                                        size_bytes=size,
                                    )
                                )
                                await self.board.publish(
                                    question.id,
                                    SYSTEM_ACTOR,
                                    PublishRequest(
                                        kind="user_file",
                                        topic="input",
                                        idempotency_key="question-file:" + str(ident),
                                        body={
                                            "input_file_id": str(saved_item.id),
                                            "name": saved_item.name,
                                            "media_type": saved_item.media_type,
                                            "sha256": saved_item.sha256,
                                            "description": (
                                                f"AOE question attachment (auto-downloaded): /inputs/{target.name}"
                                            ),
                                        },
                                    ),
                                )
                                changed = True
                                break
                        except Exception as exc:
                            last_error = str(exc) or type(exc).__name__
                        finally:
                            temporary.unlink(missing_ok=True)
                    if saved_item is None:
                        await self.board.publish(
                            question.id,
                            SYSTEM_ACTOR,
                            PublishRequest(
                                kind="user_prompt",
                                topic="system",
                                idempotency_key="remote-attachment-error:" + str(ident),
                                body={
                                    "text": (
                                        f"系统自动下载题目附件失败（{spec.url}）：{last_error}。"
                                        "请根据题面或动态靶机情况自行确认附件来源。"
                                    )
                                },
                            ),
                        )
            finally:
                if owns:
                    await client.aclose()
            if changed:
                self.invalidate(question.id)
                if on_updated is not None:
                    await on_updated(await self.storage.get_task(question.id))
            return changed


__all__ = ["AOEAttachmentManager", "MAX_REMOTE_ATTACHMENT_BYTES"]
