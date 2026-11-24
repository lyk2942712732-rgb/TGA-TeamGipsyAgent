"""AOE Scout unified standby watch engine: zero-token sentinel + explicit manual/periodic LLM fallback."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

from .domain import SYSTEM_ACTOR, AgentState, EventKind, RunState, TaskRun
from .errors import ConflictError, ContractError

if TYPE_CHECKING:
    from .aoe import AOERuntime

WATCH_PROBE_FILENAME = "watch_probe.py"
DEFAULT_PROBE_INTERVAL_SECONDS = 45
PROBE_TIMEOUT_SECONDS = 25
MAX_CONSECUTIVE_PROBE_FAILURES = 3
DEFAULT_LLM_POLL_INTERVAL_SECONDS = 300
MIN_SENTINEL_INTERVAL_SECONDS = 15
MAX_SENTINEL_INTERVAL_SECONDS = 600
MIN_LLM_INTERVAL_SECONDS = 60
MAX_LLM_INTERVAL_SECONDS = 3600

WatchMode = Literal["sentinel", "llm_poll", "manual"]
WatchState = Literal["collecting", "watching", "probing", "idle", "error_paused"]
WatchAction = Literal["enable_sentinel", "enable_llm_poll", "disable"]


def default_watch_state(*, probe_present: bool = False, scan_round: int = 1) -> dict[str, Any]:
    return {
        "mode": "manual",
        "enabled": False,
        "state": "idle",
        "probe_script_present": probe_present,
        "probe_interval_seconds": DEFAULT_PROBE_INTERVAL_SECONDS,
        "llm_interval_seconds": None,
        "next_check_at": None,
        "last_checked_at": None,
        "last_check_kind": None,
        "last_result": None,
        "last_new_count": 0,
        "last_solved_sync_count": 0,
        "last_error": None,
        "consecutive_failures": 0,
        "scan_round": scan_round,
        "sentinel_checks_total": 0,
    }


def parse_probe_output(stdout: str) -> dict[str, Any]:
    """Extract and validate the JSON payload printed by /workspace/watch_probe.py."""
    text = (stdout or "").strip()
    if not text:
        raise ValueError("watch_probe.py returned empty stdout")
    payload: Any = None
    try:
        payload = json.loads(text)
    except ValueError:
        for line in reversed(text.splitlines()):
            candidate = line.strip()
            if not candidate or not candidate.startswith("{"):
                continue
            try:
                payload = json.loads(candidate)
                break
            except ValueError:
                continue
    if not isinstance(payload, dict):
        raise ValueError("watch_probe.py stdout must be a JSON object")
    raw_keys = payload.get("source_keys")
    if not isinstance(raw_keys, list):
        raise ValueError("watch_probe.py JSON must include a list field 'source_keys'")
    source_keys: list[str] = []
    seen: set[str] = set()
    for item in raw_keys:
        key = str(item).strip()
        if key and key not in seen:
            seen.add(key)
            source_keys.append(key)
    raw_solved = payload.get("solved_keys") or []
    if not isinstance(raw_solved, list):
        raise ValueError("watch_probe.py 'solved_keys' must be a list when supplied")
    solved_keys: list[str] = []
    seen_solved: set[str] = set()
    for item in raw_solved:
        key = str(item).strip()
        if key and key not in seen_solved:
            seen_solved.add(key)
            solved_keys.append(key)
    notes = str(payload.get("notes") or "").strip()
    return {"source_keys": source_keys, "solved_keys": solved_keys, "notes": notes}


class AOEScoutWatch:
    """Manages post-collection zero-token sentinel probing and explicit manual/periodic LLM wakeups."""

    def __init__(self, aoe: AOERuntime) -> None:
        self.aoe = aoe

    def probe_script_path(self, parent_id: UUID) -> Path:
        return self.aoe.config.task_path(parent_id, "workspaces", "scout") / WATCH_PROBE_FILENAME

    def has_probe_script(self, parent_id: UUID) -> bool:
        path = self.probe_script_path(parent_id)
        try:
            return path.is_file() and not path.is_symlink() and path.stat().st_size > 0
        except OSError:
            return False

    def state_for(self, parent: TaskRun) -> dict[str, Any]:
        probe_present = self.has_probe_script(parent.id)
        raw = parent.metadata.get("scout_watch")
        base = default_watch_state(
            probe_present=probe_present,
            scan_round=int(parent.metadata.get("collection_round", 1) or 1),
        )
        if isinstance(raw, dict):
            base.update(raw)
        base["probe_script_present"] = probe_present
        if parent.metadata.get("scout_collecting") or not parent.metadata.get("collection_complete"):
            if parent.state not in {RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED}:
                base["state"] = "collecting"
        return base

    async def _save_watch(self, parent_id: UUID, watch: dict[str, Any]) -> dict[str, Any]:
        await self.aoe.storage.update_task(parent_id, metadata={"scout_watch": watch})
        return watch

    async def on_initial_collection_complete(self, parent_id: UUID) -> dict[str, Any]:
        """After round 1 finishes: auto-enable zero-token sentinel if script exists; otherwise stay idle."""
        parent = await self.aoe.storage.get_task(parent_id)
        now = self.aoe.clock()
        probe_present = self.has_probe_script(parent_id)
        watch = self.state_for(parent)
        watch["probe_script_present"] = probe_present
        watch["scan_round"] = max(1, int(watch.get("scan_round", 1) or 1))
        watch["last_checked_at"] = now
        watch["last_check_kind"] = "llm_scan"
        watch["consecutive_failures"] = 0
        watch["last_error"] = None
        if probe_present:
            interval = int(watch.get("probe_interval_seconds") or DEFAULT_PROBE_INTERVAL_SECONDS)
            watch.update(
                mode="sentinel",
                enabled=True,
                state="watching",
                probe_interval_seconds=interval,
                next_check_at=now + interval,
                last_result=f"首轮收集完成，已默认开启零 Token 哨兵（每 {interval}s 探测新题）",
            )
            await self._save_watch(parent_id, watch)
            await self.aoe.storage.update_agent(
                parent_id,
                "scout",
                desired_state=AgentState.RUNNING,
                actual_state=AgentState.IDLE,
                container_id=None,
                metadata={
                    "scout_phase": "watching",
                    "current_question_id": None,
                    "current_question_title": None,
                },
            )
        else:
            watch.update(
                mode="manual",
                enabled=False,
                state="idle",
                next_check_at=None,
                last_result="首轮收集完成；未生成零 Token 探测脚本，需手动唤醒或开启定时巡检",
            )
            await self._save_watch(parent_id, watch)
            await self.aoe.storage.update_agent(
                parent_id,
                "scout",
                desired_state=AgentState.COMPLETED,
                actual_state=AgentState.COMPLETED,
                container_id=None,
                metadata={
                    "scout_phase": "completed",
                    "current_question_id": None,
                    "current_question_title": None,
                },
            )
        return watch

    async def on_incremental_scan_started(
        self,
        parent_id: UUID,
        *,
        scan_round: int,
        reason: str,
    ) -> dict[str, Any]:
        parent = await self.aoe.storage.get_task(parent_id)
        watch = self.state_for(parent)
        watch.update(
            state="collecting",
            scan_round=scan_round,
            next_check_at=None,
            last_error=None,
            last_result=f"第 {scan_round} 轮增量收集中（{reason}）",
        )
        return await self._save_watch(parent_id, watch)

    async def on_incremental_scan_finished(
        self,
        parent_id: UUID,
        *,
        new_count: int,
        solved_sync_count: int = 0,
        error: str | None = None,
        stopped_by_user: bool = False,
    ) -> dict[str, Any]:
        parent = await self.aoe.storage.get_task(parent_id)
        now = self.aoe.clock()
        probe_present = self.has_probe_script(parent_id)
        watch = self.state_for(parent)
        watch["probe_script_present"] = probe_present
        watch["last_checked_at"] = now
        watch["last_check_kind"] = "llm_scan"
        watch["last_new_count"] = new_count
        watch["last_solved_sync_count"] = solved_sync_count

        summary_parts = [f"新增 {new_count} 题"]
        if solved_sync_count:
            summary_parts.append(f"同步平台已解出 {solved_sync_count} 题")
        summary = " · ".join(summary_parts)

        if stopped_by_user:
            watch.update(
                mode="manual",
                enabled=False,
                state="idle",
                next_check_at=None,
                last_error=None,
                last_result=f"已手动停止本轮巡检（{summary}）",
            )
            await self._save_watch(parent_id, watch)
            await self.aoe.storage.update_agent(
                parent_id,
                "scout",
                desired_state=AgentState.STOPPED,
                actual_state=AgentState.STOPPED,
                container_id=None,
                metadata={"scout_phase": "completed"},
            )
            return watch

        if error:
            watch["last_error"] = error
            watch["last_result"] = f"本轮增量巡检异常：{error}"
        else:
            watch["last_error"] = None
            watch["consecutive_failures"] = 0
            watch["last_result"] = f"第 {watch.get('scan_round', 2)} 轮增量巡检完成（{summary}）"

        prev_mode = watch.get("mode", "manual")
        prev_enabled = bool(watch.get("enabled", False))
        # If user didn't explicitly turn off watch and a probe script is present, default to zero-token sentinel.
        auto_resume_sentinel = prev_mode == "sentinel" or (
            not prev_enabled and error is None and prev_mode != "llm_poll"
        )
        if probe_present and auto_resume_sentinel:
            interval = int(watch.get("probe_interval_seconds") or DEFAULT_PROBE_INTERVAL_SECONDS)
            watch.update(
                mode="sentinel",
                enabled=True,
                state="watching",
                probe_interval_seconds=interval,
                next_check_at=now + interval,
            )
            await self._save_watch(parent_id, watch)
            await self.aoe.storage.update_agent(
                parent_id,
                "scout",
                desired_state=AgentState.RUNNING,
                actual_state=AgentState.IDLE,
                container_id=None,
                last_error=error,
                metadata={"scout_phase": "watching"},
            )
        elif prev_mode == "llm_poll" and prev_enabled:
            interval = int(watch.get("llm_interval_seconds") or DEFAULT_LLM_POLL_INTERVAL_SECONDS)
            watch.update(
                mode="llm_poll",
                enabled=True,
                state="watching",
                llm_interval_seconds=interval,
                next_check_at=now + interval,
            )
            await self._save_watch(parent_id, watch)
            await self.aoe.storage.update_agent(
                parent_id,
                "scout",
                desired_state=AgentState.RUNNING,
                actual_state=AgentState.IDLE,
                container_id=None,
                last_error=error,
                metadata={"scout_phase": "watching"},
            )
        else:
            watch.update(
                mode="manual",
                enabled=False,
                state="idle",
                next_check_at=None,
            )
            await self._save_watch(parent_id, watch)
            await self.aoe.storage.update_agent(
                parent_id,
                "scout",
                desired_state=AgentState.COMPLETED,
                actual_state=AgentState.COMPLETED,
                container_id=None,
                last_error=error,
                metadata={"scout_phase": "completed"},
            )
        return watch

    async def ensure_standby_watch(self, parent_id: UUID) -> dict[str, Any]:
        """Keep zero-token sentinel running on standby confirmation; never auto-enable LLM polling."""
        parent = await self.aoe.storage.get_task(parent_id)
        now = self.aoe.clock()
        probe_present = self.has_probe_script(parent_id)
        watch = self.state_for(parent)
        watch["probe_script_present"] = probe_present

        if parent.metadata.get("scout_collecting"):
            return watch

        if probe_present:
            interval = int(watch.get("probe_interval_seconds") or DEFAULT_PROBE_INTERVAL_SECONDS)
            next_at = watch.get("next_check_at")
            keep_existing = (
                watch.get("mode") == "sentinel"
                and watch.get("enabled")
                and isinstance(next_at, (int, float))
                and next_at > now
            )
            if not keep_existing:
                next_at = now + interval
            watch.update(
                mode="sentinel",
                enabled=True,
                state="watching",
                probe_interval_seconds=interval,
                next_check_at=next_at,
                consecutive_failures=0,
                last_error=None,
                last_result=f"任务保持存活，零 Token 哨兵持续站岗（每 {interval}s 探测新题）",
            )
            await self._save_watch(parent_id, watch)
            await self.aoe.storage.update_agent(
                parent_id,
                "scout",
                desired_state=AgentState.RUNNING,
                actual_state=AgentState.IDLE,
                container_id=None,
                metadata={"scout_phase": "watching"},
            )
            return watch

        # No probe script: keep user-explicit llm_poll if already active, otherwise stay manual standby.
        if watch.get("mode") == "llm_poll" and watch.get("enabled"):
            interval = int(watch.get("llm_interval_seconds") or DEFAULT_LLM_POLL_INTERVAL_SECONDS)
            next_at = watch.get("next_check_at")
            if not (isinstance(next_at, (int, float)) and next_at > now):
                next_at = now + interval
            watch.update(state="watching", next_check_at=next_at)
            await self._save_watch(parent_id, watch)
            await self.aoe.storage.update_agent(
                parent_id,
                "scout",
                desired_state=AgentState.RUNNING,
                actual_state=AgentState.IDLE,
                container_id=None,
                metadata={"scout_phase": "watching"},
            )
            return watch

        watch.update(
            mode="manual",
            enabled=False,
            state="idle",
            next_check_at=None,
            last_result="任务保持站岗存活；未检测到零 Token 脚本，可随时手动唤醒 Scout 或开启定时巡检",
        )
        await self._save_watch(parent_id, watch)
        await self.aoe.storage.update_agent(
            parent_id,
            "scout",
            desired_state=AgentState.IDLE,
            actual_state=AgentState.IDLE,
            container_id=None,
            metadata={"scout_phase": "completed"},
        )
        return watch

    async def configure(
        self,
        parent_id: UUID,
        *,
        action: WatchAction,
        interval_seconds: int | None = None,
    ) -> dict[str, Any]:
        parent = await self.aoe.storage.get_task(parent_id)
        if parent.scene_id != "aoe" or parent.parent_task_id is not None:
            raise ContractError("Scout 站岗设置仅支持 AOE 批量任务")
        if parent.state in {RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED}:
            raise ConflictError("当前任务已结束，无法修改 Scout 站岗设置")
        if not parent.metadata.get("collection_complete"):
            raise ConflictError("请等待首轮题目收集完成后再调整站岗模式")

        now = self.aoe.clock()
        probe_present = self.has_probe_script(parent_id)
        watch = self.state_for(parent)
        collecting = bool(parent.metadata.get("scout_collecting"))

        if action == "enable_sentinel":
            if not probe_present:
                raise ConflictError(
                    "未检测到零 Token 探测脚本（/workspace/watch_probe.py），请先手动唤醒 Scout 或开启定时巡检"
                )
            interval = DEFAULT_PROBE_INTERVAL_SECONDS if interval_seconds is None else int(interval_seconds)
            if not MIN_SENTINEL_INTERVAL_SECONDS <= interval <= MAX_SENTINEL_INTERVAL_SECONDS:
                raise ContractError(
                    f"哨兵探测间隔必须在 {MIN_SENTINEL_INTERVAL_SECONDS}~{MAX_SENTINEL_INTERVAL_SECONDS} 秒之间"
                )
            watch.update(
                mode="sentinel",
                enabled=True,
                state="collecting" if collecting else "watching",
                probe_interval_seconds=interval,
                next_check_at=None if collecting else now + interval,
                consecutive_failures=0,
                last_error=None,
                last_result=f"已开启零 Token 哨兵站岗（每 {interval}s 探测新题）",
            )
            await self._save_watch(parent_id, watch)
            if not collecting:
                await self.aoe.storage.update_agent(
                    parent_id,
                    "scout",
                    desired_state=AgentState.RUNNING,
                    actual_state=AgentState.IDLE,
                    metadata={"scout_phase": "watching"},
                )
            await self.aoe.events.emit(
                parent_id,
                actor=SYSTEM_ACTOR,
                kind=EventKind.SYSTEM,
                text=f"已开启 Scout 零 Token 哨兵站岗（每 {interval} 秒探测一次）",
                channel_agent_id="scout",
                payload={"scout_watch": watch},
            )
            self.aoe.wake(parent_id)
            return watch

        if action == "enable_llm_poll":
            interval = DEFAULT_LLM_POLL_INTERVAL_SECONDS if interval_seconds is None else int(interval_seconds)
            if not MIN_LLM_INTERVAL_SECONDS <= interval <= MAX_LLM_INTERVAL_SECONDS:
                raise ContractError(f"定时巡检间隔必须在 {MIN_LLM_INTERVAL_SECONDS}~{MAX_LLM_INTERVAL_SECONDS} 秒之间")
            watch.update(
                mode="llm_poll",
                enabled=True,
                state="collecting" if collecting else "watching",
                llm_interval_seconds=interval,
                next_check_at=None if collecting else now + interval,
                consecutive_failures=0,
                last_error=None,
                last_result=f"已开启定时 LLM 巡检（每 {max(1, round(interval / 60))} 分钟唤醒一次）",
            )
            await self._save_watch(parent_id, watch)
            if not collecting:
                await self.aoe.storage.update_agent(
                    parent_id,
                    "scout",
                    desired_state=AgentState.RUNNING,
                    actual_state=AgentState.IDLE,
                    metadata={"scout_phase": "watching"},
                )
            await self.aoe.events.emit(
                parent_id,
                actor=SYSTEM_ACTOR,
                kind=EventKind.SYSTEM,
                text=f"已开启 Scout 定时巡检（每 {max(1, round(interval / 60))} 分钟唤醒一次）",
                channel_agent_id="scout",
                payload={"scout_watch": watch},
            )
            self.aoe.wake(parent_id)
            return watch

        if action == "disable":
            watch.update(
                mode="manual",
                enabled=False,
                state="collecting" if collecting else "idle",
                next_check_at=None,
                last_result="已暂停自动站岗（可随时手动唤醒 Scout 巡检）",
            )
            await self._save_watch(parent_id, watch)
            if not collecting:
                await self.aoe.storage.update_agent(
                    parent_id,
                    "scout",
                    desired_state=AgentState.COMPLETED,
                    actual_state=AgentState.COMPLETED,
                    metadata={"scout_phase": "completed"},
                )
            await self.aoe.events.emit(
                parent_id,
                actor=SYSTEM_ACTOR,
                kind=EventKind.SYSTEM,
                text="已暂停 Scout 自动站岗，切换为手动唤醒模式",
                channel_agent_id="scout",
                payload={"scout_watch": watch},
            )
            self.aoe.wake(parent_id)
            return watch

        raise ContractError(f"Unsupported watch action: {action}")

    async def run_sentinel_probe(self, parent_id: UUID) -> dict[str, Any]:
        """Run /workspace/watch_probe.py in a zero-token isolated container and act on diffs."""
        parent = await self.aoe.storage.get_task(parent_id)
        if parent.state in {RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED}:
            return self.state_for(parent)
        if parent.metadata.get("scout_collecting"):
            return self.state_for(parent)
        watch = self.state_for(parent)
        if not self.has_probe_script(parent_id):
            watch.update(
                mode="manual",
                enabled=False,
                state="idle",
                next_check_at=None,
                last_result="零 Token 探测脚本已不存在，已切换为手动模式",
            )
            return await self._save_watch(parent_id, watch)

        watch.update(state="probing", next_check_at=None)
        await self._save_watch(parent_id, watch)

        try:
            exit_code, stdout, stderr = await self.aoe.containers.run_scout_probe(
                parent_id, timeout=PROBE_TIMEOUT_SECONDS
            )
            if exit_code != 0:
                detail = (stderr or stdout or f"exit code {exit_code}").strip()[-400:]
                raise RuntimeError(f"watch_probe.py 退出码 {exit_code}: {detail}")
            parsed = parse_probe_output(stdout)
        except Exception as exc:
            now = self.aoe.clock()
            failures = int(watch.get("consecutive_failures", 0) or 0) + 1
            err_text = str(exc).strip()[:400] or type(exc).__name__
            watch["last_checked_at"] = now
            watch["last_check_kind"] = "sentinel"
            watch["consecutive_failures"] = failures
            watch["last_error"] = err_text
            if failures >= MAX_CONSECUTIVE_PROBE_FAILURES:
                watch.update(
                    enabled=False,
                    state="error_paused",
                    next_check_at=None,
                    last_result=f"哨兵连续 {failures} 次失败已熔断暂停（不自动耗 Token）：{err_text}",
                )
                await self._save_watch(parent_id, watch)
                await self.aoe.storage.update_agent(
                    parent_id,
                    "scout",
                    desired_state=AgentState.PAUSED,
                    actual_state=AgentState.PAUSED,
                    last_error=err_text,
                    metadata={"scout_phase": "watching"},
                )
                await self.aoe.events.emit(
                    parent_id,
                    actor=SYSTEM_ACTOR,
                    kind=EventKind.ERROR,
                    text=(
                        f"零 Token 哨兵连续 {failures} 次探测失败，已自动熔断暂停以防误耗 Token；"
                        f"请点击「唤醒 Scout」修复脚本或检查平台状态：{err_text}"
                    ),
                    channel_agent_id="scout",
                    payload={"consecutive_failures": failures, "error": err_text},
                )
            else:
                backoff = 60 if failures == 1 else 120
                watch.update(
                    enabled=True,
                    state="watching",
                    next_check_at=now + backoff,
                    last_result=(
                        f"哨兵探测异常（第 {failures}/{MAX_CONSECUTIVE_PROBE_FAILURES} 次，"
                        f"{backoff}s 后重试）：{err_text}"
                    ),
                )
                await self._save_watch(parent_id, watch)
            self.aoe.wake(parent_id)
            return watch

        now = self.aoe.clock()
        questions = await self.aoe.storage.list_questions(parent_id)
        existing_keys = {q.metadata.get("source_key", "") for q in questions}
        new_keys = [key for key in parsed["source_keys"] if key not in existing_keys]

        solved_sync_count = 0
        if parsed["solved_keys"]:
            solved_sync_count = await self.aoe.sync_platform_solved_keys(parent_id, parsed["solved_keys"])

        watch["last_checked_at"] = now
        watch["last_check_kind"] = "sentinel"
        watch["consecutive_failures"] = 0
        watch["last_error"] = None
        watch["sentinel_checks_total"] = int(watch.get("sentinel_checks_total", 0) or 0) + 1
        watch["last_new_count"] = len(new_keys)
        watch["last_solved_sync_count"] = solved_sync_count

        if new_keys:
            preview = ", ".join(new_keys[:5]) + (" ..." if len(new_keys) > 5 else "")
            watch.update(
                state="collecting",
                next_check_at=None,
                last_result=f"零 Token 哨兵发现 {len(new_keys)} 道新题（{preview}），正在唤醒 Scout 增量收集",
            )
            await self._save_watch(parent_id, watch)
            await self.aoe.events.emit(
                parent_id,
                actor=SYSTEM_ACTOR,
                kind=EventKind.SYSTEM,
                text=f"零 Token 哨兵发现 {len(new_keys)} 道新题（{preview}），立即唤醒 Scout 增量收集",
                channel_agent_id="scout",
                payload={"detected_new_keys": new_keys, "solved_sync_count": solved_sync_count},
            )
            await self.aoe.wake_scout(
                parent_id,
                reason="sentinel",
                detected_new_keys=new_keys,
            )
            return watch

        interval = int(watch.get("probe_interval_seconds") or DEFAULT_PROBE_INTERVAL_SECONDS)
        extra = f" · 同步平台已解出 {solved_sync_count} 题" if solved_sync_count else ""
        watch.update(
            mode="sentinel",
            enabled=True,
            state="watching",
            next_check_at=now + interval,
            last_result=f"零 Token 哨兵探测正常 · 暂无新题{extra}",
        )
        await self._save_watch(parent_id, watch)
        self.aoe.wake(parent_id)
        return watch

    def next_due(self, parent: TaskRun) -> float | None:
        if not parent.metadata.get("collection_complete") or parent.metadata.get("scout_collecting"):
            return None
        watch = parent.metadata.get("scout_watch")
        if not isinstance(watch, dict) or not watch.get("enabled"):
            return None
        if watch.get("state") != "watching":
            return None
        due = watch.get("next_check_at")
        return float(due) if isinstance(due, (int, float)) else None


__all__ = [
    "AOEScoutWatch",
    "DEFAULT_LLM_POLL_INTERVAL_SECONDS",
    "DEFAULT_PROBE_INTERVAL_SECONDS",
    "MAX_CONSECUTIVE_PROBE_FAILURES",
    "PROBE_TIMEOUT_SECONDS",
    "WATCH_PROBE_FILENAME",
    "WatchAction",
    "WatchMode",
    "WatchState",
    "default_watch_state",
    "parse_probe_output",
]
