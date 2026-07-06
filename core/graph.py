"""LangGraph assembly for the CTF Core MVP."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal

from langchain_core.messages import AIMessage
from langgraph.graph import END, START, StateGraph

from core.config import CoreConfig
from core.contracts import BoundChatModel, ToolLike
from core.middleware import budget_exhausted, should_reflect
from core.nodes import (
    DecisionNode,
    FlagVerificationNode,
    GuardedToolExecutor,
    ObservationNode,
    ReflectionNode,
    TerminalNode,
    UserFeedbackNode,
)
from core.nodes.decision import message_text
from core.nodes.flag_verification import extract_claimed_flags
from core.state import AgentStatus, CTFAgentState
from core.state.agent_state import read_blackboard

Route = Literal[
    "decision",
    "tools",
    "observation",
    "flag_verification",
    "reflection",
    "user_feedback",
    "terminal",
    "__end__",
]


def _last_ai(state: CTFAgentState) -> AIMessage | None:
    for message in reversed(state.get("messages", [])):
        if isinstance(message, AIMessage):
            return message
    return None


def build_graph(
    *,
    model: BoundChatModel,
    tools: Sequence[ToolLike],
    config: CoreConfig,
    checkpointer: Any,
) -> Any:
    """Build and compile the custom state graph."""

    decision = DecisionNode(model, config)
    executor = GuardedToolExecutor(tools, config)
    observation = ObservationNode(config)
    flag_verification = FlagVerificationNode(config)
    reflection = ReflectionNode(config)
    user_feedback = UserFeedbackNode()
    terminal = TerminalNode()

    builder = StateGraph(CTFAgentState)
    builder.add_node("decision", decision)
    builder.add_node("tools", executor)
    builder.add_node("observation", observation)
    builder.add_node("flag_verification", flag_verification)
    builder.add_node("reflection", reflection)
    builder.add_node("user_feedback", user_feedback)
    builder.add_node("terminal", terminal)

    def route_entry(state: CTFAgentState) -> Route:
        board = read_blackboard(state)
        if board.status in {AgentStatus.COMPLETED, AgentStatus.FAILED}:
            return "terminal"
        if board.awaiting_user_submission:
            return "user_feedback"
        return "decision"

    def route_after_decision(state: CTFAgentState) -> Route:
        assistant = _last_ai(state)
        if assistant and assistant.tool_calls:
            return "tools"
        if assistant:
            claims = extract_claimed_flags(
                message_text(assistant.content),
                config.flag_patterns,
            )
            if claims:
                return "flag_verification"
        if budget_exhausted(read_blackboard(state), config):
            return "terminal"
        return "reflection"

    def route_after_observation(state: CTFAgentState) -> Route:
        board = read_blackboard(state)
        if budget_exhausted(board, config):
            return "terminal"
        if should_reflect(board, config):
            return "reflection"
        return "decision"

    def route_after_flag(state: CTFAgentState) -> Route:
        board = read_blackboard(state)
        if board.awaiting_user_submission:
            return END
        if budget_exhausted(board, config):
            return "terminal"
        return "reflection"

    def route_after_feedback(state: CTFAgentState) -> Route:
        board = read_blackboard(state)
        if board.status == AgentStatus.COMPLETED:
            return "terminal"
        if board.awaiting_user_submission:
            return END
        return "reflection"

    def route_after_reflection(state: CTFAgentState) -> Route:
        board = read_blackboard(state)
        if board.status == AgentStatus.FAILED or budget_exhausted(board, config):
            return "terminal"
        return "decision"

    builder.add_conditional_edges(
        START,
        route_entry,
        {
            "decision": "decision",
            "user_feedback": "user_feedback",
            "terminal": "terminal",
        },
    )
    builder.add_conditional_edges(
        "decision",
        route_after_decision,
        {
            "tools": "tools",
            "flag_verification": "flag_verification",
            "reflection": "reflection",
            "terminal": "terminal",
        },
    )
    builder.add_edge("tools", "observation")
    builder.add_conditional_edges(
        "observation",
        route_after_observation,
        {
            "decision": "decision",
            "reflection": "reflection",
            "terminal": "terminal",
        },
    )
    builder.add_conditional_edges(
        "flag_verification",
        route_after_flag,
        {
            END: END,
            "reflection": "reflection",
            "terminal": "terminal",
        },
    )
    builder.add_conditional_edges(
        "user_feedback",
        route_after_feedback,
        {
            END: END,
            "reflection": "reflection",
            "terminal": "terminal",
        },
    )
    builder.add_conditional_edges(
        "reflection",
        route_after_reflection,
        {
            "decision": "decision",
            "terminal": "terminal",
        },
    )
    builder.add_edge("terminal", END)

    return builder.compile(checkpointer=checkpointer)
