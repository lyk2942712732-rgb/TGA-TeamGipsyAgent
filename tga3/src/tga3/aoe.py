"""AOE-only question collection, isolated boards and two-slot attempt scheduling."""

from __future__ import annotations

import asyncio
import hashlib
import json
import mimetypes
import time
from collections import defaultdict
from copy import deepcopy
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID, uuid4, uuid5

from .aoe_models import (
    AOEQuestionInput,
    active_attempts,
    attempt_budget_seconds,
    attempt_budgets,
    attempted_by,
    joint_ready,
    next_joint_question,
    next_solo_question,
    overview_order,
    schedulable_questions,
    scout_ready,
    unfinished,
)
from .docker_runtime import LaunchSpec
from .domain import SYSTEM_ACTOR, AgentRun, AgentState, EventKind, InputFile, PublishRequest, RunState, TaskRun
from .errors import ConflictError, ContractError
from .evidence import evidence_files
from .target_instances import TargetInstances

if TYPE_CHECKING:
    from .coordinator import TaskCoordinator


TERMINAL = {RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED}


class AOERuntime:
    def __init__(self, coordinator: TaskCoordinator, *, clock=time.time, reconcile_seconds=5.0):
        self.coordinator = coordinator
        self.config, self.storage = coordinator.config, coordinator.storage
        self.containers, self.board, self.events = coordinator.containers, coordinator.blackboard, coordinator.events
        self.clock = clock
        self.locks: defaultdict[UUID, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.automation = None
        self._checked_sequences: dict[UUID, int] = {}
        self._manifest_digests: dict[UUID, str] = {}
        self._collection_digests: dict[UUID, str] = {}
        self._manifest_sources: dict[UUID, tuple] = {}
        self._input_files: dict[UUID, list[InputFile]] = {}
        self._parents: dict[UUID, UUID] = {}
        self._agent_sources: dict[tuple[UUID, str], tuple] = {}
        self._wakeups: dict[UUID, asyncio.Event] = {}
        self._next_due: dict[UUID, float | None] = {}
        self.reconcile_seconds = reconcile_seconds
        self._unsubscribe = self.storage.subscribe_changes(self._storage_changed)
        self.targets = TargetInstances(self.config, clock=lambda: self.clock(), on_change=self.wake)
        self._recovering: set[UUID] = set()
        self._target_jobs: dict[UUID, asyncio.Task] = {}
        self._target_claims: dict[UUID, tuple[str, ...]] = {}
        self._target_closes: dict[UUID, asyncio.Task] = {}

    def wake(self, parent_id: UUID) -> None:
        if event := self._wakeups.get(parent_id):
            event.set()

    def _storage_changed(self, kind, value) -> None:
        if kind == "task":
            if value.scene_id != "aoe":
                return
            parent_id = value.parent_task_id or value.id
            self._parents[value.id] = parent_id
        else:
            parent_id = self._parents.get(value.task_id)
            if parent_id is None:
                return
            if kind == "input":
                self._input_files.pop(value.task_id, None)
                self._manifest_sources.pop(value.task_id, None)
            elif kind == "agent":
                # Token/accounting and acknowledgement metadata do not affect scheduling.
                key = (value.task_id, value.agent_id)
                source = (value.desired_state, value.actual_state, value.container_id, value.last_error,
                          *(value.metadata.get(k) for k in ("aoe_paused", "scout_phase", "aoe_attempt_id",
                            "current_question_id", "current_question_title", "cli_session_id")))
                if self._agent_sources.get(key) == source:
                    return
                self._agent_sources[key] = source
        self.wake(parent_id)

    async def restore_paths(self) -> None:
        for question in await self.storage.list_questions():
            self.config.register_question_path(question.parent_task_id, question.id)
            self._parents[question.id] = question.parent_task_id

    async def _attachments(self, question_id: UUID) -> list[InputFile]:
        if question_id not in self._input_files:
            files = await self.storage.list_input_files(question_id)
            self._input_files[question_id] = sorted(files, key=lambda f: (f.created_at, f.id))
        return self._input_files[question_id]

    async def _reconcile_inputs(self, parent_id: UUID, questions: list[TaskRun]) -> None:
        # One batch query also recovers a missed input notification; no N+1 reads.
        grouped = defaultdict(list)
        for item in await self.storage.list_question_input_files(parent_id):
            grouped[item.task_id].append(item)
        for question in questions:
            files = sorted(grouped[question.id], key=lambda f: (f.created_at, f.id))
            if files != self._input_files.get(question.id):
                self._manifest_sources.pop(question.id, None)
                self._input_files[question.id] = files

    async def _update_agent(self, current: AgentRun, **changes) -> AgentRun:
        metadata = changes.get("metadata", {})
        if (any(getattr(current, k) != v for k, v in changes.items() if k != "metadata")
                or any(current.metadata.get(k) != v or k not in current.metadata for k, v in metadata.items())):
            return await self.storage.update_agent(current.task_id, current.agent_id, **changes)
        return current

    async def _update_parent(self, current: TaskRun, *, state=None, metadata=None) -> TaskRun:
        if (state is not None and current.state != state
                or any(current.metadata.get(k) != v for k, v in (metadata or {}).items())):
            return await self.storage.update_task(current.id, state=state, metadata=metadata)
        return current

    def _question_source(self, question: TaskRun) -> tuple:
        fields = ("description", "source_key", "difficulty", "connection_info", "submission_instructions",
                  "status", "registered_at", "solved_at", "scout_ready", "scout_phase", "attempts", "flag")
        return (question.title, question.state, question.created_at, question.blackboard_seq,
                *(question.metadata.get(k) for k in fields), self.targets.spec(question).model_dump(mode="json"),
                self.targets.state(question.id))

    def _atomic_json(self, path: Path, value: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f".{uuid4().hex}.tmp")
        try:
            temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    async def question_view(self, question: TaskRun) -> dict:
        attempts = question.metadata.get("attempts", [])
        files = await self._attachments(question.id)
        return {
            "question_id": str(question.id),
            "parent_task_id": str(question.parent_task_id),
            "title": question.title,
            "description": question.metadata["description"],
            "source_key": question.metadata["source_key"],
            "difficulty": question.metadata.get("difficulty"),
            "connection_info": question.metadata.get("connection_info", ""),
            "submission_instructions": question.metadata.get("submission_instructions", ""),
            "status": question.metadata.get("status", "queued"),
            "registered_at": question.metadata.get("registered_at", question.created_at.timestamp()),
            "solved_at": question.metadata.get("solved_at"),
            "scout_ready": scout_ready(question),
            "scout_phase": question.metadata.get("scout_phase", "completed" if scout_ready(question) else "queued"),
            "attempted_by": sorted(attempted_by(question)),
            "current_agents": [attempt["agent_id"] for attempt in active_attempts(question)],
            "attempts": attempts,
            "flag": question.metadata.get("flag"),
            "report_state": (
                "completed"
                if question.state == RunState.COMPLETED
                else "failed"
                if question.metadata.get("status") in {"solved", "abandoned"} and question.state == RunState.FAILED
                else "generating"
                if question.state in {RunState.FINALIZING, RunState.REPORTING}
                else "pending"
            ),
            "blackboard_seq": question.blackboard_seq,
            "attachments": [
                {**{key: value for key, value in item.model_dump(mode="json").items() if key != "storage_path"},
                 "path": Path(item.storage_path).relative_to(self.config.task_path(question.id)).as_posix()}
                for item in files
            ],
        }

    async def snapshot(self, parent_id: UUID) -> dict:
        parent = await self.storage.get_task(parent_id)
        if parent.scene_id != "aoe" or parent.parent_task_id is not None:
            raise ContractError("AOE overview requires its parent task")
        questions = await self.storage.list_questions(parent_id)
        views = [await self.question_view(question) for question in sorted(questions, key=overview_order)]
        return {
            "task_id": str(parent_id),
            "phase": parent.metadata.get("aoe_phase", "collecting"),
            "attempt_seconds": attempt_budgets(parent.metadata)["first"],
            "attempt_budgets": attempt_budgets(parent.metadata),
            "scout_mode": parent.metadata.get("aoe_scout_mode", "legacy_recon"),
            "collection_complete": parent.metadata.get("collection_complete", False),
            "questions": views,
        }

    async def _save_question(self, question: TaskRun) -> None:
        source = self._question_source(question)
        path = self.config.task_path(question.id) / "question.json"
        if self._manifest_sources.get(question.id) == source and path.is_file():
            return
        value = await self.question_view(question)
        value["target"] = self.targets.spec(question).model_dump(mode="json")
        value["target_runtime"] = self.targets.state(question.id)
        digest = hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
        if self._manifest_digests.get(question.id) != digest:
            self._atomic_json(path, value)
            self._manifest_digests[question.id] = digest
        elif not path.is_file():
            self._atomic_json(path, value)
        self._manifest_sources[question.id] = deepcopy(source)

    async def _collection_manifest(self, parent_id: UUID, *, parent=None, questions=None) -> dict:
        parent = parent if parent is not None else await self.storage.get_task(parent_id)
        questions = questions if questions is not None else await self.storage.list_questions(parent_id)
        value = {
            "task_id": str(parent_id),
            "collection_complete": parent.metadata.get("collection_complete", False),
            "questions": [
                {
                    "question_id": str(q.id),
                    "title": q.title,
                    "target": self.targets.spec(q).model_dump(mode="json"),
                    **{
                        key: q.metadata.get(key)
                        for key in (
                            "source_key",
                            "description",
                            "difficulty",
                            "connection_info",
                            "submission_instructions",
                        )
                    },
                }
                for q in questions
            ],
        }
        path = self.config.task_path(parent_id) / "questions.json"
        digest = hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
        if self._collection_digests.get(parent_id) != digest or not path.is_file():
            self._atomic_json(path, value)
            self._collection_digests[parent_id] = digest
        return value

    async def _collecting_parent(self, task_id: UUID) -> TaskRun:
        parent = await self.storage.get_task(task_id)
        scout = await self.storage.get_agent(task_id, "scout")
        if (
            parent.scene_id != "aoe"
            or parent.parent_task_id is not None
            or parent.state not in {RunState.SCOUTING, RunState.RUNNING}
            or scout.metadata.get("scout_phase") != "collecting"
            or parent.metadata.get("collection_complete")
        ):
            raise ContractError("Question collection is available only in the AOE Scout pre-phase")
        return parent

    def _source_file(self, parent_id: UUID, supplied: str) -> Path:
        if supplied.startswith("/inputs/"):
            root, relative = self.config.task_path(parent_id, "inputs"), supplied.removeprefix("/inputs/")
        else:
            root = self.config.task_path(parent_id, "workspaces", "scout")
            relative = supplied.removeprefix("/workspace/")
        return next(iter(evidence_files(root, [relative]).values()))

    async def _copy_input(self, question: TaskRun, source: Path, supplied: str) -> None:
        ident = uuid5(question.id, "attachment:" + supplied)
        path = self.config.task_path(question.id, "inputs") / f"{ident}-{source.name}"

        def copy():
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

    async def register_questions(self, parent_id: UUID, specifications: list[AOEQuestionInput]) -> dict:
        async with self.locks[parent_id]:
            await self._collecting_parent(parent_id)
            existing = {q.metadata["source_key"]: q for q in await self.storage.list_questions(parent_id)}
            if not specifications or len({spec.source_key for spec in specifications}) != len(specifications):
                raise ContractError("Supply a nonempty batch with distinct original source keys")
            # Validate every path before persisting new questions in this registration call.
            sources = {
                spec.source_key: [(path, self._source_file(parent_id, path)) for path in spec.attachments]
                for spec in specifications
                if spec.source_key not in existing
                or not existing[spec.source_key].metadata.get("registration_complete")
            }
            for spec in specifications:
                payload = spec.model_dump(mode="json")
                fingerprint = hashlib.sha256(
                    json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
                ).hexdigest()
                if spec.source_key in existing:
                    old = existing[spec.source_key]
                    legacy = hashlib.sha256(json.dumps({k: v for k, v in payload.items() if k != "target"},
                                                       ensure_ascii=False, sort_keys=True).encode()).hexdigest()
                    compatible = old.metadata.get("target") is None and spec.target.mode == "unknown"
                    accepted = {fingerprint, legacy} if compatible else {fingerprint}
                    if old.metadata.get("registration_digest") not in accepted:
                        raise ConflictError("Original question key already registered with different data")
                    if existing[spec.source_key].metadata.get("registration_complete"):
                        continue
                question_id = uuid5(parent_id, "aoe-question:" + spec.source_key)
                self.config.register_question_path(parent_id, question_id)
                self.config.ensure_task_directories(question_id)
                question = existing.get(spec.source_key) or await self.storage.create_task(
                    spec.title,
                    "aoe",
                    question_id,
                    parent_task_id=parent_id,
                    metadata={
                        **{key: value for key, value in payload.items()
                           if key not in {"title", "attachments", "target"}},
                        "registration_digest": fingerprint,
                        "ordinal": len(existing),
                        "status": "preparing",
                        "scout_ready": False,
                        "scout_phase": "queued",
                        "attempts": [],
                    },
                )
                self.targets.register(question.id, spec.target)
                registered_agents = {run.agent_id for run in await self.storage.list_agents(question.id)}
                for run in await self.storage.list_agents(parent_id):
                    if run.agent_id not in registered_agents:
                        await self.storage.upsert_agent(
                            AgentRun(
                                task_id=question.id,
                                agent_id=run.agent_id,
                                sdk=run.sdk,
                                provider_id=run.provider_id,
                                model_id=run.model_id,
                                protocol=run.protocol,
                                metadata={"scout_phase": "collecting"} if run.agent_id == "scout" else {},
                            )
                        )
                await self.storage.update_task(question.id, state=RunState.SCOUTING)
                await self.board.publish(
                    question.id,
                    SYSTEM_ACTOR,
                    PublishRequest(
                        kind="user_prompt",
                        topic="scene",
                        body={"text": self.config.scene("aoe").system_prompt},
                        idempotency_key="scene-prompt",
                    ),
                )
                text = (
                    f"题目：{spec.title}\n原始题目 ID（source_key）：{spec.source_key}"
                    f"\n内部题目 ID：{question_id}\n\n{spec.description}"
                    f"\n\n连接信息：{spec.connection_info}\n提交要求：{spec.submission_instructions}"
                )
                await self.board.publish(
                    question.id,
                    SYSTEM_ACTOR,
                    PublishRequest(
                        kind="user_prompt",
                        topic="question",
                        body={"text": text},
                        idempotency_key="question-description",
                    ),
                )
                for entry in await self.storage.list_entries(parent_id):
                    if entry.kind == "user_prompt" and entry.topic == "realtime":
                        await self.board.publish(
                            question.id,
                            entry.actor,
                            PublishRequest(
                                kind="user_prompt",
                                topic="realtime",
                                body=entry.body,
                                idempotency_key="aoe-" + entry.idempotency_key,
                            ),
                        )
                for supplied, source in sources[spec.source_key]:
                    await self._copy_input(question, source, supplied)
                await self.storage.update_task(question.id, state=RunState.RUNNING, metadata={
                    "registration_complete": True, "registered_at": self.clock(),
                    "scout_ready": True, "scout_phase": "completed", "status": "queued",
                })
                await self.storage.update_agent(question.id, "scout", desired_state=AgentState.COMPLETED,
                                                actual_state=AgentState.COMPLETED,
                                                metadata={"scout_phase": "completed"})
                existing[spec.source_key] = await self.storage.get_task(question.id)
                await self._save_question(existing[spec.source_key])
            parent = await self.storage.update_task(parent_id, state=RunState.RUNNING,
                                                    metadata={"aoe_scout_mode": "collection_only"})
            await self._collection_manifest(parent_id, parent=parent, questions=list(existing.values()))
            await self.events.emit(
                parent_id,
                actor=SYSTEM_ACTOR,
                kind=EventKind.SYSTEM,
                text=f"AOE 已登记 {len(existing)} 道题目",
                payload={"question_count": len(existing)},
            )
            return {"saved": True, "task_id": str(parent_id), "question_count": len(existing),
                    "questions": [{"question_id": str(existing[spec.source_key].id),
                                   "source_key": spec.source_key,
                                   "path": str(existing[spec.source_key].id) + '/question.json'}
                                  for spec in specifications]}

    async def finish_collection(self, parent_id: UUID, expected_count: int) -> dict:
        async with self.locks[parent_id]:
            parent = await self.storage.get_task(parent_id)
            questions = await self.storage.list_questions(parent_id)
            if (
                not questions
                or len(questions) != expected_count
                or any(not q.metadata.get("registration_complete") for q in questions)
            ):
                raise ContractError("Collected question count does not match the complete source list")
            if not parent.metadata.get("collection_complete"):
                await self._collecting_parent(parent_id)
                parent = await self.storage.update_task(parent_id, metadata={"collection_complete": True})
            return {"saved": True, **await self._collection_manifest(parent_id, parent=parent, questions=questions)}

    async def run(self, parent_id: UUID, bindings: dict) -> None:
        scout_job = None
        wakeup = self._wakeups.setdefault(parent_id, asyncio.Event())
        self._parents[parent_id] = parent_id
        loop = asyncio.get_running_loop()
        next_reconcile = loop.time()
        try:
            parent = await self.storage.get_task(parent_id)
            await self._recover_attempts(parent_id)
            questions = await self.storage.list_questions(parent_id)
            pending = not parent.metadata.get(
                "scout_complete", bool(questions) and all(scout_ready(q) for q in questions)
            )
            if pending:
                scout_job = asyncio.create_task(self._scout(parent_id, bindings))
                scout_job.add_done_callback(lambda _job: self.wake(parent_id))
            while True:
                # Clear before reading authoritative state, so changes arriving
                # during this tick remain set and cannot be lost before waiting.
                wakeup.clear()
                if scout_job is not None and scout_job.done():
                    await scout_job  # Surface Scout failure while Workers are running.
                reconcile = loop.time() >= next_reconcile
                if await self.tick(parent_id, reconcile=reconcile):
                    break
                if reconcile:
                    next_reconcile = loop.time() + self.reconcile_seconds
                delay = max(0, next_reconcile - loop.time())
                if (due := self._next_due.get(parent_id)) is not None:
                    delay = min(delay, max(0, due - self.clock()))
                try:
                    await asyncio.wait_for(wakeup.wait(), timeout=delay)
                except TimeoutError:
                    pass
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self.storage.update_task(parent_id, state=RunState.FAILED, metadata={"aoe_phase": "failed"})
            await self.events.emit(parent_id, actor=SYSTEM_ACTOR, kind=EventKind.ERROR, text=f"AOE 任务运行失败：{exc}")
            await self.stop_children(parent_id)
        finally:
            if scout_job is not None:
                if not scout_job.done():
                    scout_job.cancel()
                await asyncio.gather(scout_job, return_exceptions=True)
            await self._cancel_target_jobs(parent_id)
            self._wakeups.pop(parent_id, None)
            self._next_due.pop(parent_id, None)
            self._clear_views(parent_id)

    def _clear_views(self, parent_id: UUID) -> None:
        self._collection_digests.pop(parent_id, None)
        for question_id, owner in list(self._parents.items()):
            if owner == parent_id:
                self._input_files.pop(question_id, None)
                self._manifest_sources.pop(question_id, None)
                self._manifest_digests.pop(question_id, None)
        self._agent_sources = {k: v for k, v in self._agent_sources.items()
                               if self._parents.get(k[0]) != parent_id}

    async def _scout(self, parent_id: UUID, bindings: dict) -> None:
        parent = await self.storage.get_task(parent_id)

        async def emit(method: str, params: dict):
            if method == "container.started":
                await self.storage.update_agent(parent_id, "scout", container_id=params["container_id"],
                                                actual_state=AgentState.RUNNING)
            else:
                await self.coordinator.handle_agent_event(parent_id, "scout", method, params)

        try:
            await self.storage.update_task(parent_id, metadata={"aoe_scout_mode": "collection_only"})
            if not parent.metadata.get("collection_complete"):
                await self.storage.update_agent(parent_id, "scout", actual_state=AgentState.RUNNING,
                                                metadata={"scout_phase": "collecting"})
                await self.events.emit(parent_id, actor=SYSTEM_ACTOR, kind=EventKind.SYSTEM,
                                       text="Scout 收集题目，Worker 随登记并行认领简单题或无难度题",
                                       channel_agent_id="scout", payload={"scout_phase": "collecting"})
                await self.containers.scout(LaunchSpec(task_id=parent_id, agent=bindings["scout"],
                                           scout_phase="collecting", aoe_parent_id=parent_id), emit)
            parent = await self.storage.get_task(parent_id)
            questions = await self.storage.list_questions(parent_id)
            if not questions or not parent.metadata.get("collection_complete"):
                raise ContractError("Scout must finish collection of the complete question list")
            # A restored pre-upgrade collection may contain registered but unprepared questions.
            # Registration already populated its board and inputs; no reconnaissance is needed.
            for question in questions:
                if not scout_ready(question) and question.metadata.get("registration_complete"):
                    await self.storage.update_task(question.id, state=RunState.RUNNING,
                                                   metadata={"scout_ready": True, "scout_phase": "completed",
                                                             "status": "queued"})
                    await self._save_question(await self.storage.get_task(question.id))
            await self.storage.update_agent(parent_id, "scout", desired_state=AgentState.COMPLETED,
                                            actual_state=AgentState.COMPLETED,
                                            metadata={"scout_phase": "completed", "current_question_id": None,
                                                      "current_question_title": None})
            await self.storage.update_task(parent_id, state=RunState.RUNNING,
                                           metadata={"scout_complete": True})
            await self.events.emit(parent_id, actor=SYSTEM_ACTOR, kind=EventKind.SYSTEM,
                                   text="全部题目信息已收集，Scout 结束工作；Worker 继续解题",
                                   channel_agent_id="scout", payload={"scout_phase": "completed"})
        finally:
            await self.containers.stop(parent_id, "scout")

    async def _new_attempts(self, parent_id: UUID, question: TaskRun, agents: tuple[str, ...], phase: str) -> TaskRun:
        # Bind the original shared access instructions at EVERY claim, including
        # questions collected before an upgrade and fresh/cross/joint attempts.
        # Never rely on Scout to copy credentials out of free-form user input.
        for entry in await self.storage.list_entries(parent_id, limit=10_000):
            if entry.kind == "user_prompt" and entry.actor.role == "user" and entry.topic in {"task", "realtime"}:
                await self.board.publish(
                    question.id,
                    entry.actor,
                    PublishRequest(
                        kind="user_prompt",
                        topic="task_context" if entry.topic == "task" else "realtime",
                        body={"text": (
                            "AOE 批次公共说明：以下包含用户提供的平台接口、访问凭证与公共要求。"
                            "仅处理当前认领题目，题面与题目 ID 以本题记录为准。\n\n" + entry.body["text"]
                        )} if entry.topic == "task" else entry.body,
                        idempotency_key="aoe-" + entry.idempotency_key,
                    ),
                )
        start = self.clock()
        additions = [
            {
                "id": str(uuid4()),
                "agent_id": agent_id,
                "phase": phase,
                "claimed_at": start,
                "started_at": None,
                "deadline": None,
                "outcome": None,
            }
            for agent_id in agents
        ]

        def assign(metadata):
            if not metadata.get("scout_ready", True):
                raise ConflictError("Question has not completed Scout handoff")
            if any(a.get("outcome") is None for a in metadata.get("attempts", [])) or metadata.get("status") in {
                "solved",
                "abandoned",
            }:
                raise ConflictError("Question is not available for a new attempt")
            return {**metadata, "status": "running", "attempts": [*metadata.get("attempts", []), *additions]}

        question = await self.storage.mutate_task_metadata(question.id, assign)
        target = self.targets.spec(question)
        if target.mode != "unknown":
            state = self.targets.state(question.id)
            text = ("靶场由系统管理，已经就绪。当前地址：" + json.dumps(state.get("addresses", []), ensure_ascii=False)
                    + "。直接使用这些地址，不要自行调用平台的启动或关闭接口。"
                    if target.mode == "managed" else
                    "本题无需靶场实例；使用已收集的附件和题目信息解题，不要申请平台实例。"
                    if target.mode == "none" else
                    "本题使用题面已有的固定目标地址，不需要申请动态实例。")
            await self.board.publish(question.id, SYSTEM_ACTOR, PublishRequest(kind="user_prompt",
                topic="target_runtime", body={"text": text}, idempotency_key="target:" + additions[0]["id"]))
        await self._save_question(question)
        # The first actual action starts the budget, shared by joint participants.
        await asyncio.gather(
            *(self._launch_attempt(parent_id, question, attempt, fresh=phase == "solo") for attempt in additions)
        )
        return question

    async def action_started(self, question_id: UUID, agent_id: str, attempt_id: str) -> None:
        question = await self.storage.get_task(question_id)
        if question.parent_task_id is None:
            return
        current = next((a for a in active_attempts(question)
                        if a['id'] == attempt_id and a['agent_id'] == agent_id), None)
        if current is None or current.get('deadline') is not None:
            return
        async with self.locks[question.parent_task_id]:
            question = await self.storage.get_task(question_id)
            current = next((a for a in active_attempts(question)
                            if a['id'] == attempt_id and a['agent_id'] == agent_id), None)
            if current is None or current.get('deadline') is not None:
                return
            parent = await self.storage.get_task(question.parent_task_id)
            now = self.clock()
            deadline = now + attempt_budget_seconds(parent.metadata, question.metadata['attempts'], current)
            def start(metadata):
                return {**metadata, 'attempts': [
                    {**a, 'started_at': now, 'deadline': deadline}
                    if a.get('outcome') is None and (a['id'] == attempt_id or current['phase'] == 'joint')
                    else a for a in metadata['attempts']]}
            question = await self.storage.mutate_task_metadata(question.id, start)
            await self._save_question(question)
            for attempt in active_attempts(question):
                if attempt.get('deadline') is not None:
                    await self.coordinator.gateway.notify(question.id, attempt['agent_id'], 'session.deadline',
                        {'attempt_id': attempt['id'], 'deadline': attempt['deadline']})

    async def _launch_attempt(self, parent_id: UUID, question: TaskRun, attempt: dict, *, fresh: bool) -> None:
        agent_id = attempt["agent_id"]
        slot = await self.storage.get_agent(parent_id, agent_id)
        binding = self.config.resolve_agent(agent_id, slot.provider_id, slot.model_id, slot.protocol)
        pointer = (
            self.config.task_path(question.id, "workspaces", agent_id) / ".tga3" / f"{binding.runtime}-session.json"
        )
        board_reset = {"blackboard_cursor": 0, "blackboard_pending": None, "blackboard_read_at": 0}
        await self.storage.update_agent(
            question.id,
            agent_id,
            provider_id=slot.provider_id,
            model_id=slot.model_id,
            protocol=slot.protocol,
            desired_state=AgentState.RUNNING,
            actual_state=AgentState.STARTING,
            last_error=None,
            metadata={
                "aoe_attempt_id": attempt["id"],
                "aoe_phase": attempt["phase"],
                **(board_reset if fresh or not pointer.is_file() else {}),
            },
        )
        await self.storage.update_agent(
            parent_id,
            agent_id,
            desired_state=AgentState.RUNNING,
            actual_state=AgentState.STARTING,
            last_error=None,
            metadata={
                "current_question_id": str(question.id),
                "current_question_title": question.title,
                "aoe_waiting": False,
                "attempt_deadline": attempt["deadline"],
            },
        )
        await self.events.emit(
            parent_id,
            actor=SYSTEM_ACTOR,
            kind=EventKind.SYSTEM,
            text=f"{slot.agent_id} {'共同攻坚' if attempt['phase'] == 'joint' else '领取'}：{question.title}",
            channel_agent_id=agent_id,
            payload={
                "question_id": str(question.id),
                "attempt_id": attempt["id"],
                "phase": attempt["phase"],
                "fresh_session": fresh,
            },
        )
        launch = asyncio.create_task(
            self.containers.launch(
                LaunchSpec(
                    task_id=question.id,
                    agent=binding,
                    aoe_parent_id=parent_id,
                    fresh_session=fresh,
                    attempt_id=attempt["id"],
                    attempt_deadline=attempt["deadline"],
                    target_mode=self.targets.spec(question).mode,
                    target_addresses=tuple(self.targets.state(question.id).get("addresses", [])),
                )
            )
        )
        try:
            container_id = await asyncio.shield(launch)
        except asyncio.CancelledError:
            await launch
            raise
        await self.storage.update_agent(question.id, agent_id, container_id=container_id)
        await self.storage.update_agent(parent_id, agent_id, container_id=container_id)

    async def _stop_worker(self, question_id: UUID, agent_id: str) -> None:
        await self.storage.update_agent(question_id, agent_id, desired_state=AgentState.STOPPING)
        try:
            async with asyncio.timeout(7):
                await self.coordinator.gateway.request(question_id, agent_id, "session.stop")
        except (Exception, TimeoutError):
            # A missing control channel must not prevent enforcing the host deadline.
            pass
        await self.containers.stop(question_id, agent_id)
        await self.storage.update_agent(
            question_id, agent_id, desired_state=AgentState.STOPPED, actual_state=AgentState.STOPPED, container_id=None
        )

    async def _finish_attempts(self, question: TaskRun, agents: set[str], outcome: str) -> TaskRun:
        await asyncio.gather(*(self._stop_worker(question.id, agent_id) for agent_id in agents))
        sessions = {}
        for agent_id in agents:
            run = await self.storage.get_agent(question.id, agent_id)
            pointer = self.config.task_path(question.id, "workspaces", agent_id) / ".tga3" / f"{run.sdk}-session.json"
            if pointer.is_file():
                sessions[agent_id] = json.loads(pointer.read_text(encoding="utf-8")).get("session_id")
            else:
                sessions[agent_id] = run.metadata.get("cli_session_id")

        def finish(metadata):
            attempts = [
                {**a, "outcome": outcome, "finished_at": self.clock(), "cli_session_id": sessions.get(a["agent_id"])}
                if a.get("outcome") is None and a["agent_id"] in agents
                else a
                for a in metadata.get("attempts", [])
            ]
            return {**metadata, "attempts": attempts}

        question = await self.storage.mutate_task_metadata(question.id, finish)
        if not active_attempts(question) and self.targets.spec(question).mode == "managed":
            job = self._target_closes[question.id] = asyncio.create_task(self.targets.release(question.id, force=True))
            job.add_done_callback(lambda _job: self.wake(question.parent_task_id))
            # Permit the fast path without holding the scheduler lock across network delays.
            await asyncio.wait({job}, timeout=0.05)
        await self._save_question(question)
        return question

    async def _candidate(self, question: TaskRun):
        cursor = self._checked_sequences.get(question.id, 0)
        while cursor < question.blackboard_seq:
            entries = await self.storage.list_entries(question.id, after_seq=cursor, limit=200)
            if not entries:
                break
            for entry in entries:
                cursor = entry.seq
                if entry.kind == "final_candidate":
                    self._checked_sequences[question.id] = cursor
                    return entry
        self._checked_sequences[question.id] = cursor
        return None

    async def _settle(self, question: TaskRun, *, status: str, flag: str | None = None) -> TaskRun:
        agents = {attempt["agent_id"] for attempt in active_attempts(question)}
        if agents:
            question = await self._finish_attempts(question, agents, "solved" if status == "solved" else "timeout")
        question = await self.storage.update_task(question.id, metadata={"status": status, "flag": flag,
                                               **({"solved_at": self.clock()} if status == "solved" else {})})
        await self._save_question(question)
        if self.automation is not None:
            self.automation.start_report(question.id)
        elif status == "abandoned":
            question = await self.storage.update_task(question.id, state=RunState.CANCELLED)
        await self.events.emit(
            question.parent_task_id,
            actor=SYSTEM_ACTOR,
            kind=EventKind.SYSTEM,
            text=f"题目{('已完成' if status == 'solved' else '达到共同攻坚预算，已放弃')}：{question.title}",
            payload={"question_id": str(question.id), "status": status},
        )
        return question

    async def _prepare_target(self, question: TaskRun, agents: tuple[str, ...]) -> bool:
        if self.targets.spec(question).mode != "managed":
            return True
        previous = self.targets.state(question.id)
        job = self._target_jobs.get(question.id)
        if job is None:
            if previous.get("retry_at", 0) > self.clock() and previous.get("state") != "ready":
                return False
            job = self._target_jobs[question.id] = asyncio.create_task(self.targets.prepare(question))
            job.add_done_callback(lambda _job: self.wake(question.parent_task_id))
            self._target_claims[question.id] = agents
        await asyncio.wait({job}, timeout=0.05)
        if not job.done():
            return False
        self._target_jobs.pop(question.id, None)
        self._target_claims.pop(question.id, None)
        ready = job.result()
        current = self.targets.state(question.id)
        if not ready and current.get("reason") != previous.get("reason"):
            reason = "平台实例配额不足" if current.get("reason") == "quota" else current.get("reason", "准备中")
            await self.events.emit(question.parent_task_id, actor=SYSTEM_ACTOR, kind=EventKind.SYSTEM,
                text=f"等待靶场资源：{question.title}（{reason}），尚未开始解题尝试",
                payload={"question_id": str(question.id), "target_state": current.get("state"), "reason": reason})
        return ready and current.get("state") == "ready"

    async def tick(self, parent_id: UUID, *, reconcile=False) -> bool:
        """One serialized scheduling decision; tests advance the injected clock without sleeping."""
        async with self.locks[parent_id]:
            parent = await self.storage.get_task(parent_id)
            if parent.state in TERMINAL:
                return True
            workers = self.config.worker_agent_ids
            questions = await self.storage.list_questions(parent_id)
            if reconcile:
                await self._reconcile_inputs(parent_id, questions)
                if not (self.config.task_path(parent_id) / "questions.json").is_file():
                    await self._collection_manifest(parent_id, parent=parent, questions=questions)

            def replace(question):
                for index, current in enumerate(questions):
                    if current.id == question.id:
                        questions[index] = question
                        break

            for question in unfinished(questions):
                candidate = await self._candidate(question)
                if candidate is not None:
                    replace(await self._settle(question, status="solved", flag=candidate.body["conclusion"]))
                    continue
                active = active_attempts(question)
                expired = {attempt["agent_id"] for attempt in active
                           if attempt.get("deadline") is not None and attempt["deadline"] <= self.clock()}
                if expired:
                    if any(a["phase"] == "joint" for a in active):
                        # Both participants share this exact deadline; never grant each an extra budget.
                        replace(await self._settle(question, status="abandoned"))
                    else:
                        question = await self._finish_attempts(question, expired, "timeout")
                        replace(await self.storage.update_task(question.id, metadata={"status": "queued"}))
                        await self.events.emit(
                            parent_id,
                            actor=SYSTEM_ACTOR,
                            kind=EventKind.SYSTEM,
                            text=f"独立尝试预算已到，保留黑板并换题：{question.title}",
                            payload={"question_id": str(question.id), "agents": sorted(expired)},
                        )
                    self._recovering.discard(question.id)
                elif active and question.id in self._recovering and await self._prepare_target(
                        question, tuple(a["agent_id"] for a in active)):
                    for attempt in active:
                        await self._launch_attempt(parent_id, question, attempt, fresh=False)
                    self._recovering.discard(question.id)
            occupied = {a["agent_id"] for q in questions for a in active_attempts(q)}
            slots = {run.agent_id: run for run in await self.storage.list_agents(parent_id)}
            available = [
                agent_id
                for agent_id in workers
                if agent_id not in occupied and not slots[agent_id].metadata.get("aoe_paused")
            ]
            eligible = schedulable_questions(questions,
                         collection_complete=parent.metadata.get("collection_complete", False))
            phase = "joint" if parent.metadata.get("collection_complete") and joint_ready(eligible, workers) else "solo"
            if any(a["phase"] == "joint" for q in questions for a in active_attempts(q)):
                phase = "joint"
            if parent.metadata.get("collection_complete") and questions and not unfinished(questions):
                phase = "reporting"
            parent = await self._update_parent(parent, metadata={"aoe_phase": phase})
            if phase == "solo":
                for agent_id in available:
                    candidates = [q for q in eligible if q.id not in self._target_claims
                                  or agent_id in self._target_claims[q.id]]
                    quota_blocked = self.targets.quota_blocked(eligible)
                    while True:
                        offline = [q for q in candidates if self.targets.spec(q).mode == "none"]
                        question = next((q for q in candidates if q.id in self._target_claims), None)
                        question = question or (next_solo_question(offline, agent_id) if quota_blocked else None)
                        question = question or next_solo_question(candidates, agent_id)
                        if question is None:
                            break
                        if not await self._prepare_target(question, (agent_id,)):
                            if question.id in self._target_claims:
                                break
                            quota_blocked |= self.targets.state(question.id).get("reason") == "quota"
                            candidates = [q for q in candidates if q.id != question.id]
                            continue
                        replace(await self._new_attempts(parent_id, question, (agent_id,), "solo"))
                        eligible = schedulable_questions(questions,
                                     collection_complete=parent.metadata.get("collection_complete", False))
                        break
            elif phase == "joint" and not occupied and len(available) == len(workers):
                candidates = list(eligible)
                quota_blocked = self.targets.quota_blocked(eligible)
                while True:
                    offline = [q for q in candidates if self.targets.spec(q).mode == "none"]
                    question = next((q for q in candidates if q.id in self._target_claims), None)
                    question = question or (next_joint_question(offline) if quota_blocked else None)
                    question = question or next_joint_question(candidates)
                    if question is None:
                        break
                    if not await self._prepare_target(question, workers):
                        if question.id in self._target_claims:
                            break
                        quota_blocked |= self.targets.state(question.id).get("reason") == "quota"
                        candidates = [q for q in candidates if q.id != question.id]
                        continue
                    replace(await self._new_attempts(parent_id, question, workers, "joint"))
                    break
            for question in questions:
                await self._save_question(question)
            await self._sync_slots(parent_id, questions, slots)
            due = [a["deadline"] for q in questions for a in active_attempts(q)
                   if a.get("deadline") is not None]
            due.extend(record["retry_at"] for record in self.targets.records.values()
                       if record["parent_id"] == str(parent_id) and record.get("retry_at", 0) > self.clock()
                       and record.get("state") != "ready")
            self._next_due[parent_id] = min(due, default=None)
            if (parent.metadata.get("collection_complete") and questions and not unfinished(questions)
                    and any(q.state not in TERMINAL for q in questions)):
                parent = await self._update_parent(parent, state=RunState.REPORTING,
                                                   metadata={"aoe_phase": "reporting"})
            if (
                questions
                and parent.metadata.get("collection_complete")
                and not unfinished(questions)
                and all(q.state in TERMINAL for q in questions)
                and parent.metadata.get("scout_complete", all(scout_ready(q) for q in questions))
            ):
                await self.storage.update_task(parent_id, state=RunState.COMPLETED, metadata={"aoe_phase": "completed"})
                for agent_id in [*workers, "reporter"]:
                    await self.storage.update_agent(
                        parent_id,
                        agent_id,
                        desired_state=AgentState.COMPLETED,
                        actual_state=AgentState.COMPLETED,
                        metadata={"aoe_waiting": False},
                    )
                await self.events.emit(
                    parent_id,
                    actor=SYSTEM_ACTOR,
                    kind=EventKind.SYSTEM,
                    text="AOE 所有题目已结束，逐题报告已处理完成。",
                )
                return True
            return False

    async def _sync_slots(self, parent_id: UUID, questions: list[TaskRun], slots: dict[str, AgentRun]) -> None:
        for agent_id in self.config.worker_agent_ids:
            slot = slots[agent_id]
            current = next(((q, a) for q in questions for a in active_attempts(q) if a["agent_id"] == agent_id), None)
            if current:
                question, attempt = current
                run = await self.storage.get_agent(question.id, agent_id)
                await self._update_agent(
                    slot,
                    actual_state=run.actual_state,
                    container_id=run.container_id,
                    last_error=run.last_error,
                    metadata={
                        "current_question_id": str(question.id),
                        "current_question_title": question.title,
                        "attempt_deadline": attempt["deadline"],
                        "aoe_waiting": False,
                        "cli_session_id": run.metadata.get("cli_session_id"),
                    },
                )
            else:
                await self._update_agent(
                    slot,
                    actual_state=AgentState.PAUSED if slot.metadata.get("aoe_paused") else AgentState.IDLE,
                    container_id=None,
                    metadata={
                        "current_question_id": None,
                        "current_question_title": None,
                        "attempt_deadline": None,
                        "aoe_waiting": True,
                    },
                )
        reporting = next((q for q in questions if q.state == RunState.REPORTING), None)
        await self._update_agent(
            slots["reporter"],
            actual_state=AgentState.RUNNING if reporting else AgentState.IDLE,
            metadata={
                "current_question_id": str(reporting.id) if reporting else None,
                "current_question_title": reporting.title if reporting else None,
            },
        )

    async def _recover_attempts(self, parent_id: UUID) -> None:
        for question in await self.storage.list_questions(parent_id):
            active = active_attempts(question)
            if active and not await self.targets.prepare(question):
                # A managed lease that cannot be restored must not run a CLI against stale addresses.
                await asyncio.gather(*(self._stop_worker(question.id, a["agent_id"]) for a in active))
                self._recovering.add(question.id)
                continue
            for attempt in active:
                if attempt.get("deadline") is None or attempt["deadline"] > self.clock():
                    await self._stop_worker(question.id, attempt["agent_id"])
                    await self._launch_attempt(parent_id, question, attempt, fresh=False)
            if question.metadata.get("status") in {"solved", "abandoned"} and question.state not in TERMINAL:
                if self.automation is not None:
                    self.automation.start_report(question.id)

    async def stop_children(self, parent_id: UUID, *, cancelled: bool = False) -> None:
        await self._cancel_target_jobs(parent_id)
        for question in await self.storage.list_questions(parent_id):
            if self.automation is not None:
                await self.automation.cancel(question.id)
            agents = {a["agent_id"] for a in active_attempts(question)}
            if agents:
                question = await self._finish_attempts(question, agents, "interrupted")
            await self.containers.stop(question.id, "reporter")
            if cancelled and question.state not in TERMINAL:
                await self.storage.update_task(question.id, state=RunState.CANCELLED, metadata={"status": "cancelled"})
        await self.targets.release_parent(parent_id)

    async def _cancel_target_jobs(self, parent_id: UUID) -> None:
        questions = {q.id for q in await self.storage.list_questions(parent_id)}
        jobs = []
        for mapping in (self._target_jobs, self._target_closes):
            for ident in questions:
                job = mapping.pop(ident, None)
                if job is not None:
                    if not job.done():
                        job.cancel()
                    jobs.append(job)
                self._target_claims.pop(ident, None)
        if jobs:
            await asyncio.gather(*jobs, return_exceptions=True)

    async def close(self) -> None:
        self._unsubscribe()
        jobs = [*self._target_jobs.values(), *self._target_closes.values()]
        for job in jobs:
            if not job.done():
                job.cancel()
        if jobs:
            await asyncio.gather(*jobs, return_exceptions=True)
        self._target_jobs.clear()
        self._target_closes.clear()
        self._target_claims.clear()
        self._input_files.clear()
        self._manifest_sources.clear()
        self._manifest_digests.clear()
        self._collection_digests.clear()
        self._agent_sources.clear()
        self._parents.clear()
        self._next_due.clear()
        self._wakeups.clear()
        await self.targets.close()
