"""Typed integration seams between Core, models, and external tools."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, Protocol, TypeAlias, runtime_checkable

from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.tools import BaseTool

ToolLike: TypeAlias = BaseTool | Callable[..., Any]


@runtime_checkable
class BoundChatModel(Protocol):
    async def ainvoke(self, messages: Sequence[BaseMessage], **kwargs: Any) -> AIMessage:
        """Return one assistant message."""


@runtime_checkable
class ToolCallingModel(Protocol):
    def bind_tools(self, tools: Sequence[ToolLike]) -> BoundChatModel:
        """Bind the tools exposed to the model."""


def tool_name(tool: ToolLike) -> str:
    """Return the stable name used in model tool calls."""

    name = getattr(tool, "name", None) or getattr(tool, "__name__", None)
    if not name:
        raise TypeError(f"Tool {tool!r} has no stable name")
    return str(name)
