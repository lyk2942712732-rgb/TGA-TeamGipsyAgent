"""LangGraph state schema."""

from __future__ import annotations

from typing import Annotated, Any

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

from core.state.blackboard import Blackboard


class CTFAgentState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]
    blackboard: dict[str, Any]


def read_blackboard(state: CTFAgentState) -> Blackboard:
    """Restore the typed model from its checkpoint-safe JSON representation."""

    return Blackboard.model_validate(state["blackboard"])


def write_blackboard(board: Blackboard) -> dict[str, Any]:
    """Serialize custom models/enums before LangGraph checkpoints the state."""

    return board.model_dump(mode="json")
