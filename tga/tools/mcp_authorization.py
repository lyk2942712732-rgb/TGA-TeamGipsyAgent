"""Explicit authorization context for MCP visibility and call policy.

MCP policy decisions depend only on the task mode, the creation-time capability
snapshot and the execution boundaries.  Declaring that surface explicitly means
operator-driven method tests do not have to fabricate a persisted `TGATask`
(which would require a verified model snapshot it cannot have).  `TGATask`
satisfies this protocol structurally.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from tga.contracts import ExecutionPolicy, MCPCapabilitySnapshot
from tga.modes import TaskMode


class MCPAuthorizationContext(Protocol):
    @property
    def id(self) -> str: ...

    @property
    def mode(self) -> TaskMode: ...

    @property
    def mcp_capabilities(self) -> MCPCapabilitySnapshot: ...

    @property
    def execution_policy(self) -> ExecutionPolicy: ...


@dataclass(frozen=True)
class OperatorMCPContext:
    """Authorization context for an explicit, operator-authorized method test."""

    mode: TaskMode
    mcp_capabilities: MCPCapabilitySnapshot
    execution_policy: ExecutionPolicy
    id: str = "mcp_method_test"


__all__ = ["MCPAuthorizationContext", "OperatorMCPContext"]
