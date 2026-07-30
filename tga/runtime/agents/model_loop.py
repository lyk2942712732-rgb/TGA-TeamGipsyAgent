"""One provider turn for a single Solver."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ModelTurn:
    response: dict[str, Any]
    duration_ms: float


class ModelLoop:
    def __init__(self, gateway: Any) -> None:
        self.gateway = gateway

    def run(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> ModelTurn:
        started = time.perf_counter()
        response = self.gateway.chat_tools(
            messages,
            tools=tools,
            temperature=getattr(self.gateway, "temperature", 0.2),
        )
        if not isinstance(response, dict) or not isinstance(response.get("message"), dict):
            raise ValueError("ModelGateway returned an invalid tool-loop response")
        return ModelTurn(
            response=response,
            duration_ms=round((time.perf_counter() - started) * 1000, 3),
        )


__all__ = ["ModelLoop", "ModelTurn"]
