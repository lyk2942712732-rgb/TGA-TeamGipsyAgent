"""Deterministic Flag extraction and LLM/tool evidence comparison."""

from __future__ import annotations

import re
from typing import Any

from langchain_core.messages import AIMessage

from core.config import CoreConfig
from core.middleware.tool_guard import call_fingerprint, normalize_args, result_fingerprint
from core.nodes.decision import message_text
from core.state import CTFAgentState
from core.state.agent_state import read_blackboard, write_blackboard

FLAG_TAG_PATTERN = re.compile(r"^\[FLAG_CANDIDATE\]\s*(\S+)\s*$", re.MULTILINE)


def extract_flags(text: str, patterns: tuple[str, ...]) -> list[str]:
    found: list[str] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            value = match.group(0).strip()
            if value not in found:
                found.append(value)
    return found


def extract_claimed_flags(text: str, patterns: tuple[str, ...]) -> list[str]:
    tagged = [value.strip() for value in FLAG_TAG_PATTERN.findall(text)]
    valid: list[str] = []
    for value in tagged:
        if any(re.fullmatch(pattern, value) for pattern in patterns) and value not in valid:
            valid.append(value)
    return valid


class FlagVerificationNode:
    def __init__(self, config: CoreConfig) -> None:
        self.config = config

    async def __call__(self, state: CTFAgentState) -> dict[str, Any]:
        board = read_blackboard(state).copy_for_update()
        messages = state.get("messages", [])
        if not messages or not isinstance(messages[-1], AIMessage):
            return {"blackboard": write_blackboard(board)}

        claimed = extract_claimed_flags(
            message_text(messages[-1].content),
            self.config.flag_patterns,
        )

        for value in claimed:
            candidate = board.verify_llm_flag(value)
            if not candidate:
                continue
            response = AIMessage(
                content=(
                    "Flag 双重证据验证通过：LLM 输出与工具原始返回逐字一致。\n\n"
                    f"`{candidate.value}`\n\n"
                    "请你手动提交，并在本会话回复“提交成功”或“提交失败”。"
                )
            )
            return {
                "messages": [response],
                "blackboard": write_blackboard(board),
            }

        summary = "LLM 候选 Flag 与任何未拒绝的工具原始证据均不一致"
        args = {"claimed_flags": claimed}
        board.record_failure(
            tool_name="core_flag_verifier",
            normalized_args=normalize_args(args),
            call_fingerprint=call_fingerprint("core_flag_verifier", args),
            result_fingerprint=result_fingerprint(summary),
            result_summary=summary,
            error_type="flag_evidence_mismatch",
        )
        return {"blackboard": write_blackboard(board)}
