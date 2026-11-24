"""AOE question lifecycle facade and multi-Worker attempt scheduler."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID, uuid4, uuid5

import httpx

from .aoe_attachments import AOEAttachmentManager
from .aoe_models import (
    AOEQuestionInput,
    active_attempts,
    attempt_budget_seconds,
    attempt_budgets,
    attempted_by,
    first_success_statistics,
    joint_ready,
    joint_workers,
    next_joint_question,
    next_solo_question,
    question_order,
    rescue_after,
    schedulable_questions,
    scout_ready,
    solo_attempts,
    unfinished,
)
from .aoe_scout_runner import AOEScoutRunner
from .aoe_views import AOEViewStore, atomic_json
from .batch_context import BatchContext
from .docker_runtime import LaunchSpec
from .domain import SYSTEM_ACTOR, AgentRun, AgentState, EventKind, InputFile, PublishRequest, RunState, TaskRun
from .errors import ConflictError, ContractError
from .question_collection import QuestionCollection
from .submissions import Submissions
from .target_instances import TargetInstances
from .task_metadata import clear_question_round_state

if TYPE_CHECKING:
    from .coordinator import TaskCoordinator


TERMINAL = {RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED}
_UNSET = object()


class AOERuntime:
    def __init__(self, coordinator: TaskCoordinator, *, clock=time.time, reconcile_seconds=5.0):
        self.coordinator = coordinator
        self.config, self.storage = coordinator.config, coordinator.storage
        self.containers, self.board, self.events = coordinator.containers, coordinator.blackboard, coordinator.events
        self.clock = clock
        self.locks: defaultdict[UUID, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._automation_override: object = _UNSET
        self._checked_sequences: dict[UUID, int] = {}
        self._parents: dict[UUID, UUID] = {}
        self._agent_sources: dict[tuple[UUID, str], tuple] = {}
        self._wakeups: dict[UUID, asyncio.Event] = {}
        self._next_due: dict[UUID, float | None] = {}
        self.reconcile_seconds = reconcile_seconds
        self._unsubscribe = self.storage.subscribe_changes(self._storage_changed)
        self._targets = TargetInstances(self.config, clock=lambda: self.clock(), on_change=self.wake)
        self._submissions = Submissions(self.config)
        self.overview = BatchContext(self.config, self.storage)
        self.attachments = AOEAttachmentManager(self.config, self.storage, self.board)
        self.views = AOEViewStore(
            self.config,
            self.storage,
            self.attachments,
            self._targets,
            self._submissions,
            write_json=lambda path, value: self._atomic_json(path, value),
            build_question_view=lambda q: self.question_view(q),
        )
        self.collection = QuestionCollection(self)
        self.scout_runner = AOEScoutRunner(self)
        self._recovering: set[UUID] = set()
        self._target_jobs: dict[UUID, asyncio.Task] = {}
        self._target_claims: dict[UUID, tuple[str, ...]] = {}
        self._target_closes: dict[UUID, asyncio.Task] = {}

    @property
    def targets(self) -> TargetInstances:
        return self._targets

    @targets.setter
    def targets(self, value: TargetInstances) -> None:
        self._targets = value
        self.views.targets = value

    @property
    def submissions(self) -> Submissions:
        return self._submissions

    @submissions.setter
    def submissions(self, value: Submissions) -> None:
        self._submissions = value
        self.views.submissions = value

    @property
    def _input_files(self) -> dict[UUID, list[InputFile]]:
        return self.attachments._input_files

    @property
    def _manifest_sources(self) -> dict[UUID, tuple]:
        return self.views._manifest_sources

    @property
    def automation(self):
        if self._automation_override is not _UNSET:
            return self._automation_override
        return getattr(self.coordinator, "automation", None)

    @automation.setter
    def automation(self, value) -> None:
        self._automation_override = value

    @property
    def attachment_client(self) -> httpx.AsyncClient | None:
        return self.attachments.client

    @attachment_client.setter
    def attachment_client(self, value: httpx.AsyncClient | None) -> None:
        self.attachments.client = value

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
                self.attachments.invalidate(value.task_id)
                self.views.invalidate_question(value.task_id)
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
        return await self.attachments.list_for(question_id)

    async def _reconcile_inputs(self, parent_id: UUID, questions: list[TaskRun]) -> None:
        await self.attachments.reconcile(parent_id, questions, on_changed=self.views.invalidate_question)

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

    def _atomic_json(self, path: Path, value: dict) -> None:
        atomic_json(path, value)

    async def question_view(self, question: TaskRun) -> dict:
        return await self.views.question_view(question)

    async def snapshot(self, parent_id: UUID) -> dict:
        return await self.views.snapshot(parent_id)

    async def _save_question(self, question: TaskRun) -> None:
        await self.views.save_question(question)

    async def refresh_collection_state(self, parent_id: UUID, *, parent=None, questions=None) -> dict:
        """Public entry point for QuestionImporter & scheduler to update collection-state.json."""
        return await self.views.write_collection_state(
            parent_id,
            summary=self.collection.state_summary(parent_id),
            parent=parent,
            questions=questions,
        )

    async def _collection_state(self, parent_id: UUID, *, parent=None, questions=None) -> dict:
        return await self.refresh_collection_state(parent_id, parent=parent, questions=questions)

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
        return self.attachments.resolve_source_file(parent_id, supplied)

    async def _copy_input(self, question: TaskRun, source: Path, supplied: str) -> None:
        await self.attachments.copy_local_input(question, source, supplied)

    async def _prepare_attachments(self, question: TaskRun) -> None:
        async def on_updated(updated: TaskRun) -> None:
            self.views.invalidate_question(updated.id)
            await self.views.save_question(updated)

        await self.attachments.prepare_remote(question, on_updated=on_updated)

    async def register_questions(self, parent_id: UUID, specifications: list[AOEQuestionInput]) -> dict:
        async with self.locks[parent_id]:
            parent = await self._collecting_parent(parent_id)
            auto_submit_flag = parent.metadata.get("aoe_auto_submit_flag", True)
            existing = {q.metadata["source_key"]: q for q in await self.storage.list_questions(parent_id)}
            if not specifications or len({spec.source_key for spec in specifications}) != len(specifications):
                raise ContractError("Supply a nonempty batch with distinct original source keys")
            # Validate every local attachment path before persisting new questions.
            # Skip attachment handling for questions already solved before this task started.
            split_map = {spec.source_key: spec.split_attachments() for spec in specifications}
            sources = {
                spec.source_key: (
                    []
                    if spec.already_solved
                    else [(path, self._source_file(parent_id, path)) for path in split_map[spec.source_key][0]]
                )
                for spec in specifications
                if spec.source_key not in existing
                or not existing[spec.source_key].metadata.get("registration_complete")
            }
            for spec in specifications:
                local_paths, remote_specs = split_map[spec.source_key]
                payload = spec.model_dump(mode="json", exclude={"source"})
                if not payload.get("flag_format"):
                    payload.pop("flag_format", None)  # Preserve historical registration digests.
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
                source_path = self.config.task_path(question_id) / 'source.json'
                if spec.source is not None and not source_path.is_file():
                    self._atomic_json(source_path, spec.source)
                pre_solved = bool(spec.already_solved)
                remote_dump = [] if pre_solved else [item.model_dump(mode="json") for item in remote_specs]
                question = existing.get(spec.source_key) or await self.storage.create_task(
                    spec.title,
                    "aoe",
                    question_id,
                    parent_task_id=parent_id,
                    metadata={
                        **{
                            key: value
                            for key, value in payload.items()
                            if key not in {"title", "attachments", "already_solved", "solved_flag"}
                        },
                        "remote_attachments": remote_dump,
                        "pre_solved": pre_solved,
                        "registration_digest": fingerprint,
                        "aoe_auto_submit_flag": auto_submit_flag,
                        "knowledge_source": parent.metadata.get("knowledge_source", "online"),
                        "produce_report": False if pre_solved else parent.metadata.get("produce_report", True),
                        "ordinal": len(existing),
                        "status": "solved" if pre_solved else "preparing",
                        **({"flag": spec.solved_flag, "finish_reason": "pre_solved", "solved_at": self.clock()}
                           if pre_solved else {}),
                        "scout_ready": False,
                        "scout_phase": "queued",
                        "attempts": [],
                    },
                )
                self.targets.register(question.id, spec.target)
                if auto_submit_flag and not pre_solved:
                    self.submissions.register(question.id, spec.submission or
                        self.submissions.legacy_spec(question, spec.target.model_dump(mode="json")))
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
                header = [
                    f"题目：{spec.title}",
                    f"原始题目 ID（source_key）：{spec.source_key}",
                    f"内部题目 ID：{question_id}",
                ]
                if spec.category:
                    header.append(f"分类：{spec.category}")
                if spec.tags:
                    header.append(f"标签：{', '.join(spec.tags)}")
                if pre_solved:
                    header.append("状态：开启本次任务前已在平台解出（跳过求解、附件下载与报告生成）")
                body_text = spec.description or (
                    "（本题在开启任务前已解出）"
                    if pre_solved
                    else "（本题无文字题面，请直接分析 /inputs 中的题目附件）"
                )
                text = "\n".join(header) + f"\n\n{body_text}\n\n连接信息：{spec.connection_info}"
                if spec.flag_format:
                    text += f"\nFlag 格式：{spec.flag_format}"
                if auto_submit_flag and not pre_solved:
                    text += f"\n提交要求：{spec.submission_instructions}"
                if remote_dump:
                    text += f"\n远程附件：系统将在领取本题时自动下载至 /inputs/（共 {len(remote_dump)} 个）"
                if spec.collection_notes:
                    text += f"\n收集备注：{spec.collection_notes}"
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
                if pre_solved:
                    await self.storage.update_task(question.id, state=RunState.COMPLETED, metadata={
                        "registration_complete": True, "registered_at": self.clock(),
                        "scout_ready": True, "scout_phase": "completed",
                        "status": "solved", "pre_solved": True, "finish_reason": "pre_solved",
                        "produce_report": False,
                    })
                else:
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
            await self._collection_state(parent_id, parent=parent, questions=list(existing.values()))
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

    async def finish_collection(
        self,
        parent_id: UUID,
        expected_count: int | None = None,
        *,
        status: str = "complete",
        warnings: list[str] | None = None,
        force: bool = False,
    ) -> dict:
        if status not in {"complete", "partial"}:
            raise ContractError("Collection status must be 'complete' or 'partial'")
        if self.collection.has_pending(parent_id):
            parent_check = await self.storage.get_task(parent_id)
            if not parent_check.metadata.get("collection_complete"):
                await self.collection.import_outbox(parent_id)
        async with self.locks[parent_id]:
            parent = await self.storage.get_task(parent_id)
            questions = await self.storage.list_questions(parent_id)
            if not questions or any(not q.metadata.get("registration_complete") for q in questions):
                raise ContractError("No complete registered questions are available to finish collection")
            if status == "complete" and expected_count is not None and len(questions) != expected_count:
                raise ContractError(
                    f"Collected question count ({len(questions)}) does not match expected_count ({expected_count})"
                )
            warnings_list = [str(item).strip() for item in (warnings or []) if str(item).strip()]
            if not parent.metadata.get("collection_complete"):
                await self.overview.prepare(parent_id)
                if not force:
                    await self._collecting_parent(parent_id)
                parent = await self.storage.update_task(
                    parent_id,
                    metadata={
                        "collection_complete": True,
                        "collection_status": status,
                        "collection_warnings": warnings_list,
                    },
                )
            return {"saved": True, **await self._collection_state(parent_id, parent=parent, questions=questions)}

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
        self.collection.clear_state(parent_id)
        question_ids = [qid for qid, owner in list(self._parents.items()) if owner == parent_id]
        for question_id in question_ids:
            self.attachments.invalidate(question_id)
        self.views.clear_parent(parent_id, question_ids)
        self._agent_sources = {
            k: v for k, v in self._agent_sources.items() if self._parents.get(k[0]) != parent_id
        }

    async def _scout(self, parent_id: UUID, bindings: dict) -> None:
        await self.scout_runner.run(parent_id, bindings)

    async def _copy_realtime(self, parent_id: UUID, question: TaskRun) -> None:
        # Initial shared instructions stay in the overview; only live corrections
        # join the question's incremental blackboard.
        for entry in await self.storage.list_entries(parent_id, limit=10_000):
            if entry.kind == "user_prompt" and entry.actor.role == "user" and entry.topic == "realtime":
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

    async def _target_prompt(self, question: TaskRun, token: str) -> None:
        target = self.targets.spec(question)
        if target.mode == "unknown":
            return
        state = self.targets.state(question.id)
        text = ("靶场由系统管理，已经就绪。当前地址：" + json.dumps(state.get("addresses", []), ensure_ascii=False)
                + "。直接使用这些地址，不要自行调用平台的启动或关闭接口。"
                if target.mode == "managed" else
                "本题无需靶场实例；使用已收集的附件和题目信息解题，不要申请平台实例。"
                if target.mode == "none" else
                "本题使用题面已有的固定目标地址，不需要申请动态实例。")
        await self.board.publish(question.id, SYSTEM_ACTOR, PublishRequest(kind="user_prompt",
            topic="target_runtime", body={"text": text}, idempotency_key="target:" + token))

    async def _new_attempts(self, parent_id: UUID, question: TaskRun, agents: tuple[str, ...], phase: str) -> TaskRun:
        pool = tuple(run.agent_id for run in await self.storage.list_agents(parent_id)
                     if run.agent_id in self.config.worker_agent_ids)
        await self._copy_realtime(parent_id, question)
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
            current = question.model_copy(update={'metadata': metadata})
            tried = attempted_by(current)
            rounds = solo_attempts(current)
            if len(pool) == 4:
                expected = set(joint_workers(current, pool))
                if phase == 'joint' and (len(rounds) != 2 or set(agents) != expected or len(agents) != 2):
                    raise ConflictError(
                        'Joint attempts require the assigned pair '
                        '(two Workers who have not tried, or reinforcement pair)')
                if phase == 'solo' and (len(agents) != 1 or len(rounds) >= 2 or set(agents) & tried):
                    raise ConflictError('A question has at most two solo attempts by distinct Workers')
            if not metadata.get("scout_ready", True):
                raise ConflictError("Question has not completed Scout handoff")
            if any(a.get("outcome") is None for a in metadata.get("attempts", [])) or metadata.get("status") in {
                "solved",
                "abandoned",
            }:
                raise ConflictError("Question is not available for a new attempt")
            return {**metadata, "status": "running", "attempts": [*metadata.get("attempts", []), *additions]}

        question = await self.storage.mutate_task_metadata(question.id, assign)
        await self._prepare_attachments(question)
        await self._target_prompt(question, additions[0]['id'])
        await self._save_question(question)
        # The first actual action starts the budget, shared by joint participants.
        await asyncio.gather(
            *(self._launch_attempt(parent_id, question, attempt,
                fresh=phase == "solo" or len(pool) == 4 and attempt['agent_id'] not in attempted_by(question))
              for attempt in additions)
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
                return {**metadata,
                    **({'swarm': {**metadata['swarm'], 'started_at': now, 'deadline': deadline}}
                       if current['phase'] == 'swarm' else {}), 'attempts': [
                    {**a, 'started_at': now, 'deadline': deadline}
                    if a.get('outcome') is None and (a['id'] == attempt_id or current['phase'] in {'joint', 'swarm'})
                    else a for a in metadata['attempts']]}
            question = await self.storage.mutate_task_metadata(question.id, start)
            await self._save_question(question)
            for attempt in active_attempts(question):
                if attempt.get('deadline') is not None:
                    await self.coordinator.gateway.notify(question.id, attempt['agent_id'], 'session.deadline',
                        {'attempt_id': attempt['id'], 'deadline': attempt['deadline']})

    async def _launch_attempt(self, parent_id: UUID, question: TaskRun, attempt: dict, *, fresh: bool) -> None:
        await self.overview.prepare(parent_id)
        agent_id = attempt["agent_id"]
        auto_submit_flag = question.metadata.get("aoe_auto_submit_flag", True)
        if auto_submit_flag:
            self.submissions.write_context(question, agent_id)
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
        label = {'joint': '共同攻坚', 'swarm': '围殴', 'reinforcement': '增援'}.get(attempt['phase'], '领取')
        await self.events.emit(
            parent_id,
            actor=SYSTEM_ACTOR,
            kind=EventKind.SYSTEM,
            text=f"{slot.agent_id} {label}：{question.title}",
            channel_agent_id=agent_id,
            payload={
                "question_id": str(question.id),
                "attempt_id": attempt["id"],
                "phase": attempt["phase"],
                "fresh_session": fresh,
            },
        )
        evidence = tuple(sorted(attempted_by(question) - {agent_id})) \
            if attempt['phase'] == 'joint' and agent_id not in attempted_by(question) else ()
        if attempt['phase'] == 'swarm':
            evidence = tuple(sorted({a['agent_id'] for a in question.metadata['attempts']
                if a.get('outcome') is not None or a.get('previous_phase')} - {agent_id}))
        if attempt['phase'] == 'reinforcement':
            evidence = tuple(a['agent_id'] for a in active_attempts(question) if a['id'] != attempt['id'])
        launch = asyncio.create_task(
            self.containers.launch(
                LaunchSpec(
                    task_id=question.id,
                    agent=binding,
                    aoe_parent_id=parent_id,
                    aoe_auto_submit_flag=auto_submit_flag,
                    knowledge_source=question.metadata.get("knowledge_source", "online"),
                    fresh_session=fresh,
                    attempt_id=attempt["id"],
                    attempt_deadline=attempt["deadline"],
                    target_mode=self.targets.spec(question).mode,
                    target_addresses=tuple(self.targets.state(question.id).get("addresses", [])),
                    evidence_agents=evidence,
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

    async def _ensure_swarm(self, parent: TaskRun, question: TaskRun, workers: tuple[str, ...],
                            slots: dict[str, AgentRun], occupied: set[str]) -> TaskRun:
        """Promote in place: retain live attempt IDs and resume each returning Worker.

        The transition grants one joint-sized budget. Once persisted it is never
        renewed by ticks, late participants, pause/resume, or controller recovery.
        """
        if not scout_ready(question):
            return question
        active = active_attempts(question)
        current_agents = {a['agent_id'] for a in active}
        joining = tuple(agent for agent in workers if agent not in current_agents
                        and agent not in occupied and not slots[agent].metadata.get('aoe_paused'))
        swarm = question.metadata.get('swarm')
        if swarm and swarm.get('deadline') is not None and swarm['deadline'] <= self.clock():
            return question
        if swarm and not joining:
            return question
        if not await self._prepare_target(question, tuple(a['agent_id'] for a in active) + joining):
            return question
        await self._prepare_attachments(question)
        if question.id in self._recovering:
            for attempt in active:
                await self._launch_attempt(parent.id, question, attempt, fresh=False)
            self._recovering.discard(question.id)
        previous = {a['agent_id'] for a in question.metadata.get('attempts', [])}
        now = self.clock()
        if not swarm:
            started = now if any(a.get('started_at') is not None for a in active) else None
            swarm = {'entered_at': now, 'started_at': started,
                     'deadline': started + attempt_budgets(parent.metadata)['joint'] if started is not None else None}
        additions = [{'id': str(uuid4()), 'agent_id': agent, 'phase': 'swarm', 'claimed_at': now,
                      'started_at': swarm['started_at'], 'deadline': swarm['deadline'], 'outcome': None}
                     for agent in joining]
        entering = 'swarm' not in question.metadata
        question = await self.storage.mutate_task_metadata(question.id, lambda metadata: {
            **metadata, 'status': 'running', 'swarm': swarm, 'attempts': [
                {**a, 'previous_phase': a['phase'], 'previous_started_at': a.get('started_at'),
                 'previous_deadline': a.get('deadline'), 'phase': 'swarm',
                 'started_at': swarm['started_at'], 'deadline': swarm['deadline']}
                if a.get('outcome') is None and a['phase'] != 'swarm' else a
                for a in metadata.get('attempts', [])] + additions})
        await self._save_question(question)
        if entering:
            await self._copy_realtime(parent.id, question)
            await self._target_prompt(question, f'swarm:{question.id}')
            await self.board.publish(question.id, SYSTEM_ACTOR, PublishRequest(kind='user_prompt',
                topic='swarm', body={'text': '只剩最后一道题，开启围殴模式。四位 Worker 协同攻坚；'
                    '继续当前思路并读取本题黑板，及时共享可复现证据。共享一份共同攻坚预算。'},
                idempotency_key=f'swarm:{question.id}'))
            await self.events.emit(parent.id, actor=SYSTEM_ACTOR, kind=EventKind.SYSTEM,
                text=f'只剩最后一道题，开启四 Worker 围殴：{question.title}',
                payload={'question_id': str(question.id), 'phase': 'swarm',
                         'budget_seconds': attempt_budgets(parent.metadata)['joint']})
            # Do not stop or relaunch the live CLI. Deadline notifications refer
            # to its original attempt ID, so it can adopt the new shared budget.
            for attempt in active:
                await self.storage.update_agent(question.id, attempt['agent_id'], metadata={'aoe_phase': 'swarm'})
                if swarm['deadline'] is not None:
                    await self.coordinator.gateway.notify(question.id, attempt['agent_id'], 'session.deadline',
                        {'attempt_id': attempt['id'], 'deadline': swarm['deadline']})
        await asyncio.gather(*(self._launch_attempt(parent.id, question, attempt,
                                                  fresh=attempt['agent_id'] not in previous)
                               for attempt in additions))
        return question

    async def _reinforce(self, parent_id: UUID, question: TaskRun, agent_id: str) -> TaskRun:
        """Join round one with its existing deadline; never restart its owner."""
        if not await self._prepare_target(question, (agent_id,)):
            return question
        now = self.clock()
        rounds = solo_attempts(question)
        if not rounds or rounds[0].get('deadline') is None or rounds[0]['deadline'] <= now:
            return question

        def join(metadata):
            current = question.model_copy(update={'metadata': metadata})
            rounds = solo_attempts(current)
            if (metadata.get('rescue', {}).get('state') != 'seeking' or metadata.get('reinforcement')
                    or metadata.get('swarm') or len(rounds) != 1 or len(active_attempts(current)) != 1
                    or agent_id in attempted_by(current)):
                raise ConflictError('Question is not awaiting first-attempt reinforcement')
            first = rounds[0]
            if first.get('deadline') is None or first['deadline'] <= now:
                raise ConflictError('First attempt has no remaining reinforcement budget')
            addition = {'id': str(uuid4()), 'agent_id': agent_id, 'phase': 'reinforcement',
                        'first_attempt_id': first['id'], 'claimed_at': now, 'started_at': now,
                        'deadline': first['deadline'], 'outcome': None}
            return {**metadata, 'rescue': {**metadata['rescue'], 'state': 'reinforced'},
                    'reinforcement': {'agent_id': agent_id, 'joined_at': now, 'first_attempt_id': first['id']},
                    'attempts': [*metadata['attempts'], addition]}

        question = await self.storage.mutate_task_metadata(question.id, join)
        await self._copy_realtime(parent_id, question)
        await self._save_question(question)
        addition = question.metadata['attempts'][-1]
        await self.board.publish(question.id, SYSTEM_ACTOR, PublishRequest(kind='user_prompt',
            topic='reinforcement', body={'text': f'{agent_id} 加入首次尝试增援。原 Worker 继续，'
                '首次尝试的起算时间和截止时间保持不变；共享本题黑板并协作，增援不另领预算。'},
            idempotency_key=f'reinforcement:{addition["id"]}'))
        await self._launch_attempt(parent_id, question, addition, fresh=True)
        return question

    async def stop_worker(self, question_id: UUID, agent_id: str) -> None:
        """Stop an active Worker on a specific AOE child question."""
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

    async def _stop_worker(self, question_id: UUID, agent_id: str) -> None:
        await self.stop_worker(question_id, agent_id)

    async def record_pause(self, question_id: UUID, agent_id: str, *, paused: bool) -> None:
        """Record acknowledged pause intervals without touching stage budgets."""
        question = await self.storage.get_task(question_id)
        active = [a for a in active_attempts(question) if a['agent_id'] == agent_id]
        needs_change = any(paused != bool(a.get('pauses') and a['pauses'][-1].get('end') is None) for a in active)
        if not question.parent_task_id or not needs_change:
            return
        now = self.clock()

        def record(metadata):
            attempts = []
            for attempt in metadata.get('attempts', []):
                if attempt['agent_id'] == agent_id and attempt.get('outcome') is None:
                    pauses = [dict(p) for p in attempt.get('pauses', [])]
                    if paused and (not pauses or pauses[-1].get('end') is not None):
                        pauses.append({'start': now, 'end': None})
                    elif not paused and pauses and pauses[-1].get('end') is None:
                        pauses[-1]['end'] = now
                    attempt = {**attempt, 'pauses': pauses}
                attempts.append(attempt)
            return {**metadata, 'attempts': attempts}

        question = await self.storage.mutate_task_metadata(question_id, record)
        await self._save_question(question)

    async def _finish_attempts(self, question: TaskRun, agents: set[str], outcome: str) -> TaskRun:
        finished_at = self.clock()
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
                {**a, "outcome": outcome, "finished_at": finished_at, "cli_session_id": sessions.get(a["agent_id"])}
                if a.get("outcome") is None and a["agent_id"] in agents
                else a
                for a in metadata.get("attempts", [])
            ]
            rescue = metadata.get('rescue')
            first = next((a for a in attempts if a['phase'] == 'solo'), None)
            if rescue and rescue['state'] == 'seeking' and first and first.get('outcome') is not None:
                rescue = {**rescue, 'state': 'closed'}
            return {**metadata, "attempts": attempts, **({'rescue': rescue} if rescue else {})}

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

    async def protect_completion(self, question_id):
        question = await self.storage.get_task(question_id)
        if question.parent_task_id is not None:
            async with self.locks[question.parent_task_id]:
                return await self._protect_completion(await self.storage.get_task(question_id))
        return question

    async def _protect_completion(self, question):
        attempts = active_attempts(question)
        closing = [a for a in attempts if a.get('deadline') is not None
                   and a['deadline'] - self.clock() <= 30]
        if not closing:
            return question
        held = next((a.get('completion_hold_action') for a in closing if a.get('completion_hold_action')), None)
        evidence = held or next((found for a in closing
                                if (found := self.submissions.recent_flag(question, a))), None)
        if not evidence:
            return question
        now = self.clock()
        protected_ids = {a['id'] for a in attempts}
        question = await self.storage.mutate_task_metadata(question.id, lambda metadata: {
            **metadata,
            **({'swarm': {**metadata['swarm'], 'deadline': now + 120}} if metadata.get('swarm') else {}),
            'attempts': [{**a, 'deadline': now + 120, 'completion_hold_action': evidence}
                                   if a['id'] in protected_ids else a for a in metadata['attempts']]})
        await self._save_question(question)
        for attempt in active_attempts(question):
            await self.coordinator.gateway.notify(question.id, attempt['agent_id'], 'session.deadline',
                {'attempt_id': attempt['id'], 'deadline': attempt['deadline']})
        if not held:
            await self.events.emit(question.parent_task_id, actor=SYSTEM_ACTOR, kind=EventKind.SYSTEM,
                text=f"检测到 flag，延长提交收尾直到最终候选发布成功：{question.title}",
                payload={'question_id': str(question.id), 'action_id': evidence})
        return question

    async def extend_question_attempt(self, parent_id: UUID, question_id: UUID, *, minutes: int = 5) -> dict:
        """Manually extend the active round deadline for a single running AOE question."""
        if not isinstance(minutes, int) or not 1 <= minutes <= 240:
            raise ContractError("延长时间必须是 1 到 240 分钟之间的整数")
        max_total_extension = 240 * 60
        async with self.locks[parent_id]:
            parent = await self.storage.get_task(parent_id)
            if parent.scene_id != "aoe" or parent.parent_task_id is not None:
                raise ContractError("延长本轮时间仅支持 AOE 批量任务")
            if parent.state in TERMINAL:
                raise ConflictError("当前任务已结束，无法延长题目时间")
            question = await self.storage.get_task(question_id)
            if question.parent_task_id != parent_id:
                raise ContractError("题目不属于当前 AOE 任务")
            if question.metadata.get("status") != "running":
                raise ConflictError("仅可为正在解题中的题目延长本轮时间")
            active = active_attempts(question)
            if not active:
                raise ConflictError("本题当前没有正在运行的尝试")
            deadlines = [attempt["deadline"] for attempt in active if attempt.get("deadline") is not None]
            if not deadlines:
                raise ConflictError("本题尚未开始倒计时，待 Worker 执行首个动作开始计时后再延长")
            delta = minutes * 60
            existing_ext = max(int(attempt.get("manual_extension_seconds", 0) or 0) for attempt in active)
            if existing_ext + delta > max_total_extension:
                remaining_allowed = max(0, (max_total_extension - existing_ext) // 60)
                raise ConflictError(
                    f"单轮手动延长累计上限为 240 分钟，本轮还可延长最多 {remaining_allowed} 分钟"
                )
            now = self.clock()
            new_deadline = max(max(deadlines), now) + delta
            active_ids = {attempt["id"] for attempt in active}

            def extend(metadata):
                updated_attempts = [
                    {
                        **a,
                        "deadline": new_deadline,
                        "manual_extension_seconds": int(a.get("manual_extension_seconds", 0) or 0) + delta,
                        "manual_extension_count": int(a.get("manual_extension_count", 0) or 0) + 1,
                    }
                    if a["id"] in active_ids
                    else a
                    for a in metadata.get("attempts", [])
                ]
                return {
                    **metadata,
                    **(
                        {"swarm": {**metadata["swarm"], "deadline": new_deadline}}
                        if metadata.get("swarm")
                        else {}
                    ),
                    "attempts": updated_attempts,
                }

            question = await self.storage.mutate_task_metadata(question.id, extend)
            await self._save_question(question)
            agents = [attempt["agent_id"] for attempt in active_attempts(question)]
            for attempt in active_attempts(question):
                await self.coordinator.gateway.notify(
                    question.id,
                    attempt["agent_id"],
                    "session.deadline",
                    {"attempt_id": attempt["id"], "deadline": new_deadline},
                )
            await self.events.emit(
                parent_id,
                actor=SYSTEM_ACTOR,
                kind=EventKind.SYSTEM,
                text=f"人工延长本轮时间 +{minutes} 分钟：{question.title}",
                payload={
                    "question_id": str(question.id),
                    "added_minutes": minutes,
                    "deadline": new_deadline,
                    "agents": agents,
                },
            )
            self.wake(parent_id)
            return {
                "question_id": str(question.id),
                "added_minutes": minutes,
                "deadline": new_deadline,
                "agents": agents,
            }

    async def prepare_restart(
        self,
        parent_id: UUID,
        *,
        rerun_scout: bool = False,
        retry_abandoned: bool = False,
        budgets: dict[str, int] | None = None,
        restart_count: int = 1,
    ) -> dict:
        """Reset an ended AOE batch to resume from its highest completed checkpoint."""
        async with self.locks[parent_id]:
            parent = await self.storage.get_task(parent_id)
            await self.targets.release_parent(parent_id)
            questions = await self.storage.list_questions(parent_id)
            need_scout = rerun_scout or not parent.metadata.get("collection_complete") or not questions

            resumed_questions = 0
            requeued_abandoned = 0
            pending_reports = 0
            for question in questions:
                q_status = question.metadata.get("status", "queued")
                if q_status == "solved":
                    if (
                        not question.metadata.get("pre_solved")
                        and question.metadata.get("produce_report", True)
                        and question.state != RunState.COMPLETED
                    ):
                        await self.storage.update_task(question.id, state=RunState.RUNNING)
                        pending_reports += 1
                    continue

                if q_status == "abandoned":
                    if not retry_abandoned:
                        if (
                            self.automation is not None
                            and question.metadata.get("produce_report", True)
                            and question.state not in {RunState.COMPLETED, RunState.CANCELLED}
                        ):
                            await self.storage.update_task(question.id, state=RunState.RUNNING)
                            pending_reports += 1
                        continue

                    question = await self.storage.mutate_task_metadata(
                        question.id,
                        lambda m: clear_question_round_state(m, supersede_all=True),
                    )
                    question = await self.storage.update_task(question.id, state=RunState.RUNNING)
                    await self.board.publish(
                        question.id,
                        SYSTEM_ACTOR,
                        PublishRequest(
                            kind="user_prompt",
                            topic="restart",
                            body={
                                "text": (
                                    f"任务已重新启动（第 {restart_count} 次运行），本题从“已放弃”恢复重新挑战。"
                                    "此前黑板中的 Findings 与工作区文件均已保留，动态靶场将重新分配，"
                                    "请先查阅历史结论并确认环境后继续推进。"
                                )
                            },
                            idempotency_key=f"aoe-restart:{restart_count}:{question.id}",
                        ),
                    )
                    await self._save_question(question)
                    requeued_abandoned += 1
                    continue

                # Unfinished / interrupted / cancelled questions return to queued without burning interrupted slots.
                question = await self.storage.mutate_task_metadata(
                    question.id,
                    lambda m: clear_question_round_state(m, supersede_interrupted=True),
                )
                question = await self.storage.update_task(question.id, state=RunState.RUNNING)
                await self.board.publish(
                    question.id,
                    SYSTEM_ACTOR,
                    PublishRequest(
                        kind="user_prompt",
                        topic="restart",
                        body={
                            "text": (
                                f"任务已重新启动（第 {restart_count} 次运行）。"
                                "此前黑板中的记录与工作区文件已保留，动态靶场将重新启动，"
                                "请先检查已有线索与最新地址后继续解题。"
                            )
                        },
                        idempotency_key=f"aoe-restart:{restart_count}:{question.id}",
                    ),
                )
                await self._save_question(question)
                resumed_questions += 1

            if not need_scout and resumed_questions == 0 and requeued_abandoned == 0 and pending_reports == 0:
                raise ConflictError(
                    "所有题目均已结束且无待补跑报告；如需重跑已放弃题目请勾选“重新挑战已放弃题目”，"
                    "或使用“基于此任务新建”开启全新运行。"
                )

            parent_meta: dict = {
                "restart_count": restart_count,
                "aoe_phase": "collecting" if need_scout else "solo",
                "collection_complete": not need_scout,
                "collection_status": (
                    "collecting" if need_scout else parent.metadata.get("collection_status", "complete")
                ),
                "scout_complete": not need_scout,
            }
            if budgets:
                parent_meta["aoe_attempt_budgets"] = budgets
            parent = await self.storage.update_task(
                parent_id,
                state=RunState.SCOUTING if need_scout else RunState.RUNNING,
                metadata=parent_meta,
            )
            if need_scout:
                await self.storage.update_agent(
                    parent_id,
                    "scout",
                    desired_state=AgentState.RUNNING,
                    actual_state=AgentState.CREATED,
                    container_id=None,
                    last_error=None,
                    metadata={"scout_phase": "collecting"},
                )
            refreshed = await self.storage.list_questions(parent_id)
            await self._collection_state(parent_id, parent=parent, questions=refreshed)
            return {
                "resumed_from": "scout" if need_scout else "workers",
                "need_scout": need_scout,
                "resumed_questions": resumed_questions,
                "requeued_abandoned": requeued_abandoned,
                "pending_reports": pending_reports,
            }

    async def _settle(self, question: TaskRun, *, status: str, flag: str | None = None,
                      solver_agent_id: str | None = None) -> TaskRun:
        active = active_attempts(question)
        agents = {attempt["agent_id"] for attempt in active}
        credits = {attempt['id']: True for attempt in active}
        helper = next((a for a in active if a['phase'] == 'reinforcement'), None)
        if status == 'solved' and helper and not any(a['phase'] in {'joint', 'swarm'} for a in active):
            first_id = helper['first_attempt_id']
            credits[first_id] = True
            credits[helper['id']] = helper['agent_id'] == solver_agent_id
        if agents:
            question = await self._finish_attempts(question, agents, "solved" if status == "solved" else "timeout")
        question = await self.storage.update_task(question.id, metadata={"status": status, "flag": flag,
                                               **({'solver_agent_id': solver_agent_id,
                                                   'attempts': [{**a, 'success_credit': credits[a['id']]}
                                                       if a['id'] in credits else a
                                                       for a in question.metadata.get('attempts', [])]}
                                                  if status == 'solved' else {}),
                                               **({'finish_reason': 'joint_failed'} if status == 'abandoned'
                                                  and question.metadata.get('swarm') else {}),
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
            questions = await self.storage.list_questions(parent_id)
            if reconcile:
                await self._reconcile_inputs(parent_id, questions)
                if not (self.config.task_path(parent_id) / "collection-state.json").is_file():
                    await self._collection_state(parent_id, parent=parent, questions=questions)

            def replace(question):
                for index, current in enumerate(questions):
                    if current.id == question.id:
                        questions[index] = question
                        break

            # Settle all published solutions first, irrespective of source order,
            # then decide whether the entire batch has one question remaining.
            for question in unfinished(questions):
                candidate = await self._candidate(question)
                if candidate is not None:
                    replace(await self._settle(question, status="solved", flag=candidate.body["conclusion"],
                        solver_agent_id=candidate.actor.agent_id if candidate.actor.role == 'worker' else None))
            slots = {run.agent_id: run for run in await self.storage.list_agents(parent_id)}
            workers = tuple(agent for agent in self.config.worker_agent_ids if agent in slots)
            remaining = unfinished(questions)
            swarm_question = next((q for q in remaining if q.metadata.get('swarm')), None)
            if parent.metadata.get("collection_complete") and len(workers) == 4 and len(remaining) == 1:
                swarm_question = remaining[0]
            for question in remaining:
                question = await self._protect_completion(question)
                if swarm_question is not None and question.id == swarm_question.id:
                    occupied_elsewhere = {a['agent_id'] for q in questions if q.id != question.id
                                          for a in active_attempts(q)}
                    question = await self._ensure_swarm(parent, question, workers, slots, occupied_elsewhere)
                replace(question)
                active = active_attempts(question)
                expired = {attempt["agent_id"] for attempt in active
                           if attempt.get("deadline") is not None and attempt["deadline"] <= self.clock()}
                swarm_deadline = question.metadata.get('swarm', {}).get('deadline')
                if expired or swarm_deadline is not None and swarm_deadline <= self.clock():
                    if question.metadata.get('swarm') or any(a["phase"] == "joint" for a in active):
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
            available = [
                agent_id
                for agent_id in workers
                if agent_id not in occupied and not slots[agent_id].metadata.get("aoe_paused")
            ]
            eligible = schedulable_questions(questions,
                         collection_complete=parent.metadata.get("collection_complete", False))
            statistics = first_success_statistics(questions)
            if len(workers) == 4:
                for question in eligible:
                    threshold = rescue_after(question, statistics)
                    if threshold is not None and self.clock() > threshold and not question.metadata.get('rescue'):
                        question = await self.storage.update_task(question.id, metadata={
                            'rescue': {'state': 'seeking', 'requested_at': self.clock(), 'threshold_at': threshold}})
                        replace(question)
                        await self.events.emit(parent_id, actor=SYSTEM_ACTOR, kind=EventKind.SYSTEM,
                            text=f'首次尝试耗时超过个人同难度均值，正在求援：{question.title}',
                            payload={'question_id': str(question.id), 'mode': 'rescue'})
                eligible = schedulable_questions(questions,
                    collection_complete=parent.metadata.get('collection_complete', False))
            phase = "joint" if parent.metadata.get("collection_complete") and joint_ready(eligible, workers) else "solo"
            if any(a["phase"] == "joint" for q in questions for a in active_attempts(q)):
                phase = "joint"
            if swarm_question is not None and unfinished(questions):
                phase = 'swarm'
            if parent.metadata.get("collection_complete") and questions and not unfinished(questions):
                phase = "reporting"
            parent = await self._update_parent(parent, metadata={"aoe_phase": phase})
            if phase == "solo":
                for agent_id in available:
                    assigned = False
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
                        assigned = True
                        eligible = schedulable_questions(questions,
                                     collection_complete=parent.metadata.get("collection_complete", False))
                        break
                    # A Worker reserved for target preparation is not idle.
                    reserved = any(agent_id in self._target_claims.get(q.id, ()) for q in questions)
                    if not assigned and not reserved and len(workers) == 4:
                        requests = [q for q in questions if q.metadata.get('rescue', {}).get('state') == 'seeking'
                            and not q.metadata.get('swarm') and not q.metadata.get('reinforcement')
                            and len(active_attempts(q)) == 1 and agent_id not in attempted_by(q)
                            and len(solo_attempts(q)) == 1 and solo_attempts(q)[0].get('outcome') is None]
                        if requests:
                            replace(await self._reinforce(parent_id, min(requests, key=question_order), agent_id))
            elif phase == "joint" and not occupied:
                candidates = [q for q in eligible if len(joint_workers(q, workers)) == 2
                              and set(joint_workers(q, workers)) <= set(available)]
                quota_blocked = self.targets.quota_blocked(eligible)
                while True:
                    offline = [q for q in candidates if self.targets.spec(q).mode == "none"]
                    question = next((q for q in candidates if q.id in self._target_claims), None)
                    question = question or (next_joint_question(offline) if quota_blocked else None)
                    question = question or next_joint_question(candidates)
                    if question is None:
                        break
                    pair = joint_workers(question, workers)
                    if not await self._prepare_target(question, pair):
                        if question.id in self._target_claims:
                            break
                        quota_blocked |= self.targets.state(question.id).get("reason") == "quota"
                        candidates = [q for q in candidates if q.id != question.id]
                        continue
                    replace(await self._new_attempts(parent_id, question, pair, "joint"))
                    break
            for question in questions:
                await self._save_question(question)
            await self._sync_slots(parent_id, questions, slots)
            due = [a["deadline"] - (30 if a["deadline"] - self.clock() > 30 else 0)
                   for q in questions for a in active_attempts(q) if a.get("deadline") is not None]
            if len(workers) == 4:
                due.extend(threshold + 0.001 for q in unfinished(questions)
                           if (threshold := rescue_after(q, statistics)) is not None and threshold >= self.clock())
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
            if agent_id not in slots:
                continue
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
            question = await self._protect_completion(question)
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
        self.attachments.clear_all()
        self.views.clear_all()
        self._agent_sources.clear()
        self._parents.clear()
        self._next_due.clear()
        self._wakeups.clear()
        await self.targets.close()
