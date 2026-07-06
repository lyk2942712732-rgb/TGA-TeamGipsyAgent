"""Produce a concise terminal response."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage

from core.state import AgentStatus, CTFAgentState
from core.state.agent_state import read_blackboard, write_blackboard


class TerminalNode:
    async def __call__(self, state: CTFAgentState) -> dict[str, Any]:
        board = read_blackboard(state).copy_for_update()

        if board.status == AgentStatus.COMPLETED:
            candidate = next(
                (
                    item
                    for item in reversed(board.flag_candidates)
                    if item.user_submission_status.value == "success"
                ),
                None,
            )
            path = board.current_path.name if board.current_path else "未记录"
            content = (
                "任务完成：用户已确认平台提交成功。\n"
                f"- Flag：`{candidate.value if candidate else '未记录'}`\n"
                f"- 最终攻击路径：{path}\n"
                f"- 已确认事实：{len(board.confirmed_facts)} 条"
            )
        else:
            board.status = AgentStatus.FAILED
            content = (
                "Agent 已达到本次 MVP 的执行预算，暂停自动尝试。\n"
                f"- 已执行决策：{board.step_count}\n"
                f"- 已确认事实：{len(board.confirmed_facts)} 条\n"
                f"- 失败尝试：{len(board.failed_attempts)} 条\n"
                "可以在保留同一黑板的前提下调整预算或补充人工线索后继续。"
            )

        return {
            "messages": [AIMessage(content=content)],
            "blackboard": write_blackboard(board),
        }
