"""Interpret manual platform-submission feedback in the same thread."""

from __future__ import annotations

from typing import Any, Literal

from langchain_core.messages import AIMessage, HumanMessage

from core.middleware.tool_guard import call_fingerprint, normalize_args, result_fingerprint
from core.nodes.decision import message_text
from core.state import CTFAgentState
from core.state.agent_state import read_blackboard, write_blackboard

SUCCESS_MARKERS = (
    "提交成功",
    "flag正确",
    "正确",
    "通过",
    "accepted",
    "success",
)
FAILURE_MARKERS = (
    "提交失败",
    "flag错误",
    "错误",
    "失败",
    "不正确",
    "未通过",
    "incorrect",
    "wrong",
    "invalid",
    "rejected",
)


def classify_feedback(text: str) -> Literal["success", "failed", "ambiguous"]:
    normalized = text.strip().casefold()
    if any(marker.casefold() in normalized for marker in FAILURE_MARKERS):
        return "failed"
    if any(marker.casefold() in normalized for marker in SUCCESS_MARKERS):
        return "success"
    return "ambiguous"


class UserFeedbackNode:
    async def __call__(self, state: CTFAgentState) -> dict[str, Any]:
        board = read_blackboard(state).copy_for_update()
        human = next(
            (
                message
                for message in reversed(state.get("messages", []))
                if isinstance(message, HumanMessage)
            ),
            None,
        )
        feedback = classify_feedback(message_text(human.content) if human else "")

        if feedback == "ambiguous":
            return {
                "messages": [
                    AIMessage(
                        content=(
                            "我正在等待这个 Flag 的手动提交结果。"
                            "请明确回复“提交成功”或“提交失败”。"
                        )
                    )
                ],
                "blackboard": write_blackboard(board),
            }

        candidate = board.pending_user_flag()
        if not candidate:
            return {
                "messages": [AIMessage(content="当前没有等待确认的 Flag。")],
                "blackboard": write_blackboard(board),
            }

        if feedback == "success":
            board.mark_user_submission(True)
            board.add_fact(
                content=f"Flag {candidate.value} 已由用户在平台手动提交确认正确",
                source_tool=candidate.source_tool,
                source_call_id=candidate.source_call_id,
                evidence=candidate.tool_evidence,
            )
            return {"blackboard": write_blackboard(board)}

        board.mark_user_submission(False)
        args = {"flag": candidate.value}
        summary = "用户手动提交后确认该 Flag 无效，可能是诱饵或提取错误"
        board.record_failure(
            tool_name="manual_flag_submission",
            normalized_args=normalize_args(args),
            call_fingerprint=call_fingerprint("manual_flag_submission", args),
            result_fingerprint=result_fingerprint(summary),
            result_summary=summary,
            error_type="flag_rejected_by_platform",
        )
        return {"blackboard": write_blackboard(board)}
