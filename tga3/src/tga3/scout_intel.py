"""Scout's extraction tool writes JSON files; only the coordinator imports them."""

import asyncio
import hashlib
import os
from collections import defaultdict
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .blackboard import Blackboard
from .config import TGA3Config
from .domain import Actor, Artifact, RunState, ScoutIntelPublication
from .errors import ConflictError, ContractError


class ScoutIntelInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    task_id: UUID
    key: str = Field(min_length=1, max_length=160)
    claim: str = Field(min_length=1, max_length=2000)
    detail: str = Field(default="", max_length=8000)
    topic: str = Field(default="general", min_length=1, max_length=200)
    evidence_paths: list[str] = Field(default_factory=list, max_length=30)


class ScoutIntelDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = Field(default=1, ge=1, le=1)
    sequence: int = Field(ge=1)
    input: ScoutIntelInput
    publication: ScoutIntelPublication


class ScoutIntelStore:
    def __init__(self, config: TGA3Config, board: Blackboard):
        self.config, self.board, self.storage = config, board, board.storage
        self.locks = defaultdict(asyncio.Lock)

    def directory(self, task_id: UUID) -> Path:
        return self.config.resolve_path(self.config.runtime.intel_root) / str(task_id) / "scout"

    def documents(self, task_id: UUID) -> list[ScoutIntelDocument]:
        result = []
        for path in sorted(self.directory(task_id).glob("*.json")):
            if path.is_symlink():
                raise ContractError("Intel JSON must be a regular file")
            document = ScoutIntelDocument.model_validate_json(path.read_bytes())
            if (document.input.task_id != task_id or document.publication.task_id != task_id
                    or document.publication.actor.agent_id != "scout"
                    or document.publication.idempotency_key != f"scout-json:{document.input.key}"):
                raise ContractError("Intel JSON does not belong to this task")
            result.append(document)
        return sorted(result, key=lambda document: document.sequence)

    async def write(self, request: ScoutIntelInput) -> dict:
        async with self.locks[request.task_id]:
            task = await self.storage.get_task(request.task_id)
            run = await self.storage.get_agent(request.task_id, "scout")
            if task.state != RunState.SCOUTING or run.metadata.get("scout_phase") != "extracting":
                raise ContractError("Intel JSON can be written only during Scout extraction")
            documents = self.documents(request.task_id)
            for document in documents:
                if document.input.key == request.key:
                    if document.input != request:
                        raise ConflictError("Intel key already belongs to a different result")
                    return self.receipt(request, document.sequence)
            configured = self.config.agents.agents["scout"]
            actor = Actor(agent_id="scout", display_name=configured.display_name, role="scout",
                          sdk=run.sdk, model=run.model_id)
            root = (self.config.resolve_path(self.config.runtime.artifact_root)
                    / str(request.task_id) / "scout").resolve()
            references = []
            for relative in request.evidence_paths:
                path = (root / relative).resolve()
                if not path.is_relative_to(root) or not path.is_file():
                    raise ContractError(f"Evidence is not a file under /artifacts: {relative}")
                content = path.read_bytes()
                artifact = await self.board.register_artifact(Artifact(
                    task_id=request.task_id, created_by=actor, name=relative, storage_path=str(path),
                    media_type="application/octet-stream", sha256=hashlib.sha256(content).hexdigest(),
                    size_bytes=len(content),
                ))
                references.append({"artifact_id": artifact.id, "locator": relative})
            publication = ScoutIntelPublication(
                task_id=request.task_id, actor=actor.model_dump(), kind="intel", topic=request.topic,
                body={"claim": request.claim, "detail": request.detail}, artifact_refs=references,
                idempotency_key=f"scout-json:{request.key}",
            )
            sequence = max((item.sequence for item in documents), default=0) + 1
            document = ScoutIntelDocument(sequence=sequence, input=request, publication=publication)
            directory = self.directory(request.task_id)
            directory.mkdir(parents=True, exist_ok=True)
            target = directory / self.filename(request.key, sequence)
            temporary = None
            try:
                with NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory, delete=False) as output:
                    temporary = Path(output.name)
                    output.write(document.model_dump_json(indent=2))
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, target)
            finally:
                if temporary:
                    temporary.unlink(missing_ok=True)
            await self.storage.update_agent(request.task_id, "scout", metadata={"intel_file_count": sequence})
            return self.receipt(request, sequence)

    @staticmethod
    def filename(key: str, sequence: int) -> str:
        return f"{sequence:05d}-{hashlib.sha256(key.encode()).hexdigest()[:16]}.json"

    def receipt(self, request: ScoutIntelInput, sequence: int) -> dict:
        return {"saved": True, "published": False,
                "path": f"/intels/{self.filename(request.key, sequence)}", "sequence": sequence}

    async def flush(self, task_id: UUID) -> int:
        async with self.locks[task_id]:
            documents = self.documents(task_id)
            for document in documents:
                await self.board.publish(task_id, document.publication.actor, document.publication.publish_request())
            await self.storage.update_agent(task_id, "scout", metadata={"intel_imported_count": len(documents)})
            return len(documents)
