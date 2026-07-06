"""Stable facade for starting and resuming one CTF agent thread."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from core.config import CoreConfig
from core.contracts import ToolCallingModel, ToolLike, tool_name
from core.graph import build_graph
from core.nodes.decision import message_text
from core.state import AgentStatus, Blackboard, CTFAgentState
from core.state.agent_state import read_blackboard, write_blackboard


@dataclass(frozen=True)
class AgentRunResult:
    """Compact result returned to CLI/Web integration code."""

    response: str
    status: AgentStatus
    awaiting_user_submission: bool
    blackboard: Blackboard


class CTFAgent:
    def __init__(
        self,
        *,
        graph: Any,
        config: CoreConfig,
        exposed_tool_names: tuple[str, ...],
        hidden_tool_names: tuple[str, ...],
    ) -> None:
        self.graph = graph
        self.config = config
        self.exposed_tool_names = exposed_tool_names
        self.hidden_tool_names = hidden_tool_names

    def _run_config(self, thread_id: str) -> dict[str, Any]:
        if not thread_id.strip():
            raise ValueError("thread_id cannot be empty")
        return {
            "configurable": {"thread_id": thread_id},
            "recursion_limit": self.config.recursion_limit,
        }

    @staticmethod
    def _to_result(state: CTFAgentState) -> AgentRunResult:
        response = ""
        for message in reversed(state.get("messages", [])):
            if isinstance(message, AIMessage):
                response = message_text(message.content)
                break
        board = read_blackboard(state)
        return AgentRunResult(
            response=response,
            status=board.status,
            awaiting_user_submission=board.awaiting_user_submission,
            blackboard=board,
        )

    async def start(
        self,
        task: str,
        *,
        thread_id: str,
        challenge_id: str = "",
        title: str = "",
        target: str = "",
    ) -> AgentRunResult:
        """Start a new blackboard in a checkpointed thread."""

        if not task.strip():
            raise ValueError("task cannot be empty")
        board = Blackboard.create(
            task.strip(),
            challenge_id=challenge_id,
            title=title,
            target=target,
        )
        state = await self.graph.ainvoke(
            {
                "messages": [HumanMessage(content=task.strip())],
                "blackboard": write_blackboard(board),
            },
            self._run_config(thread_id),
        )
        return self._to_result(state)

    async def resume(self, user_message: str, *, thread_id: str) -> AgentRunResult:
        """Resume the same thread, including manual Flag feedback."""

        if not user_message.strip():
            raise ValueError("user_message cannot be empty")
        run_config = self._run_config(thread_id)
        snapshot = await self.graph.aget_state(run_config)
        if not snapshot.values or "blackboard" not in snapshot.values:
            raise LookupError(f"No saved CTF session for thread_id={thread_id!r}")
        state = await self.graph.ainvoke(
            {"messages": [HumanMessage(content=user_message.strip())]},
            run_config,
        )
        return self._to_result(state)

    async def get_blackboard(self, *, thread_id: str) -> Blackboard:
        snapshot = await self.graph.aget_state(self._run_config(thread_id))
        if not snapshot.values or "blackboard" not in snapshot.values:
            raise LookupError(f"No saved CTF session for thread_id={thread_id!r}")
        return Blackboard.model_validate(snapshot.values["blackboard"])


def create_ctf_agent(
    *,
    model: ToolCallingModel,
    tools: Sequence[ToolLike],
    config: CoreConfig | None = None,
    checkpointer: Any | None = None,
) -> CTFAgent:
    """Create the MVP agent and hide manual-only platform tools."""

    resolved_config = config or CoreConfig()
    exposed: list[ToolLike] = []
    hidden_names: list[str] = []
    seen_names: set[str] = set()

    for tool in tools:
        name = tool_name(tool)
        if name in seen_names:
            raise ValueError(f"Duplicate tool name: {name}")
        seen_names.add(name)
        if name in resolved_config.hidden_tool_names:
            hidden_names.append(name)
        else:
            exposed.append(tool)

    bound_model = model.bind_tools(exposed) if exposed else model
    saver = checkpointer or InMemorySaver()
    graph = build_graph(
        model=bound_model,
        tools=exposed,
        config=resolved_config,
        checkpointer=saver,
    )
    return CTFAgent(
        graph=graph,
        config=resolved_config,
        exposed_tool_names=tuple(tool_name(tool) for tool in exposed),
        hidden_tool_names=tuple(hidden_names),
    )
