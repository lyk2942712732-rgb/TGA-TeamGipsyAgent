"""Connection registry for worker-initiated JSON-RPC WebSockets."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID

from .errors import RuntimeUnavailableError
from .protocol import EVENT_METHODS, RpcMessage


class Socket(Protocol):
    async def accept(self) -> None: ...
    async def receive_text(self) -> str: ...
    async def send_text(self, data: str) -> None: ...
    async def close(self, code: int = 1000) -> None: ...


EventHandler = Callable[[UUID, str, str, dict[str, Any]], Awaitable[None]]


@dataclass
class AgentConnection:
    task_id: UUID
    agent_id: str
    socket: Socket
    pending: dict[str, asyncio.Future[Any]] = field(default_factory=dict)
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    counter: int = 0

    async def send(self, message: RpcMessage) -> None:
        async with self.send_lock:
            await self.socket.send_text(message.model_dump_json(exclude_none=True))

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        await self.send(RpcMessage.notification(method, params))

    async def request(self, method: str, params: dict[str, Any] | None = None, timeout: float = 30) -> Any:
        self.counter += 1
        request_id = f"control-{self.counter}"
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        try:
            await self.send(RpcMessage.request(request_id, method, params))
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            self.pending.pop(request_id, None)


class AgentGateway:
    def __init__(self, on_event: EventHandler) -> None:
        self.on_event = on_event
        self.connections: dict[tuple[UUID, str], AgentConnection] = {}
        self._lock = asyncio.Lock()

    def connected(self, task_id: UUID, agent_id: str) -> bool:
        return (task_id, agent_id) in self.connections

    async def attach(self, task_id: UUID, agent_id: str, socket: Socket) -> None:
        await socket.accept()
        connection = AgentConnection(task_id=task_id, agent_id=agent_id, socket=socket)
        key = (task_id, agent_id)
        async with self._lock:
            old = self.connections.get(key)
            self.connections[key] = connection
        if old:
            await old.socket.close(code=1012)
        try:
            while True:
                message = RpcMessage.model_validate_json(await socket.receive_text())
                if message.method is None:
                    pending = connection.pending.get(str(message.id))
                    if pending and not pending.done():
                        if message.error:
                            pending.set_exception(RuntimeUnavailableError(message.error.get("message", "RPC error")))
                        else:
                            pending.set_result(message.result)
                    continue
                if message.method not in EVENT_METHODS:
                    if message.id is not None:
                        await connection.send(RpcMessage.failure(message.id, -32601, "method not allowed"))
                    continue
                try:
                    await self.on_event(task_id, agent_id, message.method, message.params or {})
                    if message.id is not None:
                        await connection.send(RpcMessage.success(message.id, {"accepted": True}))
                except Exception as exc:  # boundary: report a stable RPC error
                    if message.id is not None:
                        await connection.send(RpcMessage.failure(message.id, -32000, str(exc)))
        finally:
            was_current = False
            async with self._lock:
                if self.connections.get(key) is connection:
                    self.connections.pop(key, None)
                    was_current = True
            if was_current:
                await self.on_event(task_id, agent_id, "session.disconnected", {})

    async def request(self, task_id: UUID, agent_id: str, method: str, params: dict[str, Any] | None = None) -> Any:
        connection = self.connections.get((task_id, agent_id))
        if connection is None:
            raise RuntimeUnavailableError(f"agent is not connected: {task_id}/{agent_id}")
        return await connection.request(method, params)

    async def notify(self, task_id: UUID, agent_id: str, method: str, params: dict[str, Any] | None = None) -> None:
        connection = self.connections.get((task_id, agent_id))
        if connection:
            await connection.notify(method, params)

    async def broadcast(self, task_id: UUID, method: str, params: dict[str, Any] | None = None) -> None:
        await asyncio.gather(
            *(
                connection.notify(method, params)
                for (candidate, _), connection in tuple(self.connections.items())
                if candidate == task_id
            ),
            return_exceptions=True,
        )

    async def disconnect_task(self, task_id: UUID) -> None:
        """Detach a task before its persistent state is deleted."""
        async with self._lock:
            connections = [
                connection
                for (candidate, _), connection in tuple(self.connections.items())
                if candidate == task_id
            ]
            for connection in connections:
                self.connections.pop((connection.task_id, connection.agent_id), None)
        await asyncio.gather(
            *(connection.socket.close(code=1001) for connection in connections),
            return_exceptions=True,
        )


__all__ = ["AgentGateway"]
