"""AOE Scout unified 3-state standby watch engine.

Scout has 3 explicit human-controllable states:
- ``collecting``: LLM Scout is actively collecting (initial or incremental).
- ``watching``: Default post-collection standby with 2 mutually exclusive methods:
  - ``sentinel`` (default, 0-token ``/workspace/watch_probe.py``)
  - ``llm_poll`` (explicit human fallback when script is unavailable, costs tokens)
  - ``unavailable`` (no script or probe circuit-broken; awaits human choice)
- ``stopped``: Explicitly stopped by user (neither collecting nor watching).
"""

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

ScoutStatus = Literal["collecting", "watching", "stopped"]
WatchMethod = Literal["sentinel", "llm_poll", "unavailable"]
UnavailableReason = Literal["no_script", "probe_failed"]
WatchMode = Literal["sentinel", "llm_poll", "manual"]
WatchState = Literal["collecting", "watching", "probing", "idle", "error_paused"]
WatchAction = Literal["resume_watch", "enable_sentinel", "enable_llm_poll", "disable_llm_poll", "disable"]


def _sync_legacy_fields(watch: dict[str, Any]) -> dict[str, Any]:
    """Derive backward-compatible mode/enabled/state fields strictly from status + watch_method."""
    status: str = watch.get("status", "stopped")
    method: str = watch.get("watch_method", "unavailable")
    reason: str | None = watch.get("unavailable_reason")
    probing = bool(watch.get("probing", False))

    if status == "collecting":
        watch["mode"] = "sentinel" if method == "sentinel" else "llm_poll" if method == "llm_poll" else "manual"
        watch["enabled"] = False
        watch["state"] = "collecting"
    elif status == "watching":
        if method == "sentinel":
            watch["mode"] = "sentinel"
            watch["enabled"] = True
            watch["state"] = "probing" if probing else "watching"
        elif method == "llm_poll":
            watch["mode"] = "llm_poll"
            watch["enabled"] = True
            watch["state"] = "watching"
        else:
            watch["mode"] = "sentinel" if reason == "probe_failed" else "manual"
            watch["enabled"] = False
            watch["state"] = "error_paused" if reason == "probe_failed" else "idle"
    else:
        watch["status"] = "stopped"
        watch["probing"] = False
        watch["mode"] = "manual"
        watch["enabled"] = False
        watch["state"] = "idle"
    return watch


