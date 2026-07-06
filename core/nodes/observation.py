"""Turn raw ToolMessages into durable blackboard evidence."""

from __future__ import annotations

import re
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage

from core.config import CoreConfig
from core.middleware import CORE_GUARD_BLOCKED, CORE_TOOL_ERROR
from core.middleware.tool_guard import (
    call_fingerprint,
    normalize_args,
    result_fingerprint,
)
from core.nodes.decision import message_text
from core.nodes.flag_verification import extract_flags
from core.state import CTFAgentState
from core.state.agent_state import read_blackboard, write_blackboard


def _tail_tool_messages(messages: list[Any]) -> list[ToolMessage]:
    tail: list[ToolMessage] = []
    for message in reversed(messages):
        if not isinstance(message, ToolMessage):
            break
        tail.append(message)
    return list(reversed(tail))


def _find_call(messages: list[Any], call_id: str) -> tuple[str, dict[str, Any]]:
    for message in reversed(messages):
        if not isinstance(message, AIMessage):
            continue
        for call in message.tool_calls or []:
            if str(call.get("id", "")) == call_id:
                args = call.get("args") if isinstance(call.get("args"), dict) else {}
                return str(call.get("name", "")), args
    return "", {}


def _error_type(content: str) -> str:
    remainder = content.removeprefix(CORE_TOOL_ERROR).strip()
    match = re.match(r"([A-Za-z_][A-Za-z0-9_]*):", remainder)
    return match.group(1) if match else "tool_error"


class ObservationNode:
    def __init__(self, config: CoreConfig) -> None:
        self.config = config

    async def __call__(self, state: CTFAgentState) -> dict[str, Any]:
        board = read_blackboard(state).copy_for_update()
        messages = state.get("messages", [])

        for tool_message in _tail_tool_messages(messages):
            content = message_text(tool_message.content)
            call_id = str(tool_message.tool_call_id)
            name, args = _find_call(messages, call_id)
            name = name or str(getattr(tool_message, "name", "") or "unknown")
            normalized = normalize_args(args)
            call_key = call_fingerprint(name, normalized)
            excerpt = content[: self.config.evidence_excerpt_chars]

            if content.startswith(CORE_GUARD_BLOCKED):
                board.no_progress_count += 1
                continue

            if content.startswith(CORE_TOOL_ERROR):
                board.record_failure(
                    tool_name=name,
                    normalized_args=normalized,
                    call_fingerprint=call_key,
                    result_fingerprint=result_fingerprint(content),
                    result_summary=excerpt,
                    error_type=_error_type(content),
                )
                continue

            board.add_fact(
                content=f"{name} 返回真实观测：{' '.join(excerpt.split())}",
                source_tool=name,
                source_call_id=call_id,
                evidence=excerpt,
            )
            board.clear_failure_streak()

            for flag in extract_flags(content, self.config.flag_patterns):
                board.add_tool_flag(
                    value=flag,
                    source_tool=name,
                    source_call_id=call_id,
                    tool_evidence=excerpt,
                )

        return {"blackboard": write_blackboard(board)}
