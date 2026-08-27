"""SDK-neutral worker session controlled through JSON-RPC WebSocket."""

from __future__ import annotations

import asyncio
import os
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

from tga3.protocol import CONTROL_METHODS, RpcMessage

Emit = Callable[[str, dict[str, Any]], Awaitable[None]]
_BLACKBOARD_WAKE = object()


class AgentAdapter(ABC):
    @abstractmethod
    async def run_cycle(self, prompt: str, emit: Emit) -> str: ...

    @abstractmethod
    async def set_model(self, params: dict[str, Any]) -> None: ...


class WorkerSession:
    def __init__(self, adapter: AgentAdapter) -> None:
        self.adapter = adapter
        self.task_id = os.environ["TGA3_TASK_ID"]
        self.agent_id = os.environ["TGA3_AGENT_ID"]
        self.control_url = f"{os.environ['TGA3_CONTROL_WS_URL'].rstrip('/')}/{self.task_id}/{self.agent_id}"
        self.sync_seconds = int(os.environ["TGA3_SYNC_SECONDS"])
        self.session_id = str(uuid4())
        self.queue: asyncio.Queue[str | object | None] = asyncio.Queue()
        self.paused = asyncio.Event()
        self.paused.set()
        self.stop = asyncio.Event()
        self.socket: Any = None
        self.send_lock = asyncio.Lock()
        self.current_cycle: asyncio.Task[str] | None = None
        self.pending_blackboard_seq = 0
        self.blackboard_wake_queued = False

    async def _queue_blackboard_update(self, latest_seq: int) -> None:
        self.pending_blackboard_seq = max(self.pending_blackboard_seq, latest_seq)
        if not self.blackboard_wake_queued:
            self.blackboard_wake_queued = True
            await self.queue.put(_BLACKBOARD_WAKE)

    async def emit(self, method: str, params: dict[str, Any]) -> None:
        if self.socket is None:
            return
        async with self.send_lock:
            await self.socket.send(RpcMessage.notification(method, params).model_dump_json(exclude_none=True))

    async def _reply(self, request: RpcMessage, result: Any = None, error: str | None = None) -> None:
        if request.id is None:
            return
        response = RpcMessage.failure(request.id, -32000, error) if error else RpcMessage.success(request.id, result)
        async with self.send_lock:
            await self.socket.send(response.model_dump_json(exclude_none=True))

    async def _receive(self) -> None:
        async for raw in self.socket:
            message = RpcMessage.model_validate_json(raw)
            if message.method is None or message.method not in CONTROL_METHODS:
                continue
            params = message.params or {}
            try:
                if message.method == "session.start":
                    await self.queue.put(str(params.get("prompt") or os.environ["TGA3_STARTUP_PROMPT"]))
                elif message.method == "session.pause":
                    self.paused.clear()
                    if self.current_cycle and not self.current_cycle.done():
                        self.current_cycle.cancel()
                        await asyncio.gather(self.current_cycle, return_exceptions=True)
                    await self.emit("agent.status", {"state": "paused"})
                elif message.method == "session.resume":
                    self.paused.set()
                    await self.emit("agent.status", {"state": "running"})
                elif message.method == "session.stop":
                    self.stop.set()
                    if self.current_cycle and not self.current_cycle.done():
                        self.current_cycle.cancel()
                        await asyncio.gather(self.current_cycle, return_exceptions=True)
                    await self.queue.put(None)
                elif message.method == "session.set_model":
                    if self.current_cycle and not self.current_cycle.done():
                        self.current_cycle.cancel()
                        await asyncio.gather(self.current_cycle, return_exceptions=True)
                    await self.adapter.set_model(params)
                elif message.method == "session.add_prompt":
                    await self.queue.put(str(params["text"]))
                elif message.method == "blackboard.changed":
                    await self._queue_blackboard_update(int(params["latest_seq"]))
                await self._reply(message, {"accepted": True})
            except Exception as exc:
                await self._reply(message, error=str(exc))

    async def _work(self) -> None:
        await self.queue.put(os.environ["TGA3_STARTUP_PROMPT"])
        while not self.stop.is_set():
            try:
                prompt = await asyncio.wait_for(self.queue.get(), timeout=self.sync_seconds)
            except TimeoutError:
                prompt = os.environ["TGA3_PERIODIC_PROMPT"]
            if prompt is None:
                return
            if prompt is _BLACKBOARD_WAKE:
                latest_seq = self.pending_blackboard_seq
                self.pending_blackboard_seq = 0
                self.blackboard_wake_queued = False
                prompt = os.environ["TGA3_BLACKBOARD_CHANGED_PROMPT"].format(latest_seq=latest_seq)
            assert isinstance(prompt, str)
            await self.paused.wait()
            await self.emit("agent.status", {"state": "running"})
            await self.emit("agent.action.started", {"summary": "开始一个工作周期"})
            try:
                self.current_cycle = asyncio.create_task(self.adapter.run_cycle(prompt, self.emit))
                output = await self.current_cycle
                if output.strip():
                    await self.emit("agent.output.delta", {"text": output})
                await self.emit("agent.action.completed", {"summary": "工作周期完成"})
                await self.emit("agent.status", {"state": "idle"})
            except asyncio.CancelledError:
                if not self.stop.is_set():
                    await self.queue.put(prompt)
            except Exception as exc:
                await self.emit("agent.error", {"message": str(exc)})
                await asyncio.sleep(2)
            finally:
                self.current_cycle = None

    async def run(self) -> None:
        from websockets.asyncio.client import connect

        async with connect(self.control_url, max_size=4 * 1024 * 1024) as socket:
            self.socket = socket
            await self.emit(
                "session.hello",
                {"session_id": self.session_id, "runtime": os.environ["TGA3_AGENT_RUNTIME"]},
            )
            receiver = asyncio.create_task(self._receive())
            worker = asyncio.create_task(self._work())
            done, pending = await asyncio.wait({receiver, worker}, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                task.result()


__all__ = ["AgentAdapter", "Emit", "WorkerSession"]
