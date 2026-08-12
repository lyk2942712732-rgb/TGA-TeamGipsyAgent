"""Thin adapters to third-party frameworks and services."""

from tga2.integrations.mcp import (
    MCPConfig,
    MCPConfigRepository,
    MCPServer,
    load_mcp_tools,
)
from tga2.integrations.model import ModelSettings, build_chat_model

__all__ = [
    "MCPConfig",
    "MCPConfigRepository",
    "MCPServer",
    "ModelSettings",
    "build_chat_model",
    "load_mcp_tools",
]
