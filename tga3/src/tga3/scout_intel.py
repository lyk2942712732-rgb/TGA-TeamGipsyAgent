"""Aggregate Scout Intel per work phase; register evidence only at final import."""

import asyncio
import hashlib
import json
import os
from collections import defaultdict
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Annotated, Literal
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field

from .action_logs import read_action_log, resolve_action_sources
from .blackboard import Blackboard
from .config import TGA3Config
from .domain import Actor, FindingBody, PublishRequest, RunState, ScoutActor
from .errors import ConflictError, ContractError
from .evidence import evidence_files, register_evidence

RawIntelID = Annotated[str, Field(pattern=r"^scout-raw-intel-[a-f0-9]{32}$")]
INTEL_FILENAME = "scout-intels.json"


class ScoutIntelInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    task_id: UUID
    claim: str = Field(min_length=1, max_length=2000)
    detail: str = Field(default="", max_length=8000)
    topic: str = Field(default="general", min_length=1, max_length=200)
    evidence_paths: list[str] = Field(default_factory=list)
    source_action_ids: list[str] = Field(default_factory=list, max_length=100)


class ScoutIntelMergeInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    task_id: UUID
    claim: str | None = Field(default=None, min_length=1, max_length=2000)
    detail: str | None = Field(default=None, max_length=8000)
    topic: str | None = Field(default=None, min_length=1, max_length=200)
    merged_from_ids: list[RawIntelID] = Field(min_length=1)


class ScoutIntelDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^scout-(raw|merged)-intel-[a-f0-9]{32}$")
    topic: str
    body: FindingBody
    source_action_ids: list[str] = Field(default_factory=list)
    evidence_paths: list[str] = Field(default_factory=list)
    merged_from_ids: list[RawIntelID] = Field(default_factory=list)


class ScoutIntelCollection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[4] = 4
    task_id: UUID
    actor: ScoutActor
    stage: Literal["raw", "merged"]
    intels: list[ScoutIntelDocument] = Field(default_factory=list)


def legacy_intel_id(task_id: UUID, stage: str, key: str) -> str:
    return f"scout-{stage}-intel-" + uuid5(task_id, f"legacy-intel:{stage}:{key}").hex


def read_intels(directory: Path, task_id: UUID, *, merged: bool = False) -> ScoutIntelCollection | None:
    stage = "merged" if merged else "raw"
    target = directory / INTEL_FILENAME
    if target.exists():
        if target.is_symlink():
            raise ContractError("Intel JSON must be a regular file")
        collection = ScoutIntelCollection.model_validate_json(target.read_bytes())
    else:
        # Read old tasks without rewriting them; deployment migrates their files with backups.
        paths = list(directory.glob("*.json"))
        if not merged:
            paths.extend(directory.parent.glob("*.json"))
        if not paths:
            return None
        originals = []
        actor = None
        for path in paths:
            if path.is_symlink():
                raise ContractError("Intel JSON must be a regular file")
            value = json.loads(path.read_bytes())
            publication = value.get("publication", value)
            request = value.get("input", value)
            if (publication["task_id"] != str(task_id) or publication["actor"]["agent_id"] != "scout"
                    or bool(request.get("merged_from_keys")) != merged):
                raise ContractError("Legacy Intel JSON does not belong to this task/phase")
            current_actor = ScoutActor.model_validate(publication["actor"])
            if actor is not None and current_actor != actor:
                raise ContractError("Legacy Intel has inconsistent actor identity")
            actor = current_actor
            originals.append((value["sequence"], ScoutIntelDocument(
                id=legacy_intel_id(task_id, stage, request["key"]), topic=publication["topic"],
                body=publication["body"], source_action_ids=publication.get("source_action_ids", []),
                evidence_paths=request.get("evidence_paths", []),
                merged_from_ids=[legacy_intel_id(task_id, "raw", key)
                                 for key in request.get("merged_from_keys", [])],
            )))
        collection = ScoutIntelCollection(task_id=task_id, actor=actor, stage=stage,
                                          intels=[item for _, item in sorted(originals, key=lambda pair: pair[0])])
    if collection.task_id != task_id or collection.stage != stage or collection.actor.agent_id != "scout":
        raise ContractError("Intel JSON does not belong to this task/phase")
    ids = [item.id for item in collection.intels]
    if len(ids) != len(set(ids)):
        raise ContractError("Intel IDs must be unique")
    for item in collection.intels:
        if not item.id.startswith(f"scout-{stage}-intel-") or bool(item.merged_from_ids) != merged:
            raise ContractError("Intel ID/source prefix does not match its phase")
    return collection


