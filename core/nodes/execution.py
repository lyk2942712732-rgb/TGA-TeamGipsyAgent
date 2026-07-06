"""Sequential guarded execution of tools supplied by roles 2 and 3."""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Sequence
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import BaseTool

from core.config import CoreConfig
from core.contracts import ToolLike, tool_name
from core.middleware import CORE_GUARD_BLOCKED, CORE_TOOL_ERROR, ToolGuard
from core.state import CTFAgentState
from core.state.agent_state import read_blackboard


def _result_text(result: Any) -> str:
    if isinstance(result, str):
        return result
    if isinstance(result, (dict, list, tuple)):
        return json.dumps(result, ensure_ascii=False, default=str)
    return str(result)


class GuardedToolExecutor:
    """Execute model calls only after deterministic repeat checks."""

    def __init__(self, tools: Sequence[ToolLike], config: CoreConfig) -> None:
        self.tools = {tool_name(tool): tool for tool in tools}
        self.guard = ToolGuard(config.repeat_failure_threshold)

    async def _invoke(self, tool: ToolLike, args: dict[str, Any]) -> Any:
        if isinstance(tool, BaseTool):
            return await tool.ainvoke(args)
        if inspect.iscoroutinefunction(tool):
            return await tool(**args)
        return await asyncio.to_thread(tool, **args)

    async def __call__(self, state: CTFAgentState) -> dict[str, Any]:
        messages = state.get("messages", [])
        if not messages or not isinstance(messages[-1], AIMessage):
            return {}

        assistant = messages[-1]
        board = read_blackboard(state)
        results: list[ToolMessage] = []

        for index, call in enumerate(assistant.tool_calls or []):
            name = str(call.get("name", ""))
            args = call.get("args") if isinstance(call.get("args"), dict) else {}
            call_id = str(call.get("id") or f"core_call_{board.step_count}_{index}")

            decision = self.guard.inspect(board, name, args)
            if not decision.allowed:
                results.append(
                    ToolMessage(
                        content=f"{CORE_GUARD_BLOCKED} {decision.reason}",
                        tool_call_id=call_id,
                        name=name,
                    )
                )
                continue

            tool = self.tools.get(name)
            if tool is None:
                results.append(
                    ToolMessage(
                        content=f"{CORE_TOOL_ERROR} UnknownTool: 未注册工具 {name}",
                        tool_call_id=call_id,
                        name=name,
                    )
                )
                continue

            try:
                result = await self._invoke(tool, args)
                content = _result_text(result)
            except Exception as exc:
                content = f"{CORE_TOOL_ERROR} {type(exc).__name__}: {exc}"

            results.append(
                ToolMessage(content=content, tool_call_id=call_id, name=name)
            )

        return {"messages": results}