def default_watch_state(*, probe_present: bool = False, scan_round: int = 1) -> dict[str, Any]:
    return _sync_legacy_fields(
        {
            "status": "collecting",
            "watch_method": "sentinel" if probe_present else "unavailable",
            "unavailable_reason": None if probe_present else "no_script",
            "probing": False,
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
    )


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
    """Manages post-collection zero-token sentinel probing and explicit fallback LLM polling."""

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
            if "status" not in raw:
                # Migrate legacy persisted records cleanly on read.
                legacy_state = raw.get("state")
                if legacy_state == "collecting":
                    base["status"] = "collecting"
                elif legacy_state in {"watching", "probing", "error_paused"}:
                    base["status"] = "watching"
                    if legacy_state == "error_paused":
                        base["watch_method"] = "unavailable"
                        base["unavailable_reason"] = "probe_failed"
                    elif raw.get("mode") == "llm_poll":
                        base["watch_method"] = "llm_poll"
                        base["unavailable_reason"] = None
                    else:
                        base["watch_method"] = "sentinel"
                        base["unavailable_reason"] = None
                else:
                    base["status"] = "watching" if not probe_present else "stopped"
                    base["watch_method"] = "unavailable" if not probe_present else "sentinel"
                    base["unavailable_reason"] = "no_script" if not probe_present else None
        base["probe_script_present"] = probe_present
        if parent.metadata.get("scout_collecting") or not parent.metadata.get("collection_complete"):
            if parent.state not in {RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED}:
                base["status"] = "collecting"
                base["probing"] = False
        return _sync_legacy_fields(base)

    async def _save_watch(self, parent_id: UUID, watch: dict[str, Any]) -> dict[str, Any]:
        normalized = _sync_legacy_fields(watch)
        await self.aoe.storage.update_task(parent_id, metadata={"scout_watch": normalized})
        return normalized

    async def _sync_scout_agent_for_watch(
        self,
        parent_id: UUID,
        watch: dict[str, Any],
        *,
        last_error: str | None = None,
    ) -> None:
        status = watch["status"]
        method = watch["watch_method"]
        reason = watch.get("unavailable_reason")
        if status == "stopped":
            await self.aoe.storage.update_agent(
                parent_id,
                "scout",
                desired_state=AgentState.STOPPED,
                actual_state=AgentState.STOPPED,
                container_id=None,
                last_error=last_error,
                metadata={"scout_phase": "completed", "current_question_id": None, "current_question_title": None},
            )
        elif status == "watching" and method == "unavailable" and reason == "probe_failed":
            await self.aoe.storage.update_agent(
                parent_id,
                "scout",
                desired_state=AgentState.PAUSED,
                actual_state=AgentState.PAUSED,
                container_id=None,
                last_error=last_error or watch.get("last_error"),
                metadata={"scout_phase": "watching", "current_question_id": None, "current_question_title": None},
            )
        elif status == "watching":
            await self.aoe.storage.update_agent(
                parent_id,
                "scout",
                desired_state=AgentState.RUNNING,
                actual_state=AgentState.IDLE,
                container_id=None,
                last_error=last_error,
                metadata={"scout_phase": "watching", "current_question_id": None, "current_question_title": None},
            )

    async def on_initial_collection_complete(self, parent_id: UUID) -> dict[str, Any]:
        """After round 1 finishes, Scout always enters ``watching`` (default sentinel if script exists)."""
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
        watch["probing"] = False
        watch["status"] = "watching"
        if probe_present:
            interval = int(watch.get("probe_interval_seconds") or DEFAULT_PROBE_INTERVAL_SECONDS)
            watch.update(
                watch_method="sentinel",
                unavailable_reason=None,
                probe_interval_seconds=interval,
                next_check_at=now + interval,
                last_result=f"首轮收集完成，已默认进入零 Token 脚本哨兵站岗（每 {interval}s 探测新题）",
            )
        else:
            watch.update(
                watch_method="unavailable",
                unavailable_reason="no_script",
                next_check_at=None,
                last_result="首轮收集完成，已进入站岗态；未生成探测脚本，可唤醒 Scout 补脚本或开启定时巡检兜底",
            )
        watch = await self._save_watch(parent_id, watch)
        await self._sync_scout_agent_for_watch(parent_id, watch)
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
            status="collecting",
            probing=False,
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
        watch["probing"] = False
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
                status="stopped",
                next_check_at=None,
                last_error=None,
                last_result=f"已手动停止收集（{summary}）",
            )
            watch = await self._save_watch(parent_id, watch)
            await self._sync_scout_agent_for_watch(parent_id, watch)
            return watch

        if error:
            watch["last_error"] = error
            watch["last_result"] = f"第 {watch.get('scan_round', 2)} 轮增量收集异常：{error}"
        else:
            watch["last_error"] = None
            watch["consecutive_failures"] = 0
            watch["last_result"] = f"第 {watch.get('scan_round', 2)} 轮增量收集完成（{summary}）"

        # Natural collection completion always returns to ``watching``.
        # Auto-upgrade rule: if a probe script is now present and scan succeeded, always upgrade to zero-token sentinel!
        watch["status"] = "watching"
        prev_method = watch.get("watch_method", "unavailable")
        if probe_present and (error is None or prev_method == "sentinel"):
            interval = int(watch.get("probe_interval_seconds") or DEFAULT_PROBE_INTERVAL_SECONDS)
            upgraded = prev_method == "llm_poll" and error is None
            watch.update(
                watch_method="sentinel",
                unavailable_reason=None,
                probe_interval_seconds=interval,
                next_check_at=now + interval,
                **(
                    {"last_result": f"{watch['last_result']} · 已检测到探测脚本，自动升级为零 Token 哨兵"}
                    if upgraded
                    else {}
                ),
            )
        elif prev_method == "llm_poll":
            interval = int(watch.get("llm_interval_seconds") or DEFAULT_LLM_POLL_INTERVAL_SECONDS)
            watch.update(
                watch_method="llm_poll",
                unavailable_reason=None,
                llm_interval_seconds=interval,
                next_check_at=now + interval,
            )
        elif probe_present:
            # Probe script exists but incremental scan itself errored after prior probe_failed.
            watch.update(
                watch_method="unavailable",
                unavailable_reason="probe_failed",
                next_check_at=None,
            )
        else:
            watch.update(
                watch_method="unavailable",
                unavailable_reason="no_script",
                next_check_at=None,
            )
        watch = await self._save_watch(parent_id, watch)
        await self._sync_scout_agent_for_watch(parent_id, watch, last_error=error)
        return watch

    async def ensure_standby_watch(self, parent_id: UUID) -> dict[str, Any]:
        """On finish-confirm 'continue_standby': keep/enter ``watching`` with zero-token sentinel by default."""
        parent = await self.aoe.storage.get_task(parent_id)
        now = self.aoe.clock()
        probe_present = self.has_probe_script(parent_id)
        watch = self.state_for(parent)
        watch["probe_script_present"] = probe_present

        if parent.metadata.get("scout_collecting"):
            return watch

        watch["status"] = "watching"
        watch["probing"] = False
        if probe_present and watch.get("unavailable_reason") != "probe_failed":
            interval = int(watch.get("probe_interval_seconds") or DEFAULT_PROBE_INTERVAL_SECONDS)
            next_at = watch.get("next_check_at")
            keep_existing = (
                watch.get("watch_method") == "sentinel"
                and isinstance(next_at, (int, float))
                and next_at > now
            )
            if not keep_existing:
                next_at = now + interval
            watch.update(
                watch_method="sentinel",
                unavailable_reason=None,
                probe_interval_seconds=interval,
                next_check_at=next_at,
                consecutive_failures=0,
                last_error=None,
                last_result=f"任务保持存活，零 Token 脚本哨兵持续站岗（每 {interval}s 探测新题）",
            )
        elif watch.get("watch_method") == "llm_poll" and watch.get("llm_interval_seconds"):
            interval = int(watch.get("llm_interval_seconds") or DEFAULT_LLM_POLL_INTERVAL_SECONDS)
            next_at = watch.get("next_check_at")
            if not (isinstance(next_at, (int, float)) and next_at > now):
                next_at = now + interval
            watch.update(
                watch_method="llm_poll",
                unavailable_reason=None,
                next_check_at=next_at,
            )
        else:
            reason: UnavailableReason = "probe_failed" if probe_present else "no_script"
            watch.update(
                watch_method="unavailable",
                unavailable_reason=reason,
                next_check_at=None,
                last_result=(
                    "任务保持站岗存活；探测脚本此前连续失败已熔断，可唤醒 Scout 修复、重试脚本或开启定时巡检兜底"
                    if reason == "probe_failed"
                    else "任务保持站岗存活；未检测到探测脚本，可唤醒 Scout 收集/补脚本或手动开启定时巡检兜底"
                ),
            )
        watch = await self._save_watch(parent_id, watch)
        await self._sync_scout_agent_for_watch(parent_id, watch)
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

        if action in {"resume_watch", "enable_sentinel"}:
            if action == "enable_sentinel" and not probe_present:
                raise ConflictError(
                    "未检测到零 Token 探测脚本（/workspace/watch_probe.py），请唤醒 Scout 生成脚本或开启定时巡检兜底"
                )
            if probe_present:
                interval = DEFAULT_PROBE_INTERVAL_SECONDS if interval_seconds is None else int(interval_seconds)
                if not MIN_SENTINEL_INTERVAL_SECONDS <= interval <= MAX_SENTINEL_INTERVAL_SECONDS:
                    raise ContractError(
                        f"哨兵探测间隔必须在 {MIN_SENTINEL_INTERVAL_SECONDS}~{MAX_SENTINEL_INTERVAL_SECONDS} 秒之间"
                    )
                watch.update(
                    status="collecting" if collecting else "watching",
                    watch_method="sentinel",
                    unavailable_reason=None,
                    probing=False,
                    probe_interval_seconds=interval,
                    next_check_at=None if collecting else now + interval,
                    consecutive_failures=0,
                    last_error=None,
                    last_result=f"已恢复零 Token 脚本哨兵站岗（每 {interval}s 探测新题）",
                )
                msg = f"Scout 已进入站岗（零 Token 脚本哨兵，每 {interval} 秒探测一次）"
            else:
                watch.update(
                    status="collecting" if collecting else "watching",
                    watch_method="unavailable",
                    unavailable_reason="no_script",
                    probing=False,
                    next_check_at=None,
                    last_result="已进入站岗态；未检测到探测脚本，可唤醒 Scout 补脚本或开启定时巡检兜底",
                )
                msg = "Scout 已进入站岗态（当前无探测脚本，可唤醒补脚本或开启定时巡检兜底）"
            watch = await self._save_watch(parent_id, watch)
            if not collecting:
                await self._sync_scout_agent_for_watch(parent_id, watch)
            await self.aoe.events.emit(
                parent_id,
                actor=SYSTEM_ACTOR,
                kind=EventKind.SYSTEM,
                text=msg,
                channel_agent_id="scout",
                payload={"scout_watch": watch},
            )
            self.aoe.wake(parent_id)
            return watch

        if action == "enable_llm_poll":
            if (
                probe_present
                and watch.get("status") == "watching"
                and watch.get("watch_method") == "sentinel"
            ):
                raise ConflictError("当前零 Token 脚本哨兵运行正常，无需切换为耗 Token 定时巡检")
            interval = DEFAULT_LLM_POLL_INTERVAL_SECONDS if interval_seconds is None else int(interval_seconds)
            if not MIN_LLM_INTERVAL_SECONDS <= interval <= MAX_LLM_INTERVAL_SECONDS:
                raise ContractError(f"定时巡检间隔必须在 {MIN_LLM_INTERVAL_SECONDS}~{MAX_LLM_INTERVAL_SECONDS} 秒之间")
            watch.update(
                status="collecting" if collecting else "watching",
                watch_method="llm_poll",
                unavailable_reason=None,
                probing=False,
                llm_interval_seconds=interval,
                next_check_at=None if collecting else now + interval,
                last_result=f"已开启定时 LLM 巡检兜底（每 {max(1, round(interval / 60))} 分钟唤醒一次 · 耗 Token）",
            )
            watch = await self._save_watch(parent_id, watch)
            if not collecting:
                await self._sync_scout_agent_for_watch(parent_id, watch)
            await self.aoe.events.emit(
                parent_id,
                actor=SYSTEM_ACTOR,
                kind=EventKind.SYSTEM,
                text=f"已开启 Scout 定时 LLM 巡检兜底（每 {max(1, round(interval / 60))} 分钟唤醒一次）",
                channel_agent_id="scout",
                payload={"scout_watch": watch},
            )
            self.aoe.wake(parent_id)
            return watch

        if action == "disable_llm_poll":
            if probe_present and watch.get("consecutive_failures", 0) < MAX_CONSECUTIVE_PROBE_FAILURES:
                interval = int(watch.get("probe_interval_seconds") or DEFAULT_PROBE_INTERVAL_SECONDS)
                watch.update(
                    status="collecting" if collecting else "watching",
                    watch_method="sentinel",
                    unavailable_reason=None,
                    next_check_at=None if collecting else now + interval,
                    last_result=f"已关闭定时 LLM 巡检，恢复零 Token 脚本哨兵（每 {interval}s 探测新题）",
                )
            else:
                reason: UnavailableReason = "probe_failed" if probe_present else "no_script"
                watch.update(
                    status="collecting" if collecting else "watching",
                    watch_method="unavailable",
                    unavailable_reason=reason,
                    next_check_at=None,
                    last_result="已关闭定时 LLM 巡检兜底（可随时手动唤醒 Scout 收集）",
                )
            watch = await self._save_watch(parent_id, watch)
            if not collecting:
                await self._sync_scout_agent_for_watch(parent_id, watch)
            await self.aoe.events.emit(
                parent_id,
                actor=SYSTEM_ACTOR,
                kind=EventKind.SYSTEM,
                text="已关闭 Scout 定时 LLM 巡检兜底",
                channel_agent_id="scout",
                payload={"scout_watch": watch},
            )
            self.aoe.wake(parent_id)
            return watch

        if action == "disable":
            watch.update(
                status="collecting" if collecting else "stopped",
                probing=False,
                next_check_at=None,
                last_result="已手动停止 Scout（不再自动站岗或收集，可随时唤醒收集或恢复站岗）",
            )
            watch = await self._save_watch(parent_id, watch)
            if not collecting:
                await self._sync_scout_agent_for_watch(parent_id, watch)
            await self.aoe.events.emit(
                parent_id,
                actor=SYSTEM_ACTOR,
                kind=EventKind.SYSTEM,
                text="已停止 Scout，进入静止状态",
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
        if watch["status"] != "watching" or watch["watch_method"] != "sentinel":
            return watch
        if not self.has_probe_script(parent_id):
            watch.update(
                status="watching",
                watch_method="unavailable",
                unavailable_reason="no_script",
                probing=False,
                next_check_at=None,
                last_result="探测脚本已不存在，请唤醒 Scout 补脚本或开启定时巡检兜底",
            )
            watch = await self._save_watch(parent_id, watch)
            await self._sync_scout_agent_for_watch(parent_id, watch)
            return watch

        watch.update(probing=True, next_check_at=None)
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
            watch["probing"] = False
            watch["last_checked_at"] = now
            watch["last_check_kind"] = "sentinel"
            watch["consecutive_failures"] = failures
            watch["last_error"] = err_text
            if failures >= MAX_CONSECUTIVE_PROBE_FAILURES:
                watch.update(
                    status="watching",
                    watch_method="unavailable",
                    unavailable_reason="probe_failed",
                    next_check_at=None,
                    last_result=(
                        f"脚本哨兵连续 {failures} 次失败已熔断；"
                        f"请唤醒 Scout 修复脚本、重试脚本或开启定时巡检兜底：{err_text}"
                    ),
                )
                watch = await self._save_watch(parent_id, watch)
                await self._sync_scout_agent_for_watch(parent_id, watch, last_error=err_text)
                await self.aoe.events.emit(
                    parent_id,
                    actor=SYSTEM_ACTOR,
                    kind=EventKind.ERROR,
                    text=(
                        f"零 Token 脚本哨兵连续 {failures} 次探测失败已熔断（绝不自动耗 Token）；"
                        f"请唤醒 Scout 修复脚本或手动开启定时巡检兜底：{err_text}"
                    ),
                    channel_agent_id="scout",
                    payload={"consecutive_failures": failures, "error": err_text},
                )
            else:
                backoff = 60 if failures == 1 else 120
                watch.update(
                    status="watching",
                    watch_method="sentinel",
                    unavailable_reason=None,
                    next_check_at=now + backoff,
                    last_result=(
                        f"脚本哨兵探测异常（第 {failures}/{MAX_CONSECUTIVE_PROBE_FAILURES} 次，"
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

        watch["probing"] = False
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
                status="collecting",
                watch_method="sentinel",
                unavailable_reason=None,
                next_check_at=None,
                last_result=f"零 Token 脚本哨兵发现 {len(new_keys)} 道新题（{preview}），正在唤醒 Scout 收集",
            )
            await self._save_watch(parent_id, watch)
            await self.aoe.events.emit(
                parent_id,
                actor=SYSTEM_ACTOR,
                kind=EventKind.SYSTEM,
                text=f"零 Token 脚本哨兵发现 {len(new_keys)} 道新题（{preview}），立即唤醒 Scout 增量收集",
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
            status="watching",
            watch_method="sentinel",
            unavailable_reason=None,
            next_check_at=now + interval,
            last_result=f"零 Token 脚本哨兵探测正常 · 暂无新题{extra}",
        )
        watch = await self._save_watch(parent_id, watch)
        self.aoe.wake(parent_id)
        return watch

    def next_due(self, parent: TaskRun) -> float | None:
        if not parent.metadata.get("collection_complete") or parent.metadata.get("scout_collecting"):
            return None
        watch = parent.metadata.get("scout_watch")
        if not isinstance(watch, dict):
            return None
        if watch.get("status") != "watching" or watch.get("probing"):
            return None
        if watch.get("watch_method") not in {"sentinel", "llm_poll"}:
            return None
        due = watch.get("next_check_at")
        return float(due) if isinstance(due, (int, float)) else None


__all__ = [
    "AOEScoutWatch",
    "DEFAULT_LLM_POLL_INTERVAL_SECONDS",
    "DEFAULT_PROBE_INTERVAL_SECONDS",
    "MAX_CONSECUTIVE_PROBE_FAILURES",
    "PROBE_TIMEOUT_SECONDS",
    "ScoutStatus",
    "UnavailableReason",
    "WATCH_PROBE_FILENAME",
    "WatchAction",
    "WatchMethod",
    "WatchMode",
    "WatchState",
    "default_watch_state",
    "parse_probe_output",
]
