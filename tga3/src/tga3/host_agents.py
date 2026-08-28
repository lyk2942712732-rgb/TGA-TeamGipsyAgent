"""Host-side Supervisor advisor and final Reporter using OpenAI Agents SDK."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .blackboard import Blackboard
from .config import ResolvedAgent, TGA3Config
from .dialogue import SolverDialogue
from .domain import (
    SYSTEM_ACTOR,
    Actor,
    AgentState,
    BlackboardEntry,
    DialogueKind,
    EntryKind,
    PublishRequest,
    RunState,
    utc_now,
)
from .skills import SkillCatalog
from .storage import Storage

AdviceReason = Literal[
    "conflict",
    "duplicate_work",
    "stalled",
    "finding_coordination",
    "worker_blocked",
    "user_request",
]


class SupervisorAdvice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1)
    reason: AdviceReason
    addressed_to: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def normalize_nullable_addressed_to(cls, value: Any) -> Any:
        if isinstance(value, dict) and value.get("addressed_to") is None:
            return {**value, "addressed_to": []}
        return value


class SupervisorDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    progress: str = Field(min_length=1)
    advice: SupervisorAdvice | None = None
    question: str | None = None


class HostModel(Protocol):
    async def supervisor(self, snapshot: str, binding: ResolvedAgent | None = None) -> SupervisorDecision: ...
    async def report(self, snapshot: str, binding: ResolvedAgent | None = None) -> str: ...


class OpenAIHostModel:
    def __init__(self, config: TGA3Config, skills: SkillCatalog) -> None:
        self.config = config
        self.skills = skills

    @staticmethod
    def _model(binding: ResolvedAgent):
        from agents import AsyncOpenAI, OpenAIChatCompletionsModel, OpenAIResponsesModel

        client = AsyncOpenAI(
            api_key=binding.api_key.get_secret_value(),
            base_url=binding.provider.sdk_base_url(binding.protocol),
        )
        if binding.protocol == "openai_chat_completions":
            return OpenAIChatCompletionsModel(model=binding.model.name, openai_client=client)
        return OpenAIResponsesModel(model=binding.model.name, openai_client=client)

    @staticmethod
    def _supervisor_instructions(binding: ResolvedAgent) -> str:
        if binding.protocol != "openai_chat_completions":
            return binding.system_prompt
        schema = json.dumps(SupervisorDecision.model_json_schema(), ensure_ascii=False, separators=(",", ":"))
        return (
            f"{binding.system_prompt}\n\n"
            "你的最终回复必须是一个 JSON 对象，不要使用 Markdown 代码块，也不要添加 JSON 之外的文字。"
            f"JSON 必须严格符合这个 schema：{schema}"
        )

    @staticmethod
    def _parse_supervisor_decision(value: Any) -> SupervisorDecision:
        if isinstance(value, SupervisorDecision):
            return value
        if isinstance(value, dict):
            return SupervisorDecision.model_validate(value)
        text = str(value or "").strip()
        decoder = json.JSONDecoder()
        for index, character in enumerate(text):
            if character != "{":
                continue
            try:
                payload, _ = decoder.raw_decode(text[index:])
            except json.JSONDecodeError:
                continue
            return SupervisorDecision.model_validate(payload)
        raise ValueError("Supervisor did not return a valid JSON decision")

    async def supervisor(self, snapshot: str, binding: ResolvedAgent | None = None) -> SupervisorDecision:
        from agents import Agent, Runner, function_tool, set_tracing_disabled

        set_tracing_disabled(True)
        binding = binding or self.config.resolve_agent("supervisor")

        @function_tool
        def skills_list() -> list[dict[str, Any]]:
            """List role-neutral skill packages by name and summary."""
            return [item.__dict__ for item in self.skills.list()]

        @function_tool
        def skill_read(name: str, path: str = "SKILL.md") -> str:
            """Read one package document. Start with SKILL.md and load referenced Markdown only as needed."""
            return self.skills.read(name, path)

        agent_kwargs: dict[str, Any] = {
            "name": binding.display_name,
            "model": self._model(binding),
            "tools": [skills_list, skill_read],
            "instructions": self._supervisor_instructions(binding),
        }
        if binding.protocol != "openai_chat_completions":
            agent_kwargs["output_type"] = SupervisorDecision
        agent = Agent(
            **agent_kwargs,
        )
        result = await Runner.run(agent, snapshot, max_turns=binding.max_turns_per_cycle)
        return self._parse_supervisor_decision(result.final_output)

    async def report(self, snapshot: str, binding: ResolvedAgent | None = None) -> str:
        from agents import Agent, Runner, function_tool, set_tracing_disabled

        set_tracing_disabled(True)
        binding = binding or self.config.resolve_agent("reporter")

        @function_tool
        def skills_list() -> list[dict[str, Any]]:
            """List role-neutral skill packages by name, including writeup guidance."""
            return [item.__dict__ for item in self.skills.list()]

        @function_tool
        def skill_read(name: str, path: str = "SKILL.md") -> str:
            """Read one package document. Start with SKILL.md and load referenced Markdown only as needed."""
            return self.skills.read(name, path)

        agent = Agent(
            name=binding.display_name,
            model=self._model(binding),
            instructions=binding.system_prompt,
            tools=[skills_list, skill_read],
        )
        result = await Runner.run(agent, snapshot, max_turns=binding.max_turns_per_cycle)
        return str(result.final_output)


class DeterministicHostModel:
    """Offline test model; production selects OpenAIHostModel."""

    async def supervisor(self, snapshot: str, binding: ResolvedAgent | None = None) -> SupervisorDecision:
        return SupervisorDecision(progress="黑板已有更新，两个 Worker 正在独立推进。")

    async def report(self, snapshot: str, binding: ResolvedAgent | None = None) -> str:
        return f"# TGA3 Writeup\n\n## 固定黑板快照\n\n```json\n{snapshot}\n```\n"


@dataclass
class _SupervisorPending:
    latest_seq: int = 0
    first_intel_at: float | None = None
    last_change_at: float = 0
    immediate: bool = False
    event: asyncio.Event = field(default_factory=asyncio.Event)


def _similar_advice(left: str, right: str) -> bool:
    normalized_left = re.sub(r"[\W_]+", "", left.casefold())
    normalized_right = re.sub(r"[\W_]+", "", right.casefold())
    if not normalized_left or not normalized_right:
        return False
    if normalized_left == normalized_right:
        return True
    if min(len(normalized_left), len(normalized_right)) >= 12 and (
        normalized_left in normalized_right or normalized_right in normalized_left
    ):
        return True
    return SequenceMatcher(None, normalized_left, normalized_right).ratio() >= 0.82


class Automation:
    """Debounced advisor plus one-shot reporter. No graph scheduler is involved."""

    def __init__(
        self,
        config: TGA3Config,
        storage: Storage,
        blackboard: Blackboard,
        dialogue: SolverDialogue,
        model: HostModel,
        on_reporter_finished: Callable[[UUID], Awaitable[None]] | None = None,
    ) -> None:
        self.config = config
        self.storage = storage
        self.blackboard = blackboard
        self.dialogue = dialogue
        self.model = model
        self.on_reporter_finished = on_reporter_finished
        self._supervisor_jobs: dict[UUID, asyncio.Task[None]] = {}
        self._reporter_jobs: dict[UUID, asyncio.Task[None]] = {}
        self._last_supervisor_seq: dict[UUID, int] = {}
        self._supervisor_pending: dict[UUID, _SupervisorPending] = {}

    async def changed(self, task_id: UUID, latest_seq: int) -> None:
        task = await self.storage.get_task(task_id)
        if task.state in {RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED}:
            return
        entries = await self.storage.list_entries(task_id, after_seq=max(0, latest_seq - 1), limit=1)
        if not entries:
            return
        latest = entries[-1]
        if latest.kind == EntryKind.FINAL_CANDIDATE:
            if task_id not in self._reporter_jobs:
                self._reporter_jobs[task_id] = asyncio.create_task(self._report(task_id))
            return
        if latest.kind == EntryKind.SUPERVISOR_ADVICE or (
            latest.kind == EntryKind.USER_PROMPT and latest.topic == "scene"
        ):
            return
        loop = asyncio.get_running_loop()
        pending = self._supervisor_pending.setdefault(task_id, _SupervisorPending())
        pending.latest_seq = max(pending.latest_seq, latest_seq)
        pending.last_change_at = loop.time()
        if latest.kind == EntryKind.INTEL:
            pending.first_intel_at = pending.first_intel_at or pending.last_change_at
        if latest.kind in {EntryKind.FINDING, EntryKind.QA}:
            pending.immediate = True
        pending.event.set()
        job = self._supervisor_jobs.get(task_id)
        if job is None or job.done():
            self._supervisor_jobs[task_id] = asyncio.create_task(self._supervise(task_id, pending))

    async def cancel(self, task_id: UUID) -> None:
        jobs = [
            job
            for job in (self._supervisor_jobs.pop(task_id, None), self._reporter_jobs.pop(task_id, None))
            if job is not None and not job.done()
        ]
        for job in jobs:
            job.cancel()
        if jobs:
            await asyncio.gather(*jobs, return_exceptions=True)
        self._last_supervisor_seq.pop(task_id, None)
        self._supervisor_pending.pop(task_id, None)

    async def _supervise(self, task_id: UUID, pending: _SupervisorPending) -> None:
        loop = asyncio.get_running_loop()
        cadence = self.config.runtime.cadence
        while True:
            if pending.latest_seq == 0:
                pending.event.clear()
                try:
                    await asyncio.wait_for(pending.event.wait(), timeout=cadence.supervisor_silence_seconds)
                except TimeoutError:
                    task = await self.storage.get_task(task_id)
                    if task.state in {RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED}:
                        return
                    if task.state == RunState.RUNNING:
                        await self._advise(task_id, task.blackboard_seq, silence=True)
                continue

            now = loop.time()
            if pending.immediate:
                ready_at = now
            else:
                ready_at = pending.last_change_at + cadence.supervisor_debounce_seconds
                if pending.first_intel_at is not None:
                    ready_at = min(ready_at, pending.first_intel_at + cadence.supervisor_intel_max_wait_seconds)
            delay = max(0.0, ready_at - now)
            if delay:
                pending.event.clear()
                try:
                    await asyncio.wait_for(pending.event.wait(), timeout=delay)
                    continue
                except TimeoutError:
                    pass

            trigger_seq = pending.latest_seq
            pending.latest_seq = 0
            pending.first_intel_at = None
            pending.immediate = False
            processed_seq = await self._advise(task_id, trigger_seq)
            if pending.latest_seq and pending.latest_seq <= processed_seq:
                pending.latest_seq = 0
                pending.first_intel_at = None
                pending.immediate = False

    async def _advise(self, task_id: UUID, trigger_seq: int, *, silence: bool = False) -> int:
        processed_seq = trigger_seq
        task = await self.storage.get_task(task_id)
        if task.state in {RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED}:
            return task.blackboard_seq
        last = self._last_supervisor_seq.get(task_id, 0)
        if trigger_seq <= last and not silence:
            return last
        configured = self.config.agents.agents["supervisor"]
        run = await self.storage.get_agent(task_id, "supervisor")
        binding = self.config.resolve_agent(
            "supervisor", run.provider_id, run.model_id, run.protocol, require_api_key=False
        )
        supervisor = Actor(
            agent_id="supervisor",
            display_name=configured.display_name,
            role="supervisor",
            sdk=configured.runtime,
            model=run.model_id,
        )
        await self.storage.update_agent(
            task_id,
            "supervisor",
            actual_state=AgentState.RUNNING,
            last_error=None,
        )
        await self.dialogue.announce_status(task_id, supervisor, AgentState.RUNNING.value)
        try:
            sync = await self.blackboard.sync(task_id, after_seq=0)
            processed_seq = sync.latest_seq
            delta = [
                entry
                for entry in sync.entries
                if entry.seq > last and entry.kind != EntryKind.SUPERVISOR_ADVICE
            ]
            durable = [
                entry
                for entry in sync.entries
                if entry.kind
                in {
                    EntryKind.USER_PROMPT,
                    EntryKind.USER_FILE,
                    EntryKind.FINDING,
                    EntryKind.QA,
                    EntryKind.FINAL_CANDIDATE,
                }
            ]
            recent_intel = [entry for entry in sync.entries if entry.kind == EntryKind.INTEL][-20:]
            advice_entries = [entry for entry in sync.entries if entry.kind == EntryKind.SUPERVISOR_ADVICE]
            recent_advice = advice_entries[-20:]
            agents = await self.storage.list_agents(task_id)
            snapshot = json.dumps(
                {
                    "latest_seq": sync.latest_seq,
                    "reason": "silence_check" if silence else "blackboard_changed",
                    "new_entries": [entry.model_dump(mode="json") for entry in delta],
                    "context": [
                        entry.model_dump(mode="json")
                        for entry in durable + recent_intel + advice_entries[-4:]
                    ],
                    "agents": [agent.model_dump(mode="json") for agent in agents],
                },
                ensure_ascii=False,
            )
            decision = await self.model.supervisor(snapshot, binding)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self.storage.update_agent(
                task_id, "supervisor", actual_state=AgentState.FAILED, last_error=str(exc)
            )
            await self.dialogue.emit(
                task_id,
                actor=supervisor,
                kind=DialogueKind.ERROR,
                text=f"Supervisor 运行失败：{exc}",
            )
            self._last_supervisor_seq[task_id] = processed_seq
            return processed_seq
        finally:
            current = await self.storage.get_agent(task_id, "supervisor")
            if current.actual_state != AgentState.FAILED:
                await self.storage.update_agent(
                    task_id,
                    "supervisor",
                    actual_state=AgentState.IDLE,
                    last_error=None,
                )
                await self.dialogue.announce_status(task_id, supervisor, AgentState.IDLE.value)
        advice = decision.advice.text if decision.advice else None
        suppression = self._advice_suppression(
            advice,
            recent_advice,
            bypass_interval=any(
                entry.kind in {EntryKind.USER_PROMPT, EntryKind.FINDING} for entry in delta
            ),
        )
        progress = decision.progress if suppression is None else f"{decision.progress}（建议未写入：{suppression}）"
        await self.dialogue.announce_blackboard(task_id, sync.latest_seq, progress)
        if advice and suppression is None:
            await self.blackboard.publish(
                task_id,
                supervisor,
                PublishRequest(
                    kind=EntryKind.SUPERVISOR_ADVICE,
                    topic="advice",
                    body={
                        "advice": advice,
                        "addressed_to": decision.advice.addressed_to,
                        "based_on_seq": sync.latest_seq,
                    },
                    idempotency_key=(
                        f"advice-{sync.latest_seq}-"
                        f"{hashlib.sha256(advice.encode('utf-8')).hexdigest()[:12]}"
                    ),
                ),
            )
        if decision.question:
            await self.dialogue.ask(task_id, supervisor=supervisor, question=decision.question)
            await self.storage.update_task(task_id, state=RunState.WAITING_USER)
        self._last_supervisor_seq[task_id] = sync.latest_seq
        return sync.latest_seq

    def _advice_suppression(
        self,
        advice: str | None,
        recent_advice: list[BlackboardEntry],
        *,
        bypass_interval: bool,
    ) -> str | None:
        if not advice:
            return None
        if any(_similar_advice(advice, str(entry.body.get("advice", ""))) for entry in recent_advice):
            return "与近期建议相同或近似"
        if bypass_interval or not recent_advice:
            return None
        elapsed = (utc_now() - recent_advice[-1].created_at).total_seconds()
        interval = self.config.runtime.cadence.supervisor_advice_interval_seconds
        if elapsed < interval:
            return f"Intel 建议间隔尚余 {max(1, int(interval - elapsed))} 秒"
        return None

    async def _report(self, task_id: UUID) -> None:
        await self.storage.update_task(task_id, state=RunState.FINALIZING)
        await asyncio.sleep(self.config.runtime.cadence.finalization_grace_seconds)
        task = await self.storage.get_task(task_id)
        snapshot_seq = task.blackboard_seq
        await self.storage.update_task(task_id, state=RunState.REPORTING, final_snapshot_seq=snapshot_seq)
        configured = self.config.agents.agents["reporter"]
        run = await self.storage.get_agent(task_id, "reporter")
        binding = self.config.resolve_agent(
            "reporter", run.provider_id, run.model_id, run.protocol, require_api_key=False
        )
        reporter = Actor(
            agent_id="reporter",
            display_name=configured.display_name,
            role="reporter",
            sdk=configured.runtime,
            model=run.model_id,
        )
        await self.storage.update_agent(task_id, "reporter", actual_state=AgentState.RUNNING)
        await self.dialogue.announce_status(task_id, reporter, AgentState.RUNNING.value)
        entries = await self.storage.list_entries(task_id, up_to_seq=snapshot_seq, limit=10_000)
        snapshot = json.dumps([entry.model_dump(mode="json") for entry in entries], ensure_ascii=False, indent=2)
        try:
            markdown = await self.model.report(snapshot, binding)
            root = self.config.resolve_path(self.config.runtime.writeup_root) / str(task_id)
            root.mkdir(parents=True, exist_ok=True)
            path = root / "writeup.md"
            path.write_text(markdown, encoding="utf-8")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            await self.storage.save_writeup(task_id, snapshot_seq, str(path), digest)
            if self.on_reporter_finished:
                await self.on_reporter_finished(task_id)
        except Exception as exc:
            if self.on_reporter_finished:
                with suppress(Exception):
                    await self.on_reporter_finished(task_id)
            await self.storage.update_agent(
                task_id,
                "reporter",
                desired_state=AgentState.FAILED,
                actual_state=AgentState.FAILED,
                last_error=str(exc),
            )
            supervisor_run = await self.storage.get_agent(task_id, "supervisor")
            if supervisor_run.actual_state != AgentState.FAILED:
                await self.storage.update_agent(
                    task_id,
                    "supervisor",
                    desired_state=AgentState.STOPPED,
                    actual_state=AgentState.STOPPED,
                )
            await self.storage.update_task(task_id, state=RunState.FAILED)
            await self.dialogue.emit(
                task_id,
                actor=reporter,
                kind=DialogueKind.ERROR,
                text=f"Reporter 运行失败：{exc}",
                channel_agent_id="reporter",
            )
            return
        await self.storage.update_agent(
            task_id,
            "reporter",
            desired_state=AgentState.COMPLETED,
            actual_state=AgentState.COMPLETED,
        )
        await self.storage.update_agent(
            task_id,
            "supervisor",
            desired_state=AgentState.COMPLETED,
            actual_state=AgentState.COMPLETED,
        )
        await self.dialogue.announce_status(task_id, reporter, AgentState.COMPLETED.value)
        await self.storage.update_task(task_id, state=RunState.COMPLETED)
        await self.dialogue.emit(
            task_id,
            actor=SYSTEM_ACTOR,
            kind=DialogueKind.SYSTEM,
            text="最终结论已固定，Markdown writeup 已生成。",
            payload={"snapshot_seq": snapshot_seq, "download_path": str(path)},
        )


__all__ = [
    "Automation",
    "DeterministicHostModel",
    "OpenAIHostModel",
    "SupervisorAdvice",
    "SupervisorDecision",
]
