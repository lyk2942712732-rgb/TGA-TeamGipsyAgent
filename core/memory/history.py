"""Bound the chat transcript while the blackboard preserves durable knowledge."""

from __future__ import annotations

from collections.abc import Sequence

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage


def recent_messages(messages: Sequence[BaseMessage], limit: int) -> list[BaseMessage]:
    """Keep a recent window without orphaning leading tool results."""

    items = list(messages)
    if len(items) <= limit:
        return items

    start = len(items) - limit
    if isinstance(items[start], ToolMessage):
        tool_ids: set[str] = set()
        index = start
        while index < len(items) and isinstance(items[index], ToolMessage):
            tool_ids.add(items[index].tool_call_id)
            index += 1
        for candidate in range(start - 1, -1, -1):
            message = items[candidate]
            if not isinstance(message, AIMessage):
                continue
            call_ids = {call.get("id", "") for call in (message.tool_calls or [])}
            if tool_ids & call_ids:
                start = candidate
                break
    return items[start:]
