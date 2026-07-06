"""Inject a strategy change when the graph detects no progress."""

from __future__ import annotations

from typing import Any

from core.config import CoreConfig
from core.middleware import budget_exhausted
from core.state import AgentStatus, CTFAgentState
from core.state.agent_state import read_blackboard, write_blackboard


class ReflectionNode:
    def __init__(self, config: CoreConfig) -> None:
        self.config = config

    async def __call__(self, state: CTFAgentState) -> dict[str, Any]:
        board = read_blackboard(state).copy_for_update()
        board.reflection_count += 1

        if budget_exhausted(board, self.config):
            board.status = AgentStatus.FAILED
            return {"blackboard": write_blackboard(board)}

        reason = (
            board.failed_attempts[-1].result_summary
            if board.failed_attempts
            else "上一轮没有调用工具，也没有产生可验证的新证据"
        )
        if board.failure_streak >= self.config.repeat_failure_threshold:
            board.block_current_path(
                f"同一工具、参数和错误连续出现 {board.failure_streak} 次"
            )

        board.reflection_guidance = (
            f"失败/无进展原因：{reason[:500]}。"
            "停止原样重复；重新检查已确认事实与待验证假设，"
            "选择不同参数、不同工具或实质不同的攻击路径。"
            "先用 [PATH] 和 [HYPOTHESIS] 更新黑板，再执行最小验证动作。"
        )
        return {"blackboard": write_blackboard(board)}