def save_intels(directory: Path, collection: ScoutIntelCollection) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / INTEL_FILENAME
    if target.is_symlink():
        raise ContractError("Intel JSON must be a regular file")
    payload = collection.model_dump(mode="json")
    if collection.stage == "raw":
        for item in payload["intels"]:
            item.pop("merged_from_ids")
    temporary = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory, delete=False) as output:
            temporary = Path(output.name)
            output.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


class ScoutIntelStore:
    def __init__(self, config: TGA3Config, board: Blackboard):
        self.config, self.board, self.storage = config, board, board.storage
        self.locks = defaultdict(asyncio.Lock)

    def directory(self, task_id: UUID, *, merged: bool = False) -> Path:
        return self.config.task_path(task_id, "intels", "scout") / ("merged" if merged else "raw")

    def documents(self, task_id: UUID, *, merged: bool = False) -> list[ScoutIntelDocument]:
        collection = read_intels(self.directory(task_id, merged=merged), task_id, merged=merged)
        return collection.intels if collection else []

    async def _collection(self, task_id: UUID, *, merged: bool) -> ScoutIntelCollection:
        collection = read_intels(self.directory(task_id, merged=merged), task_id, merged=merged)
        if collection is not None:
            return collection
        run = await self.storage.get_agent(task_id, "scout")
        configured = self.config.agents.agents["scout"]
        return ScoutIntelCollection(task_id=task_id, stage="merged" if merged else "raw", actor=ScoutActor(
            agent_id="scout", display_name=configured.display_name, sdk=run.sdk, model=run.model_id,
        ))

    @staticmethod
    def _id(task_id: UUID, stage: str, payload: dict) -> str:
        digest = hashlib.sha256(json.dumps({"task_id": str(task_id), **payload},
                                           ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:32]
        return f"scout-{stage}-intel-{digest}"

    @staticmethod
    def receipt(document: ScoutIntelDocument, *, merged: bool) -> dict:
        return {"saved": True, "published": False, "id": document.id,
                "path": f"/intels/{'merged' if merged else 'raw'}/{INTEL_FILENAME}"}

    async def write(self, request: ScoutIntelInput) -> dict:
        async with self.locks[request.task_id]:
            task = await self.storage.get_task(request.task_id)
            run = await self.storage.get_agent(request.task_id, "scout")
            if task.state != RunState.SCOUTING or run.metadata.get("scout_phase") != "extracting":
                raise ContractError("Intel JSON can be written only during Scout extraction")
            root = self.config.task_path(request.task_id, "workspaces", "scout")
            paths = sorted(evidence_files(root, request.evidence_paths))
            sources = sorted(resolve_action_sources(self.config.task_path(request.task_id, "logs", "scout"),
                                                    request.task_id, "scout", request.source_action_ids, paths))
            payload = {"topic": request.topic, "body": {"claim": request.claim, "detail": request.detail},
                       "source_action_ids": sources, "evidence_paths": paths}
            document = ScoutIntelDocument(id=self._id(request.task_id, "raw", payload), **payload)
            collection = await self._collection(request.task_id, merged=False)
            if document.id not in {item.id for item in collection.intels}:
                collection.intels.append(document)
                save_intels(self.directory(request.task_id), collection)
                await self.storage.update_agent(request.task_id, "scout", metadata={
                    "intel_file_count": len(collection.intels),
                })
            return self.receipt(document, merged=False)

    async def write_action(self, task_id: UUID, action_id: str, claim: str, detail: str, topic: str,
                           selected_paths: list[str] | None = None) -> dict:
        actions = read_action_log(self.config.task_path(task_id, "logs", "scout"),
                                  task_id=task_id, agent_id="scout")["actions"]
        action = next((item for item in actions if item["action_id"] == action_id), None)
        if action is None or action["status"] not in {"completed", "failed", "interrupted"}:
            raise ContractError("Use a returned action ID from this Scout's runtime materials")
        artifact = action.get("artifact", {})
        defaults = [path for path in [artifact.get("output_path"), *artifact.get("attachments", [])] if path]
        if selected_paths is not None and (not selected_paths or set(selected_paths) - set(defaults)):
            raise ContractError("Explicit evidence must be a nonempty subset of this action's recorded files")
        return await self.write(ScoutIntelInput(task_id=task_id, claim=claim, detail=detail, topic=topic,
            source_action_ids=[action_id], evidence_paths=defaults if selected_paths is None else selected_paths))

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
            raw = {document.id: document for document in self.documents(request.task_id)}
            ids = sorted(set(request.merged_from_ids))
            if set(ids) - raw.keys():
                raise ContractError(f"Unknown raw Intel IDs: {sorted(set(ids) - raw.keys())}")
            collection = await self._collection(request.task_id, merged=True)
            originals = [raw[intel_id] for intel_id in ids]
            if len(originals) == 1:
                original = originals[0]
                if any(value is not None and value != expected for value, expected in (
                    (request.claim, original.body.claim), (request.detail, original.body.detail),
                    (request.topic, original.topic),
                )):
                    raise ContractError("Singleton Intel is copied verbatim; submit only its original ID")
                body, topic = original.body.model_dump(), original.topic
            else:
                if request.claim is None:
                    raise ContractError("Multiple Intel require an AI-written claim; add detail when needed")
                body = {"claim": request.claim, "detail": request.detail or ""}
                topic = request.topic or "general"
            payload = {"topic": topic, "body": body,
                       "merged_from_ids": ids,
                       "source_action_ids": sorted({s for item in originals for s in item.source_action_ids}),
                       "evidence_paths": sorted({p for item in originals for p in item.evidence_paths})}
            document = ScoutIntelDocument(id=self._id(request.task_id, "merged", payload), **payload)
            if document.id in {item.id for item in collection.intels}:
                return self.receipt(document, merged=True)
            assigned = {intel_id for item in collection.intels for intel_id in item.merged_from_ids}
            if assigned & set(ids):
                raise ConflictError(f"Raw Intel already merged: {sorted(assigned & set(ids))}")
            collection.intels.append(document)
            save_intels(self.directory(request.task_id, merged=True), collection)
            await self.storage.update_agent(request.task_id, "scout", metadata={
                "intel_merged_count": len(collection.intels), "intel_raw_covered_count": len(assigned | set(ids)),
            })
            return self.receipt(document, merged=True)

    def validate_merge(self, task_id: UUID) -> dict:
        coverage = self.validate_extraction(task_id)
        raw = {document.id: document for document in self.documents(task_id)}
        documents = self.documents(task_id, merged=True)
        ids = [intel_id for document in documents for intel_id in document.merged_from_ids]
        if set(ids) != raw.keys() or len(ids) != len(set(ids)):
            raise ContractError(f"Scout merge must cover each raw Intel exactly once: "
                                f"missing={sorted(raw.keys() - set(ids))}, unknown={sorted(set(ids) - raw.keys())}")
        for document in documents:
            originals = [raw[intel_id] for intel_id in document.merged_from_ids]
            if (set(document.source_action_ids) != {s for item in originals for s in item.source_action_ids}
                    or set(document.evidence_paths) != {p for item in originals for p in item.evidence_paths}):
                raise ContractError("Merged Intel provenance differs from its original Intel")
        return {**coverage, "intel_file_count": len(raw), "intel_merged_count": len(documents),
                "intel_raw_covered_count": len(ids), "intel_raw_uncovered": []}

    async def flush(self, task_id: UUID) -> int:
        async with self.locks[task_id]:
            coverage = self.validate_merge(task_id)
            collection = read_intels(self.directory(task_id, merged=True), task_id, merged=True)
            imported = {entry.idempotency_key for entry in await self.storage.list_entries(task_id, limit=10_000)
                        if entry.actor.agent_id == "scout" and entry.kind == "intel"}
            for document in collection.intels if collection else []:
                retry_key = f"scout-intel:{document.id}"
                if retry_key in imported:
                    continue
                references = await register_evidence(self.board, task_id, Actor.model_validate(collection.actor),
                    self.config.task_path(task_id, "workspaces", "scout"), document.evidence_paths)
                await self.board.publish(task_id, collection.actor, PublishRequest(
                    kind="intel", topic=document.topic, body=document.body.model_dump(),
                    artifact_refs=references, source_action_ids=document.source_action_ids,
                    idempotency_key=retry_key,
                ))
            await self.storage.update_agent(task_id, "scout", metadata={
                **coverage, "intel_imported_count": len(collection.intels) if collection else 0,
            })
            return len(collection.intels) if collection else 0
