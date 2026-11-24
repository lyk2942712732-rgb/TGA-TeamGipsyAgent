"""AOE Scout collection lifecycle runner: launches initial and incremental Scout collection rounds."""

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
    """Runs AOE Scout collection rounds independently of the Worker attempt scheduler."""

    def __init__(self, aoe: AOERuntime) -> None:
        self.aoe = aoe

    async def run(self, parent_id: UUID, bindings: dict) -> None:
        aoe = self.aoe
        parent = await aoe.storage.get_task(parent_id)
        await aoe.overview.prepare(parent_id)
        aoe.collection.ensure_directories(parent_id)
        await aoe.storage.update_task(
            parent_id,
            metadata={
                "aoe_scout_mode": "collection_only",
                "scout_collecting": not bool(parent.metadata.get("collection_complete")),
                "collection_round": max(1, int(parent.metadata.get("collection_round", 1) or 1)),
                "collection_round_complete": bool(parent.metadata.get("collection_complete")),
                "detected_new_keys": [],
            },
        )
        parent = await aoe.storage.get_task(parent_id)
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
            await aoe.storage.update_task(
                parent_id,
                state=RunState.RUNNING,
                metadata={
                    "scout_complete": True,
                    "scout_collecting": False,
                    "collection_round_complete": True,
                },
            )
            watch = await aoe.watch.on_initial_collection_complete(parent_id)
            await aoe.refresh_collection_state(parent_id)
            status_label = (
                "部分题目信息已收集"
                if parent.metadata.get("collection_status") == "partial"
                else "全部题目信息已收集"
            )
            watch_note = (
                f"已默认开启零 Token 哨兵站岗（每 {watch['probe_interval_seconds']}s 探测新题）"
                if watch.get("mode") == "sentinel" and watch.get("enabled")
                else "未检测到零 Token 探测脚本，Scout 已待命（可随时手动唤醒巡检）"
            )
            await aoe.events.emit(
                parent_id,
                actor=SYSTEM_ACTOR,
                kind=EventKind.SYSTEM,
                text=f"{status_label}（共 {len(questions)} 题），{watch_note}；Worker 继续解题",
                channel_agent_id="scout",
                payload={
                    "scout_phase": "watching" if watch.get("enabled") else "completed",
                    "collection_status": parent.metadata.get("collection_status", "complete"),
                    "question_count": len(questions),
                    "scout_watch": watch,
                },
            )
        finally:
            if watcher is not None:
                watcher.cancel()
                await asyncio.gather(watcher, return_exceptions=True)
            await aoe.containers.stop(parent_id, "scout")

    async def run_incremental(
        self,
        parent_id: UUID,
        bindings: dict,
        *,
        reason: str = "manual",
        detected_new_keys: list[str] | None = None,
    ) -> dict:
        """Run a follow-up incremental Scout scan without blocking ongoing Workers."""
        aoe = self.aoe
        await aoe.overview.prepare(parent_id)
        aoe.collection.ensure_directories(parent_id)
        parent = await aoe.storage.get_task(parent_id)
        before_questions = await aoe.storage.list_questions(parent_id)
        before_keys = {q.metadata.get("source_key", "") for q in before_questions}
        before_solved = {
            q.metadata.get("source_key", "")
            for q in before_questions
            if q.metadata.get("status") == "solved"
        }
        scan_round = int(parent.metadata.get("collection_round", 1) or 1) + 1
        keys_hint = [str(k).strip() for k in (detected_new_keys or []) if str(k).strip()]
        reason_label = {
            "manual": "手动唤醒",
            "sentinel": "零 Token 哨兵发现新题",
            "llm_poll": "定时巡检",
        }.get(reason, reason)

        await aoe.storage.update_task(
            parent_id,
            metadata={
                "scout_collecting": True,
                "collection_round": scan_round,
                "collection_round_complete": False,
                "detected_new_keys": keys_hint,
            },
        )
        await aoe.watch.on_incremental_scan_started(parent_id, scan_round=scan_round, reason=reason_label)
        await aoe.storage.update_agent(
            parent_id,
            "scout",
            desired_state=AgentState.RUNNING,
            actual_state=AgentState.RUNNING,
            last_error=None,
            metadata={"scout_phase": "collecting"},
        )
        await aoe.refresh_collection_state(parent_id)
        await aoe.events.emit(
            parent_id,
            actor=SYSTEM_ACTOR,
            kind=EventKind.SYSTEM,
            text=f"Scout 已唤醒，开始第 {scan_round} 轮增量收题（{reason_label}）",
            channel_agent_id="scout",
            payload={"scout_phase": "collecting", "scan_round": scan_round, "reason": reason},
        )

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
                    if not current.metadata.get("scout_collecting") or current.metadata.get(
                        "collection_round_complete"
                    ):
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
        stopped_by_user = False
        try:
            budget = min(int(aoe.config.runtime.aoe_collection_timeout_seconds), 240)
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
                            aoe_incremental=True,
                            aoe_detected_new_keys=tuple(keys_hint),
                        ),
                        emit,
                    )
            except asyncio.CancelledError:
                stopped_by_user = True
            except TimeoutError:
                scout_error = f"增量巡检超时（{budget}s）"
            except Exception as exc:
                scout_error = str(exc) or type(exc).__name__
            finally:
                if watcher is not None:
                    watcher.cancel()
                    await asyncio.gather(watcher, return_exceptions=True)
                    watcher = None

            if not stopped_by_user and aoe.collection.has_pending(parent_id):
                await aoe.collection.import_outbox(parent_id)

            after_questions = await aoe.storage.list_questions(parent_id)
            for question in after_questions:
                if not scout_ready(question) and question.metadata.get("registration_complete"):
                    await aoe.storage.update_task(
                        question.id,
                        state=RunState.RUNNING,
                        metadata={"scout_ready": True, "scout_phase": "completed", "status": "queued"},
                    )
                    await aoe.views.save_question(await aoe.storage.get_task(question.id))

            after_keys = {q.metadata.get("source_key", "") for q in after_questions}
            after_solved = {
                q.metadata.get("source_key", "")
                for q in after_questions
                if q.metadata.get("status") == "solved"
            }
            new_count = len(after_keys - before_keys)
            solved_sync_count = len((after_solved - before_solved) & before_keys)

            await aoe.storage.update_task(
                parent_id,
                metadata={
                    "scout_collecting": False,
                    "collection_round_complete": True,
                    "detected_new_keys": [],
                },
            )
            watch = await aoe.watch.on_incremental_scan_finished(
                parent_id,
                new_count=new_count,
                solved_sync_count=solved_sync_count,
                error=scout_error,
                stopped_by_user=stopped_by_user,
            )
            await aoe.refresh_collection_state(parent_id)

            if stopped_by_user:
                await aoe.events.emit(
                    parent_id,
                    actor=SYSTEM_ACTOR,
                    kind=EventKind.SYSTEM,
                    text=(
                        f"已手动停止 Scout 第 {scan_round} 轮增量收题"
                        f"（本轮新增 {new_count} 题，累计 {len(after_questions)} 题）"
                    ),
                    channel_agent_id="scout",
                    payload={"scan_round": scan_round, "new_count": new_count, "stopped": True},
                )
            elif scout_error:
                await aoe.events.emit(
                    parent_id,
                    actor=SYSTEM_ACTOR,
                    kind=EventKind.ERROR,
                    text=f"Scout 第 {scan_round} 轮增量巡检异常（不影响已运行 Worker）：{scout_error}",
                    channel_agent_id="scout",
                    payload={"scan_round": scan_round, "new_count": new_count, "error": scout_error},
                )
            else:
                extra = f"，同步平台已解出 {solved_sync_count} 题" if solved_sync_count else ""
                await aoe.events.emit(
                    parent_id,
                    actor=SYSTEM_ACTOR,
                    kind=EventKind.SYSTEM,
                    text=(
                        f"Scout 第 {scan_round} 轮增量收题完成：新增 {new_count} 题{extra}"
                        f"（累计 {len(after_questions)} 题）"
                    ),
                    channel_agent_id="scout",
                    payload={
                        "scan_round": scan_round,
                        "new_count": new_count,
                        "solved_sync_count": solved_sync_count,
                        "question_count": len(after_questions),
                        "scout_watch": watch,
                    },
                )
            aoe.wake(parent_id)
            return {
                "scan_round": scan_round,
                "new_count": new_count,
                "solved_sync_count": solved_sync_count,
                "stopped": stopped_by_user,
                "error": scout_error,
                "scout_watch": watch,
            }
        finally:
            if watcher is not None:
                watcher.cancel()
                await asyncio.gather(watcher, return_exceptions=True)
            await aoe.containers.stop(parent_id, "scout")


__all__ = ["AOEScoutRunner"]
