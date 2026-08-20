"""Supervisor-facing dialogue stream and the single Q&A path."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from uuid import UUID

from .domain import (
    SYSTEM_ACTOR,
    USER_ACTOR,
    Actor,
    BlackboardEntry,
    DialogueKind,
    DialogueMessage,
    PendingQuestion,
)
from .storage import Storage

DialogueHook = Callable[[UUID, int], Awaitable[None]]


class SolverDialogue:
    def __init__(self, storage: Storage, on_change: DialogueHook | None = None) -> None:
        self.storage = storage
        self.on_change = on_change

    async def emit(
        self,
        task_id: UUID,
        *,
        actor: Actor,
        kind: DialogueKind,
        text: str,
        channel_agent_id: str = "supervisor",
        payload: dict | None = None,
    ) -> DialogueMessage:
        message = await self.storage.append_dialogue(
            task_id,
            actor,
            kind,
            text,
            channel_agent_id=channel_agent_id,
            payload=payload,
        )
        if self.on_change:
            await self.on_change(task_id, message.seq)
        return message

    async def announce_status(self, task_id: UUID, agent: Actor, state: str, detail: str = "") -> DialogueMessage:
        text = f"{agent.display_name} 状态变更为 {state}" + (f"：{detail}" if detail else "")
        return await self.emit(
            task_id,
            actor=SYSTEM_ACTOR,
            kind=DialogueKind.AGENT_STATUS,
            text=text,
            payload={"agent_id": agent.agent_id, "state": state, "detail": detail},
        )

    async def announce_blackboard(self, task_id: UUID, latest_seq: int, text: str) -> DialogueMessage:
        return await self.emit(
            task_id,
            actor=SYSTEM_ACTOR,
            kind=DialogueKind.BLACKBOARD_PROGRESS,
            text=text,
            payload={"latest_seq": latest_seq},
        )

    async def ask(
        self,
        task_id: UUID,
        *,
        supervisor: Actor,
        question: str,
        origin: Actor | None = None,
    ) -> PendingQuestion:
        origin = origin or supervisor
        pending = await self.storage.create_question(
            PendingQuestion(task_id=task_id, question=question, origin=origin, asked_by=supervisor)
        )
        prefix = "Supervisor 提问" if origin.agent_id == supervisor.agent_id else f"代 {origin.display_name} 提问"
        await self.emit(
            task_id,
            actor=supervisor,
            kind=DialogueKind.QUESTION,
            text=f"{prefix}：{question}",
            payload={"question_id": str(pending.id), "origin_agent_id": origin.agent_id},
        )
        return pending

    async def answer(
        self,
        question_id: UUID,
        *,
        answer: str,
        attachment_ids: Sequence[UUID] = (),
        user: Actor = USER_ACTOR,
        idempotency_key: str,
    ) -> BlackboardEntry:
        entry = await self.storage.answer_question(
            question_id,
            answer=answer,
            attachment_ids=attachment_ids,
            user=user,
            idempotency_key=idempotency_key,
        )
        if self.on_change:
            task = await self.storage.get_task(entry.task_id)
            await self.on_change(entry.task_id, task.dialogue_seq)
        return entry

    async def history(self, task_id: UUID, *, after_seq: int = 0, limit: int = 500) -> list[DialogueMessage]:
        return await self.storage.list_dialogue(task_id, after_seq=after_seq, limit=limit)


__all__ = ["SolverDialogue"]
