"""Small JSON-RPC 2.0 contract used over one bidirectional WebSocket."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, model_validator


class RpcMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    jsonrpc: Literal["2.0"] = "2.0"
    id: str | int | None = None
    method: str | None = None
    params: dict[str, Any] | None = None
    result: Any = None
    error: dict[str, Any] | None = None

    @model_validator(mode="after")
    def valid_shape(self) -> RpcMessage:
        if self.method is not None and (self.result is not None or self.error is not None):
            raise ValueError("request/notification cannot contain result or error")
        if self.method is None and self.id is None:
            raise ValueError("response requires id")
        if self.error is not None and self.result is not None:
            raise ValueError("response cannot contain result and error")
        return self

    @classmethod
    def notification(cls, method: str, params: dict[str, Any] | None = None) -> RpcMessage:
        return cls(method=method, params=params or {})

    @classmethod
    def request(cls, request_id: str | int, method: str, params: dict[str, Any] | None = None) -> RpcMessage:
        return cls(id=request_id, method=method, params=params or {})

    @classmethod
    def success(cls, request_id: str | int, result: Any) -> RpcMessage:
        return cls(id=request_id, result=result)

    @classmethod
    def failure(cls, request_id: str | int, code: int, message: str, data: Any = None) -> RpcMessage:
        error = {"code": code, "message": message}
        if data is not None:
            error["data"] = data
        return cls(id=request_id, error=error)


CONTROL_METHODS = frozenset(
    {
        "session.start",
        "session.pause",
        "session.resume",
        "session.stop",
        "session.set_model",
        "session.add_prompt",
        "blackboard.changed",
    }
)

EVENT_METHODS = frozenset(
    {
        "session.hello",
        "session.heartbeat",
        "agent.status",
        "agent.output.delta",
        "agent.action.started",
        "agent.action.completed",
        "agent.needs_user_input",
        "agent.error",
    }
)


__all__ = ["CONTROL_METHODS", "EVENT_METHODS", "RpcMessage"]
