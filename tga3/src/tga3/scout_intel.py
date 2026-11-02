"""Scout's extraction tool writes JSON files; only the coordinator imports them."""

import asyncio
import hashlib
import os
from collections import defaultdict
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .action_logs import read_action_log, resolve_action_sources
from .blackboard import Blackboard
from .config import TGA3Config
from .domain import Actor, RunState, ScoutIntelPublication
from .errors import ConflictError, ContractError
from .evidence import register_evidence


class ScoutIntelInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    task_id: UUID
    key: str = Field(min_length=1, max_length=160)
    claim: str = Field(min_length=1, max_length=2000)
    detail: str = Field(default="", max_length=8000)
    topic: str = Field(default="general", min_length=1, max_length=200)
    evidence_paths: list[str] = Field(default_factory=list, max_length=30)
    source_action_ids: list[str] = Field(default_factory=list, max_length=100)


class ScoutIntelDocument(ScoutIntelPublication):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = Field(default=2, ge=2, le=2)
    sequence: int = Field(ge=1)
    key: str
    evidence_paths: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def flatten_legacy_document(cls, value):
        if isinstance(value, dict) and "input" in value and "publication" in value:
            request = ScoutIntelInput.model_validate(value["input"])
            publication = ScoutIntelPublication.model_validate(value["publication"])
            return {**publication.model_dump(), "schema_version": 2, "sequence": value["sequence"],
                    "key": request.key, "evidence_paths": request.evidence_paths}
        return value

    @property
    def input(self) -> ScoutIntelInput:
        return ScoutIntelInput(task_id=self.task_id, key=self.key, claim=self.body.claim, detail=self.body.detail,
                               topic=self.topic, evidence_paths=self.evidence_paths,
                               source_action_ids=self.source_action_ids)

    @property
    def publication(self) -> ScoutIntelPublication:
        return ScoutIntelPublication.model_validate({field: getattr(self, field)
                                                     for field in ScoutIntelPublication.model_fields})


class ScoutIntelStore:
    def __init__(self, config: TGA3Config, board: Blackboard):
        self.config, self.board, self.storage = config, board, board.storage
        self.locks = defaultdict(asyncio.Lock)

    def directory(self, task_id: UUID) -> Path:
        return self.config.task_path(task_id, "intels", "scout")

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
            sources = resolve_action_sources(self.config.task_path(request.task_id, "logs", "scout"),
                                             request.task_id, "scout", request.source_action_ids,
                                             request.evidence_paths)
            request = request.model_copy(update={"source_action_ids": sources})
            documents = self.documents(request.task_id)
            for document in documents:
                if document.input.key == request.key:
                    if document.input != request:
                        raise ConflictError("Intel key already belongs to a different result")
                    return self.receipt(request, document.sequence)
            configured = self.config.agents.agents["scout"]
            actor = Actor(agent_id="scout", display_name=configured.display_name, role="scout",
                          sdk=run.sdk, model=run.model_id)
            root = self.config.task_path(request.task_id, "workspaces", "scout")
            references = await register_evidence(self.board, request.task_id, actor, root, request.evidence_paths)
            publication = ScoutIntelPublication(
                task_id=request.task_id, actor=actor.model_dump(), kind="intel", topic=request.topic,
                body={"claim": request.claim, "detail": request.detail}, artifact_refs=references,
                idempotency_key=f"scout-json:{request.key}",
                source_action_ids=request.source_action_ids,
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
            actions = read_action_log(self.config.task_path(task_id, "logs", "scout"),
                                      task_id=task_id, agent_id="scout")["actions"]
            records = {action["action_id"] for action in actions}
            covered = {source for document in documents for source in document.input.source_action_ids}
            await self.storage.update_agent(task_id, "scout", metadata={
                "intel_imported_count": len(documents), "action_count": len(records),
                "action_covered_count": len(records & covered),
                "action_uncovered": sorted(records - covered),
            })
            return len(documents)
