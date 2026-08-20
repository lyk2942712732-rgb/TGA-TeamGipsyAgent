"""Persistence contracts plus deterministic memory and PostgreSQL implementations."""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID, uuid4

from .domain import (
    Actor,
    AgentRun,
    Artifact,
    ArtifactRef,
    BlackboardEntry,
    DialogueKind,
    DialogueMessage,
    EntryKind,
    FinalCandidateBody,
    InputFile,
    PendingQuestion,
    PublishRequest,
    QABody,
    RunState,
    TaskRun,
    Writeup,
    utc_now,
)
from .errors import ConflictError, FindingRejectedError, NotFoundError


class Storage(Protocol):
    async def create_task(self, title: str, scene_id: str, task_id: UUID | None = None) -> TaskRun: ...
    async def get_task(self, task_id: UUID) -> TaskRun: ...
    async def list_tasks(self, *, limit: int = 200) -> list[TaskRun]: ...
    async def update_task(
        self, task_id: UUID, *, state: RunState | None = None, final_snapshot_seq: int | None = None
    ) -> TaskRun: ...
    async def upsert_agent(self, run: AgentRun) -> AgentRun: ...
    async def get_agent(self, task_id: UUID, agent_id: str) -> AgentRun: ...
    async def list_agents(self, task_id: UUID) -> list[AgentRun]: ...
    async def update_agent(self, task_id: UUID, agent_id: str, **changes: Any) -> AgentRun: ...
    async def register_artifact(self, artifact: Artifact) -> Artifact: ...
    async def register_input_file(self, item: InputFile) -> InputFile: ...
    async def publish(self, task_id: UUID, actor: Actor, request: PublishRequest) -> BlackboardEntry: ...
    async def list_entries(
        self, task_id: UUID, *, after_seq: int = 0, up_to_seq: int | None = None, limit: int = 500
    ) -> list[BlackboardEntry]: ...
    async def finding_links(self, task_id: UUID, finding_id: UUID) -> list[ArtifactRef]: ...
    async def append_dialogue(
        self,
        task_id: UUID,
        actor: Actor,
        kind: DialogueKind,
        text: str,
        *,
        channel_agent_id: str = "supervisor",
        payload: dict[str, Any] | None = None,
    ) -> DialogueMessage: ...
    async def list_dialogue(self, task_id: UUID, *, after_seq: int = 0, limit: int = 500) -> list[DialogueMessage]: ...
    async def create_question(self, question: PendingQuestion) -> PendingQuestion: ...
    async def get_question(self, question_id: UUID) -> PendingQuestion: ...
    async def answer_question(
        self,
        question_id: UUID,
        *,
        answer: str,
        attachment_ids: Sequence[UUID],
        user: Actor,
        idempotency_key: str,
    ) -> BlackboardEntry: ...
    async def save_writeup(self, task_id: UUID, snapshot_seq: int, path: str, sha256: str) -> UUID: ...
    async def latest_writeup(self, task_id: UUID) -> Writeup: ...


def _validate_publish(
    *,
    task_id: UUID,
    entries: Sequence[BlackboardEntry],
    artifacts: Sequence[Artifact],
    request: PublishRequest,
) -> None:
    if request.kind == EntryKind.FINDING:
        by_id = {a.id: a for a in artifacts}
        bad = [
            str(ref.artifact_id)
            for ref in request.artifact_refs
            if ref.artifact_id not in by_id
            or by_id[ref.artifact_id].task_id != task_id
            or not by_id[ref.artifact_id].available
            or not ref.locator.strip()
        ]
        if bad:
            raise FindingRejectedError(f"artifact references unavailable for this task: {', '.join(bad)}")
    if request.kind == EntryKind.FINAL_CANDIDATE:
        body = FinalCandidateBody.model_validate(request.body)
        valid = {entry.id for entry in entries if entry.kind == EntryKind.FINDING}
        missing = [str(entry_id) for entry_id in body.finding_ids if entry_id not in valid]
        if missing:
            raise FindingRejectedError(f"final candidate references unknown findings: {', '.join(missing)}")


