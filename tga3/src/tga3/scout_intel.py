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


class ScoutIntelMergeInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    task_id: UUID
    key: str = Field(min_length=1, max_length=160)
    claim: str = Field(min_length=1, max_length=2000)
    detail: str = Field(default="", max_length=8000)
    topic: str = Field(default="general", min_length=1, max_length=200)
    merged_from_keys: list[str] = Field(min_length=1)


class ScoutIntelDocument(ScoutIntelPublication):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = Field(default=2, ge=2, le=3)
    sequence: int = Field(ge=1)
    key: str
    evidence_paths: list[str] = Field(default_factory=list)
    merged_from_keys: list[str] = Field(default_factory=list)

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

    def directory(self, task_id: UUID, *, merged: bool = False) -> Path:
        return self.config.task_path(task_id, "intels", "scout") / ("merged" if merged else "raw")

    def documents(self, task_id: UUID, *, merged: bool = False) -> list[ScoutIntelDocument]:
        result = []
        paths = list(self.directory(task_id, merged=merged).glob("*.json"))
        # Existing tasks keep their v1/v2 extraction files in the parent directory.
        if not merged:
            paths.extend(self.directory(task_id).parent.glob("*.json"))
        for path in sorted(paths):
            if path.is_symlink():
                raise ContractError("Intel JSON must be a regular file")
            document = ScoutIntelDocument.model_validate_json(path.read_bytes())
            if (document.task_id != task_id
                    or document.publication.actor.agent_id != "scout"
                    or document.publication.idempotency_key != (
                        f"scout-merged-json:{document.key}" if merged else f"scout-json:{document.key}")
                    or bool(document.merged_from_keys) != merged):
                raise ContractError("Intel JSON does not belong to this task")
            result.append(document)
        if len({document.key for document in result}) != len(result):
            raise ContractError("Intel keys must be unique")
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
            self._save(request.task_id, document)
            await self.storage.update_agent(request.task_id, "scout", metadata={"intel_file_count": sequence})
            return self.receipt(request, sequence)

    @staticmethod
    def filename(key: str, sequence: int) -> str:
        return f"{sequence:05d}-{hashlib.sha256(key.encode()).hexdigest()[:16]}.json"

    def receipt(self, request: ScoutIntelInput | ScoutIntelMergeInput, sequence: int) -> dict:
        stage = "merged" if isinstance(request, ScoutIntelMergeInput) else "raw"
        return {"saved": True, "published": False,
                "path": f"/intels/{stage}/{self.filename(request.key, sequence)}", "sequence": sequence}

    def _save(self, task_id: UUID, document: ScoutIntelDocument, *, merged: bool = False) -> None:
        directory = self.directory(task_id, merged=merged)
        directory.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory, delete=False) as output:
                temporary = Path(output.name)
                output.write(document.model_dump_json(indent=2, exclude_defaults=False))
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, directory / self.filename(document.key, document.sequence))
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)

    def validate_extraction(self, task_id: UUID) -> dict:
        actions = read_action_log(self.config.task_path(task_id, "logs", "scout"),
                                  task_id=task_id, agent_id="scout")["actions"]
        records = {action["action_id"] for action in actions}
        covered = {source for document in self.documents(task_id) for source in document.source_action_ids}
        if records != covered:
            raise ContractError(f"Scout extraction action coverage mismatch: missing={sorted(records - covered)}, "
                                f"unknown={sorted(covered - records)}")
        return {"action_count": len(records), "action_covered_count": len(records), "action_uncovered": []}

    async def merge(self, request: ScoutIntelMergeInput) -> dict:
        async with self.locks[request.task_id]:
            task = await self.storage.get_task(request.task_id)
            run = await self.storage.get_agent(request.task_id, "scout")
            if task.state != RunState.SCOUTING or run.metadata.get("scout_phase") != "merging":
                raise ContractError("Merged Intel JSON can be written only during Scout merging")
            self.validate_extraction(request.task_id)
            raw = {document.key: document for document in self.documents(request.task_id)}
            keys = sorted(set(request.merged_from_keys))
            if set(keys) - raw.keys():
                raise ContractError(f"Unknown raw Intel keys: {sorted(set(keys) - raw.keys())}")
            documents = self.documents(request.task_id, merged=True)
            for document in documents:
                if document.key == request.key:
                    if (document.body.claim != request.claim or document.body.detail != request.detail
                            or document.topic != request.topic or document.merged_from_keys != keys):
                        raise ConflictError("Merged Intel key already belongs to a different result")
                    return self.receipt(request, document.sequence)
            assigned = {key for document in documents for key in document.merged_from_keys}
            if assigned & set(keys):
                raise ConflictError(f"Raw Intel already merged: {sorted(assigned & set(keys))}")
            originals = [raw[key] for key in keys]
            references = {str(ref.artifact_id): ref for document in originals for ref in document.artifact_refs}
            document = ScoutIntelDocument(
                schema_version=3, task_id=request.task_id, actor=originals[0].actor, kind="intel",
                topic=request.topic, body={"claim": request.claim, "detail": request.detail},
                idempotency_key=f"scout-merged-json:{request.key}", key=request.key,
                sequence=max((item.sequence for item in documents), default=0) + 1,
                merged_from_keys=keys,
                source_action_ids=sorted({source for item in originals for source in item.source_action_ids}),
                evidence_paths=sorted({path for item in originals for path in item.evidence_paths}),
                artifact_refs=list(references.values()),
            )
            self._save(request.task_id, document, merged=True)
            await self.storage.update_agent(request.task_id, "scout", metadata={
                "intel_merged_count": document.sequence,
                "intel_raw_covered_count": len(assigned | set(keys)),
            })
            return self.receipt(request, document.sequence)

    def validate_merge(self, task_id: UUID) -> dict:
        coverage = self.validate_extraction(task_id)
        raw = {document.key: document for document in self.documents(task_id)}
        documents = self.documents(task_id, merged=True)
        keys = [key for document in documents for key in document.merged_from_keys]
        if set(keys) != raw.keys() or len(keys) != len(set(keys)):
            raise ContractError(f"Scout merge must cover each raw Intel exactly once: "
                                f"missing={sorted(raw.keys() - set(keys))}, unknown={sorted(set(keys) - raw.keys())}")
        for document in documents:
            originals = [raw[key] for key in document.merged_from_keys]
            if (set(document.source_action_ids) != {s for item in originals for s in item.source_action_ids}
                    or set(document.evidence_paths) != {p for item in originals for p in item.evidence_paths}
                    or {ref.model_dump_json() for ref in document.artifact_refs}
                    != {ref.model_dump_json() for item in originals for ref in item.artifact_refs}):
                raise ContractError("Merged Intel provenance differs from its original Intel")
        return {**coverage, "intel_file_count": len(raw), "intel_merged_count": len(documents),
                "intel_raw_covered_count": len(keys), "intel_raw_uncovered": []}

    async def flush(self, task_id: UUID) -> int:
        async with self.locks[task_id]:
            coverage = self.validate_merge(task_id)
            documents = self.documents(task_id, merged=True)
            for document in documents:
                await self.board.publish(task_id, document.publication.actor, document.publication.publish_request())
            await self.storage.update_agent(task_id, "scout", metadata={
                **coverage, "intel_imported_count": len(documents),
            })
            return len(documents)
