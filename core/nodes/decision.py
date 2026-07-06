"""LLM decision node and explicit blackboard-directive parser."""

from __future__ import annotations

import re
from typing import Any

from langchain_core.messages import AIMessage

from core.config import CoreConfig
from core.contracts import BoundChatModel
from core.prompts import build_model_messages
from core.state import Blackboard, CTFPhase, CTFAgentState, HypothesisStatus
from core.state.agent_state import read_blackboard, write_blackboard

PATH_PATTERN = re.compile(r"^\[PATH\]\s*(.+)$", re.MULTILINE)
HYPOTHESIS_PATTERN = re.compile(r"^\[HYPOTHESIS\]\s*(.+)$", re.MULTILINE)
CONFIRM_PATTERN = re.compile(r"^\[CONFIRM\]\s*([A-Za-z0-9_-]+)\s*$", re.MULTILINE)
REJECT_PATTERN = re.compile(r"^\[REJECT\]\s*([A-Za-z0-9_-]+)\s*$", re.MULTILINE)


def message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "\n".join(parts)
    return str(content or "")


def _split_directive(value: str, expected: int) -> list[str]:
    parts = [part.strip() for part in value.split("|", maxsplit=expected - 1)]
    return parts + [""] * (expected - len(parts))


def apply_board_directives(board: Blackboard, text: str) -> None:
    path_matches = PATH_PATTERN.findall(text)
    if path_matches:
        name, goal, next_step = _split_directive(path_matches[-1], 3)
        board.set_path(name, goal, next_step)
        if board.phase == CTFPhase.RECON:
            board.phase = CTFPhase.ANALYSIS

    for raw in HYPOTHESIS_PATTERN.findall(text):
        content, verification = _split_directive(raw, 2)
        board.add_hypothesis(content, verification)

    for hypothesis_id in CONFIRM_PATTERN.findall(text):
        board.update_hypothesis(hypothesis_id, HypothesisStatus.CONFIRMED)

    for hypothesis_id in REJECT_PATTERN.findall(text):
        board.update_hypothesis(hypothesis_id, HypothesisStatus.REJECTED)


class DecisionNode:
    def __init__(self, model: BoundChatModel, config: CoreConfig) -> None:
        self.model = model
        self.config = config

    async def __call__(self, state: CTFAgentState) -> dict[str, Any]:
        board = read_blackboard(state).copy_for_update()
        board.step_count += 1

        request = build_model_messages(board, state.get("messages", []), self.config)
        response = await self.model.ainvoke(request)
        if not isinstance(response, AIMessage):
            response = AIMessage(content=getattr(response, "content", str(response)))

        text = message_text(response.content)
        apply_board_directives(board, text)
        if not response.tool_calls and "[FLAG_CANDIDATE]" not in text:
            board.no_progress_count += 1

        return {"messages": [response], "blackboard": write_blackboard(board)}
