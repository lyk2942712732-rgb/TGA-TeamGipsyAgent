"""AOE Scout collection lifecycle runner: launches Scout, watches outbox, and finalizes handoff."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from uuid import UUID

from .aoe_models import scout_ready
from .docker_runtime import LaunchSpec
from .domain import SYSTEM_ACTOR, AgentState, EventKind, RunState
from .errors import ContractError

if TYPE_CHECKING:
    from .aoe import AOERuntime


class AOEScoutRunner:
    """Runs the AOE Scout collection stage independently of the Worker attempt scheduler."""

    def __init__(self, aoe: AOERuntime) -> None:
        self.aoe = aoe

    async def run(self, parent_id: UUID, bindings: dict) -> None:
        aoe = self.aoe
        parent = await aoe.storage.get_task(parent_id)
        await aoe.overview.prepare(parent_id)
        aoe.collection.ensure_directories(parent_id)
        await aoe.refresh_collection_state(parent_id, parent=parent)

        async def emit(method: str, params: dict) -> None:
            if method == "container.started":
                await aoe.storage.update_agent(
                    parent_id, "scout", container_id=params["container_id"], actual_state=AgentState.RUNNING
                )
            else:
                await aoe.coordinator.handle_agent_event(parent_id, "scout", method, params)

        async def watch_outbox() -> None:
            while True:
                try:
                    current = await aoe.storage.get_task(parent_id)
                    if current.metadata.get("collection_complete"):
                        return
                    if aoe.collection.has_pending(parent_id):
                        await aoe.collection.import_outbox(parent_id)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    pass
                await asyncio.sleep(0.5)

        watcher: asyncio.Task | None = None
        scout_error: str | None = None
        try:
            await aoe.storage.update_task(parent_id, metadata={"aoe_scout_mode": "collection_only"})
            if not parent.metadata.get("collection_complete"):
                budget = aoe.config.runtime.aoe_collection_timeout_seconds
                await aoe.storage.update_agent(
                    parent_id, "scout", actual_state=AgentState.RUNNING, metadata={"scout_phase": "collecting"}
                )
                await aoe.events.emit(
                    parent_id,
                    actor=SYSTEM_ACTOR,
                    kind=EventKind.SYSTEM,
                    text="Scout 收集题目，Worker 随登记并行认领简单题或无难度题",
                    channel_agent_id="scout",
                    payload={"scout_phase": "collecting"},
                )
                watcher = asyncio.create_task(watch_outbox())
                try:
                    async with asyncio.timeout(budget + 15):
                        await aoe.containers.scout(
                            LaunchSpec(
                                task_id=parent_id,
                                agent=bindings["scout"],
                                scout_phase="collecting",
                                phase_budget_seconds=budget,
                                aoe_parent_id=parent_id,
                            ),
                            emit,
                        )
                except TimeoutError:
                    scout_error = f"Scout collection timed out after {budget}s"
                except Exception as exc:
                    scout_error = str(exc) or type(exc).__name__
                finally:
                    if watcher is not None:
                        watcher.cancel()
                        await asyncio.gather(watcher, return_exceptions=True)
                        watcher = None
                if aoe.collection.has_pending(parent_id):
                    current = await aoe.storage.get_task(parent_id)
                    if not current.metadata.get("collection_complete"):
                        await aoe.collection.import_outbox(parent_id)

            parent = await aoe.storage.get_task(parent_id)
            questions = await aoe.storage.list_questions(parent_id)
            if not parent.metadata.get("collection_complete"):
                if questions:
                    warning = (
                        scout_error
                        or "Scout ended before calling scout_finish_collection; continuing with collected questions"
                    )
                    await aoe.finish_collection(parent_id, status="partial", warnings=[warning], force=True)
                    await aoe.events.emit(
                        parent_id,
                        actor=SYSTEM_ACTOR,
                        kind=EventKind.SYSTEM,
                        text=f"Scout 部分收集结束（已登记 {len(questions)} 题），继续解题：{warning}",
                        channel_agent_id="scout",
                        payload={"scout_phase": "completed", "collection_status": "partial", "warning": warning},
                    )
                else:
                    raise ContractError(scout_error or "Scout finished without registering any questions")

            parent = await aoe.storage.get_task(parent_id)
            questions = await aoe.storage.list_questions(parent_id)
            for question in questions:
                if not scout_ready(question) and question.metadata.get("registration_complete"):
                    await aoe.storage.update_task(
                        question.id,
                        state=RunState.RUNNING,
                        metadata={"scout_ready": True, "scout_phase": "completed", "status": "queued"},
                    )
                    await aoe.views.save_question(await aoe.storage.get_task(question.id))
            await aoe.storage.update_agent(
                parent_id,
                "scout",
                desired_state=AgentState.COMPLETED,
                actual_state=AgentState.COMPLETED,
                metadata={
                    "scout_phase": "completed",
                    "current_question_id": None,
                    "current_question_title": None,
                },
            )
            await aoe.storage.update_task(parent_id, state=RunState.RUNNING, metadata={"scout_complete": True})
            status_label = (
                "部分题目信息已收集"
                if parent.metadata.get("collection_status") == "partial"
                else "全部题目信息已收集"
            )
            await aoe.events.emit(
                parent_id,
                actor=SYSTEM_ACTOR,
                kind=EventKind.SYSTEM,
                text=f"{status_label}（共 {len(questions)} 题），Scout 结束工作；Worker 继续解题",
                channel_agent_id="scout",
                payload={
                    "scout_phase": "completed",
                    "collection_status": parent.metadata.get("collection_status", "complete"),
                    "question_count": len(questions),
                },
            )
        finally:
            if watcher is not None:
                watcher.cancel()
                await asyncio.gather(watcher, return_exceptions=True)
            await aoe.containers.stop(parent_id, "scout")


__all__ = ["AOEScoutRunner"]