class InMemoryStorage:
    """Fast behavioural reference used by tests and local protocol development."""

    def __init__(self) -> None:
        self.tasks: dict[UUID, TaskRun] = {}
        self.agents: dict[tuple[UUID, str], AgentRun] = {}
        self.artifacts: dict[UUID, Artifact] = {}
        self.input_files: dict[UUID, InputFile] = {}
        self.entries: dict[UUID, list[BlackboardEntry]] = defaultdict(list)
        self.links: dict[UUID, list[ArtifactRef]] = defaultdict(list)
        self.dialogue: dict[UUID, list[DialogueMessage]] = defaultdict(list)
        self.questions: dict[UUID, PendingQuestion] = {}
        self.answers: dict[UUID, BlackboardEntry] = {}
        self.writeups: dict[UUID, Writeup] = {}
        self._locks: defaultdict[UUID, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def create_task(self, title: str, scene_id: str, task_id: UUID | None = None) -> TaskRun:
        run = TaskRun(id=task_id or uuid4(), title=title, scene_id=scene_id)
        if run.id in self.tasks:
            raise ConflictError(f"task already exists: {run.id}")
        self.tasks[run.id] = run
        return run

    async def get_task(self, task_id: UUID) -> TaskRun:
        try:
            return self.tasks[task_id]
        except KeyError as exc:
            raise NotFoundError(f"task not found: {task_id}") from exc

    async def list_tasks(self, *, limit: int = 200) -> list[TaskRun]:
        return sorted(self.tasks.values(), key=lambda item: item.created_at, reverse=True)[:limit]

    async def update_task(
        self, task_id: UUID, *, state: RunState | None = None, final_snapshot_seq: int | None = None
    ) -> TaskRun:
        async with self._locks[task_id]:
            run = await self.get_task(task_id)
            changes: dict[str, Any] = {"updated_at": utc_now()}
            if state is not None:
                changes["state"] = state
            if final_snapshot_seq is not None:
                changes["final_snapshot_seq"] = final_snapshot_seq
            run = run.model_copy(update=changes)
            self.tasks[task_id] = run
            return run

    async def upsert_agent(self, run: AgentRun) -> AgentRun:
        self.agents[(run.task_id, run.agent_id)] = run
        return run

    async def get_agent(self, task_id: UUID, agent_id: str) -> AgentRun:
        try:
            return self.agents[(task_id, agent_id)]
        except KeyError as exc:
            raise NotFoundError(f"agent not found: {task_id}/{agent_id}") from exc

    async def list_agents(self, task_id: UUID) -> list[AgentRun]:
        return [value for (tid, _), value in self.agents.items() if tid == task_id]

    async def update_agent(self, task_id: UUID, agent_id: str, **changes: Any) -> AgentRun:
        run = await self.get_agent(task_id, agent_id)
        allowed = set(AgentRun.model_fields) - {"task_id", "agent_id"}
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"unknown agent fields: {sorted(unknown)}")
        changes["updated_at"] = utc_now()
        run = AgentRun.model_validate({**run.model_dump(), **changes})
        self.agents[(task_id, agent_id)] = run
        return run

    async def register_artifact(self, artifact: Artifact) -> Artifact:
        await self.get_task(artifact.task_id)
        if artifact.id in self.artifacts:
            raise ConflictError(f"artifact already exists: {artifact.id}")
        self.artifacts[artifact.id] = artifact
        return artifact

    async def register_input_file(self, item: InputFile) -> InputFile:
        await self.get_task(item.task_id)
        if item.id in self.input_files:
            raise ConflictError(f"input file already exists: {item.id}")
        self.input_files[item.id] = item
        return item

    async def publish(self, task_id: UUID, actor: Actor, request: PublishRequest) -> BlackboardEntry:
        async with self._locks[task_id]:
            task = await self.get_task(task_id)
            for existing in self.entries[task_id]:
                if existing.actor.agent_id == actor.agent_id and existing.idempotency_key == request.idempotency_key:
                    return existing
            _validate_publish(
                task_id=task_id,
                entries=self.entries[task_id],
                artifacts=list(self.artifacts.values()),
                request=request,
            )
            entry = BlackboardEntry(
                task_id=task_id,
                seq=task.blackboard_seq + 1,
                actor=actor,
                kind=request.kind,
                topic=request.topic,
                body=request.validated_body().model_dump(mode="json"),
                idempotency_key=request.idempotency_key,
            )
            self.entries[task_id].append(entry)
            if request.kind == EntryKind.FINDING:
                self.links[entry.id] = list(request.artifact_refs)
            self.tasks[task_id] = task.model_copy(update={"blackboard_seq": entry.seq, "updated_at": utc_now()})
            return entry

    async def list_entries(
        self, task_id: UUID, *, after_seq: int = 0, up_to_seq: int | None = None, limit: int = 500
    ) -> list[BlackboardEntry]:
        await self.get_task(task_id)
        upper = up_to_seq if up_to_seq is not None else 2**63 - 1
        return [entry for entry in self.entries[task_id] if after_seq < entry.seq <= upper][:limit]

    async def finding_links(self, task_id: UUID, finding_id: UUID) -> list[ArtifactRef]:
        entry = next((entry for entry in self.entries[task_id] if entry.id == finding_id), None)
        if entry is None or entry.kind != EntryKind.FINDING:
            raise NotFoundError(f"finding not found: {finding_id}")
        return list(self.links[finding_id])

    async def append_dialogue(
        self,
        task_id: UUID,
        actor: Actor,
        kind: DialogueKind,
        text: str,
        *,
        channel_agent_id: str = "supervisor",
        payload: dict[str, Any] | None = None,
    ) -> DialogueMessage:
        async with self._locks[task_id]:
            task = await self.get_task(task_id)
            message = DialogueMessage(
                task_id=task_id,
                seq=task.dialogue_seq + 1,
                actor=actor,
                kind=kind,
                text=text,
                channel_agent_id=channel_agent_id,
                payload=payload or {},
            )
            self.dialogue[task_id].append(message)
            self.tasks[task_id] = task.model_copy(update={"dialogue_seq": message.seq, "updated_at": utc_now()})
            return message

    async def list_dialogue(self, task_id: UUID, *, after_seq: int = 0, limit: int = 500) -> list[DialogueMessage]:
        await self.get_task(task_id)
        return [message for message in self.dialogue[task_id] if message.seq > after_seq][:limit]

    async def create_question(self, question: PendingQuestion) -> PendingQuestion:
        await self.get_task(question.task_id)
        if question.id in self.questions:
            raise ConflictError(f"question already exists: {question.id}")
        self.questions[question.id] = question
        return question

    async def get_question(self, question_id: UUID) -> PendingQuestion:
        try:
            return self.questions[question_id]
        except KeyError as exc:
            raise NotFoundError(f"question not found: {question_id}") from exc

    async def answer_question(
        self,
        question_id: UUID,
        *,
        answer: str,
        attachment_ids: Sequence[UUID],
        user: Actor,
        idempotency_key: str,
    ) -> BlackboardEntry:
        question = await self.get_question(question_id)
        async with self._locks[question.task_id]:
            if question.state == "answered":
                return self.answers[question_id]
            task = await self.get_task(question.task_id)
            body = QABody(
                question=question.question,
                answer=answer,
                origin_type="supervisor" if question.origin.role == "supervisor" else "agent",
                origin_agent_id=question.origin.agent_id,
                answer_attachment_ids=list(attachment_ids),
            )
            entry = BlackboardEntry(
                task_id=question.task_id,
                seq=task.blackboard_seq + 1,
                actor=question.asked_by,
                kind=EntryKind.QA,
                topic="q&a",
                body=body.model_dump(mode="json"),
                idempotency_key=idempotency_key,
            )
            message = DialogueMessage(
                task_id=question.task_id,
                seq=task.dialogue_seq + 1,
                actor=user,
                kind=DialogueKind.USER_MESSAGE,
                text=answer,
                payload={"question_id": str(question_id), "attachment_ids": [str(x) for x in attachment_ids]},
            )
            self.entries[question.task_id].append(entry)
            self.dialogue[question.task_id].append(message)
            self.questions[question_id] = question.model_copy(update={"state": "answered"})
            self.answers[question_id] = entry
            self.tasks[question.task_id] = task.model_copy(
                update={"blackboard_seq": entry.seq, "dialogue_seq": message.seq, "updated_at": utc_now()}
            )
            return entry

    async def save_writeup(self, task_id: UUID, snapshot_seq: int, path: str, sha256: str) -> UUID:
        await self.get_task(task_id)
        writeup = Writeup(
            task_id=task_id,
            snapshot_seq=snapshot_seq,
            storage_path=path,
            sha256=sha256,
        )
        self.writeups[writeup.id] = writeup
        return writeup.id

    async def latest_writeup(self, task_id: UUID) -> Writeup:
        await self.get_task(task_id)
        values = [item for item in self.writeups.values() if item.task_id == task_id]
        if not values:
            raise NotFoundError(f"writeup not found for task: {task_id}")
        return max(values, key=lambda item: item.created_at)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _object(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _task(row: Any) -> TaskRun:
    return TaskRun.model_validate(dict(row))


def _agent(row: Any) -> AgentRun:
    data = dict(row)
    return AgentRun.model_validate({key: data[key] for key in AgentRun.model_fields})


def _entry(row: Any) -> BlackboardEntry:
    data = dict(row)
    data["actor"] = _object(data["actor"])
    data["body"] = _object(data["body"])
    return BlackboardEntry.model_validate(data)


def _dialogue(row: Any) -> DialogueMessage:
    data = dict(row)
    data["actor"] = _object(data["actor"])
    data["payload"] = _object(data["payload"])
    return DialogueMessage.model_validate(data)


class PostgresStorage:
    """PostgreSQL store. Per-task advisory locks serialize sequence allocation."""

    def __init__(self, pool: Any) -> None:
        self.pool = pool

    @classmethod
    async def connect(cls, dsn: str, *, min_size: int = 1, max_size: int = 10) -> PostgresStorage:
        import asyncpg

        return cls(await asyncpg.create_pool(dsn, min_size=min_size, max_size=max_size))

    async def close(self) -> None:
        await self.pool.close()

    async def initialize(self, schema_path: Path) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(schema_path.read_text(encoding="utf-8"))

    @staticmethod
    async def _lock(conn: Any, task_id: UUID) -> None:
        await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", str(task_id))

    async def create_task(self, title: str, scene_id: str, task_id: UUID | None = None) -> TaskRun:
        task_id = task_id or uuid4()
        row = await self.pool.fetchrow(
            "INSERT INTO task_runs(id,title,scene_id,state) VALUES($1,$2,$3,$4) RETURNING *",
            task_id,
            title,
            scene_id,
            RunState.CREATED.value,
        )
        return _task(row)

    async def get_task(self, task_id: UUID) -> TaskRun:
        row = await self.pool.fetchrow("SELECT * FROM task_runs WHERE id=$1", task_id)
        if row is None:
            raise NotFoundError(f"task not found: {task_id}")
        return _task(row)

    async def list_tasks(self, *, limit: int = 200) -> list[TaskRun]:
        rows = await self.pool.fetch(
            "SELECT * FROM task_runs ORDER BY created_at DESC LIMIT $1",
            limit,
        )
        return [_task(row) for row in rows]

    async def update_task(
        self, task_id: UUID, *, state: RunState | None = None, final_snapshot_seq: int | None = None
    ) -> TaskRun:
        row = await self.pool.fetchrow(
            """UPDATE task_runs SET state=COALESCE($2,state),
               final_snapshot_seq=COALESCE($3,final_snapshot_seq), updated_at=now()
               WHERE id=$1 RETURNING *""",
            task_id,
            state.value if state else None,
            final_snapshot_seq,
        )
        if row is None:
            raise NotFoundError(f"task not found: {task_id}")
        return _task(row)

    async def upsert_agent(self, run: AgentRun) -> AgentRun:
        row = await self.pool.fetchrow(
            """INSERT INTO agent_runs(task_id,agent_id,sdk,desired_state,actual_state,provider_id,model_id,
               container_id,session_id,last_error,updated_at)
               VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
               ON CONFLICT(task_id,agent_id) DO UPDATE SET sdk=EXCLUDED.sdk,
               desired_state=EXCLUDED.desired_state,actual_state=EXCLUDED.actual_state,
               provider_id=EXCLUDED.provider_id,model_id=EXCLUDED.model_id,
               container_id=EXCLUDED.container_id,session_id=EXCLUDED.session_id,
               last_error=EXCLUDED.last_error,updated_at=EXCLUDED.updated_at RETURNING *""",
            run.task_id,
            run.agent_id,
            run.sdk,
            run.desired_state.value,
            run.actual_state.value,
            run.provider_id,
            run.model_id,
            run.container_id,
            run.session_id,
            run.last_error,
            run.updated_at,
        )
        return _agent(row)

    async def get_agent(self, task_id: UUID, agent_id: str) -> AgentRun:
        row = await self.pool.fetchrow("SELECT * FROM agent_runs WHERE task_id=$1 AND agent_id=$2", task_id, agent_id)
        if row is None:
            raise NotFoundError(f"agent not found: {task_id}/{agent_id}")
        return _agent(row)

    async def list_agents(self, task_id: UUID) -> list[AgentRun]:
        return [
            _agent(row)
            for row in await self.pool.fetch("SELECT * FROM agent_runs WHERE task_id=$1 ORDER BY agent_id", task_id)
        ]

    async def update_agent(self, task_id: UUID, agent_id: str, **changes: Any) -> AgentRun:
        current = await self.get_agent(task_id, agent_id)
        updated = AgentRun.model_validate({**current.model_dump(), **changes, "updated_at": utc_now()})
        return await self.upsert_agent(updated)

    async def register_artifact(self, artifact: Artifact) -> Artifact:
        row = await self.pool.fetchrow(
            """INSERT INTO artifacts(
                   id,task_id,created_by,name,storage_path,media_type,sha256,size_bytes,available,created_at
               )
               VALUES($1,$2,$3::jsonb,$4,$5,$6,$7,$8,$9,$10) RETURNING *""",
            artifact.id,
            artifact.task_id,
            _json(artifact.created_by.model_dump(mode="json")),
            artifact.name,
            artifact.storage_path,
            artifact.media_type,
            artifact.sha256,
            artifact.size_bytes,
            artifact.available,
            artifact.created_at,
        )
        data = dict(row)
        data["created_by"] = _object(data["created_by"])
        return Artifact.model_validate(data)

    async def register_input_file(self, item: InputFile) -> InputFile:
        row = await self.pool.fetchrow(
            """INSERT INTO input_files(id,task_id,name,storage_path,media_type,sha256,size_bytes,created_at)
               VALUES($1,$2,$3,$4,$5,$6,$7,$8) RETURNING *""",
            item.id,
            item.task_id,
            item.name,
            item.storage_path,
            item.media_type,
            item.sha256,
            item.size_bytes,
            item.created_at,
        )
        return InputFile.model_validate(dict(row))

    async def publish(self, task_id: UUID, actor: Actor, request: PublishRequest) -> BlackboardEntry:
        async with self.pool.acquire() as conn, conn.transaction():
            await self._lock(conn, task_id)
            existing = await conn.fetchrow(
                "SELECT * FROM blackboard_entries WHERE task_id=$1 AND actor_id=$2 AND idempotency_key=$3",
                task_id,
                actor.agent_id,
                request.idempotency_key,
            )
            if existing:
                return _entry(existing)
            if request.kind == EntryKind.FINDING:
                ids = [ref.artifact_id for ref in request.artifact_refs]
                rows = await conn.fetch(
                    "SELECT id FROM artifacts WHERE task_id=$1 AND available AND id=ANY($2::uuid[])", task_id, ids
                )
                if {row["id"] for row in rows} != set(ids):
                    raise FindingRejectedError("one or more artifacts are unavailable or belong to another task")
            if request.kind == EntryKind.FINAL_CANDIDATE:
                ids = FinalCandidateBody.model_validate(request.body).finding_ids
                rows = await conn.fetch(
                    "SELECT id FROM blackboard_entries WHERE task_id=$1 AND kind='finding' AND id=ANY($2::uuid[])",
                    task_id,
                    ids,
                )
                if {row["id"] for row in rows} != set(ids):
                    raise FindingRejectedError("final candidate references unknown findings")
            seq = await conn.fetchval(
                """UPDATE task_runs SET blackboard_seq=blackboard_seq+1,updated_at=now()
                   WHERE id=$1 RETURNING blackboard_seq""",
                task_id,
            )
            if seq is None:
                raise NotFoundError(f"task not found: {task_id}")
            entry_id = uuid4()
            row = await conn.fetchrow(
                """INSERT INTO blackboard_entries(id,task_id,seq,actor,actor_id,kind,topic,body,idempotency_key)
                   VALUES($1,$2,$3,$4::jsonb,$5,$6,$7,$8::jsonb,$9) RETURNING *""",
                entry_id,
                task_id,
                seq,
                _json(actor.model_dump(mode="json")),
                actor.agent_id,
                request.kind.value,
                request.topic,
                _json(request.validated_body().model_dump(mode="json")),
                request.idempotency_key,
            )
            for ref in request.artifact_refs:
                await conn.execute(
                    """INSERT INTO finding_artifact_links(
                           finding_entry_id,artifact_id,locator,description
                       ) VALUES($1,$2,$3,$4)""",
                    entry_id,
                    ref.artifact_id,
                    ref.locator,
                    ref.description,
                )
            await conn.execute("SELECT pg_notify('tga3_blackboard',$1)", _json({"task_id": task_id, "latest_seq": seq}))
            return _entry(row)

    async def list_entries(
        self, task_id: UUID, *, after_seq: int = 0, up_to_seq: int | None = None, limit: int = 500
    ) -> list[BlackboardEntry]:
        rows = await self.pool.fetch(
            """SELECT * FROM blackboard_entries WHERE task_id=$1 AND seq>$2 AND ($3::bigint IS NULL OR seq<=$3)
               ORDER BY seq LIMIT $4""",
            task_id,
            after_seq,
            up_to_seq,
            limit,
        )
        return [_entry(row) for row in rows]

    async def finding_links(self, task_id: UUID, finding_id: UUID) -> list[ArtifactRef]:
        rows = await self.pool.fetch(
            """SELECT l.artifact_id,l.locator,l.description FROM finding_artifact_links l
               JOIN blackboard_entries b ON b.id=l.finding_entry_id
               WHERE b.task_id=$1 AND b.id=$2 AND b.kind='finding'""",
            task_id,
            finding_id,
        )
        if not rows:
            raise NotFoundError(f"finding not found: {finding_id}")
        return [ArtifactRef.model_validate(dict(row)) for row in rows]

    async def append_dialogue(
        self,
        task_id: UUID,
        actor: Actor,
        kind: DialogueKind,
        text: str,
        *,
        channel_agent_id: str = "supervisor",
        payload: dict[str, Any] | None = None,
    ) -> DialogueMessage:
        async with self.pool.acquire() as conn, conn.transaction():
            await self._lock(conn, task_id)
            seq = await conn.fetchval(
                "UPDATE task_runs SET dialogue_seq=dialogue_seq+1,updated_at=now() WHERE id=$1 RETURNING dialogue_seq",
                task_id,
            )
            if seq is None:
                raise NotFoundError(f"task not found: {task_id}")
            row = await conn.fetchrow(
                """INSERT INTO dialogue_messages(id,task_id,seq,channel_agent_id,actor,kind,text,payload)
                   VALUES($1,$2,$3,$4,$5::jsonb,$6,$7,$8::jsonb) RETURNING *""",
                uuid4(),
                task_id,
                seq,
                channel_agent_id,
                _json(actor.model_dump(mode="json")),
                kind.value,
                text,
                _json(payload or {}),
            )
            await conn.execute("SELECT pg_notify('tga3_dialogue',$1)", _json({"task_id": task_id, "latest_seq": seq}))
            return _dialogue(row)

    async def list_dialogue(self, task_id: UUID, *, after_seq: int = 0, limit: int = 500) -> list[DialogueMessage]:
        rows = await self.pool.fetch(
            "SELECT * FROM dialogue_messages WHERE task_id=$1 AND seq>$2 ORDER BY seq LIMIT $3",
            task_id,
            after_seq,
            limit,
        )
        return [_dialogue(row) for row in rows]

    async def create_question(self, question: PendingQuestion) -> PendingQuestion:
        await self.pool.execute(
            """INSERT INTO pending_questions(id,task_id,question,origin,asked_by,state,created_at)
               VALUES($1,$2,$3,$4::jsonb,$5::jsonb,$6,$7)""",
            question.id,
            question.task_id,
            question.question,
            _json(question.origin.model_dump(mode="json")),
            _json(question.asked_by.model_dump(mode="json")),
            question.state,
            question.created_at,
        )
        return question

    async def get_question(self, question_id: UUID) -> PendingQuestion:
        row = await self.pool.fetchrow("SELECT * FROM pending_questions WHERE id=$1", question_id)
        if row is None:
            raise NotFoundError(f"question not found: {question_id}")
        data = dict(row)
        data["origin"] = _object(data["origin"])
        data["asked_by"] = _object(data["asked_by"])
        return PendingQuestion.model_validate({key: data[key] for key in PendingQuestion.model_fields})

    async def answer_question(
        self,
        question_id: UUID,
        *,
        answer: str,
        attachment_ids: Sequence[UUID],
        user: Actor,
        idempotency_key: str,
    ) -> BlackboardEntry:
        async with self.pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow("SELECT * FROM pending_questions WHERE id=$1 FOR UPDATE", question_id)
            if row is None:
                raise NotFoundError(f"question not found: {question_id}")
            task_id = row["task_id"]
            await self._lock(conn, task_id)
            if row["state"] == "answered":
                existing = await conn.fetchrow(
                    """SELECT * FROM blackboard_entries
                       WHERE task_id=$1 AND actor_id='supervisor' AND idempotency_key=$2""",
                    task_id,
                    idempotency_key,
                )
                if existing is None:
                    raise ConflictError("question was already answered with another idempotency key")
                return _entry(existing)
            origin = Actor.model_validate(_object(row["origin"]))
            asked_by = Actor.model_validate(_object(row["asked_by"]))
            body = QABody(
                question=row["question"],
                answer=answer,
                origin_type="supervisor" if origin.role == "supervisor" else "agent",
                origin_agent_id=origin.agent_id,
                answer_attachment_ids=list(attachment_ids),
            )
            bb_seq = await conn.fetchval(
                """UPDATE task_runs
                   SET blackboard_seq=blackboard_seq+1,dialogue_seq=dialogue_seq+1,updated_at=now()
                   WHERE id=$1 RETURNING blackboard_seq""",
                task_id,
            )
            dialogue_seq = await conn.fetchval("SELECT dialogue_seq FROM task_runs WHERE id=$1", task_id)
            entry_row = await conn.fetchrow(
                """INSERT INTO blackboard_entries(id,task_id,seq,actor,actor_id,kind,topic,body,idempotency_key)
                   VALUES($1,$2,$3,$4::jsonb,$5,'qa','q&a',$6::jsonb,$7) RETURNING *""",
                uuid4(),
                task_id,
                bb_seq,
                _json(asked_by.model_dump(mode="json")),
                asked_by.agent_id,
                _json(body.model_dump(mode="json")),
                idempotency_key,
            )
            await conn.execute(
                """INSERT INTO dialogue_messages(id,task_id,seq,channel_agent_id,actor,kind,text,payload)
                   VALUES($1,$2,$3,'supervisor',$4::jsonb,$5,$6,$7::jsonb)""",
                uuid4(),
                task_id,
                dialogue_seq,
                _json(user.model_dump(mode="json")),
                DialogueKind.USER_MESSAGE.value,
                answer,
                _json({"question_id": str(question_id), "attachment_ids": [str(x) for x in attachment_ids]}),
            )
            await conn.execute(
                """UPDATE pending_questions SET state='answered',answer=$2,answer_attachment_ids=$3::jsonb,
                   answered_at=now() WHERE id=$1""",
                question_id,
                answer,
                _json([str(x) for x in attachment_ids]),
            )
            return _entry(entry_row)

    async def save_writeup(self, task_id: UUID, snapshot_seq: int, path: str, sha256: str) -> UUID:
        writeup_id = uuid4()
        await self.pool.execute(
            "INSERT INTO writeups(id,task_id,snapshot_seq,storage_path,sha256) VALUES($1,$2,$3,$4,$5)",
            writeup_id,
            task_id,
            snapshot_seq,
            path,
            sha256,
        )
        return writeup_id

    async def latest_writeup(self, task_id: UUID) -> Writeup:
        row = await self.pool.fetchrow(
            "SELECT * FROM writeups WHERE task_id=$1 ORDER BY created_at DESC LIMIT 1",
            task_id,
        )
        if row is None:
            raise NotFoundError(f"writeup not found for task: {task_id}")
        return Writeup.model_validate(dict(row))


__all__ = ["InMemoryStorage", "PostgresStorage", "Storage"]
