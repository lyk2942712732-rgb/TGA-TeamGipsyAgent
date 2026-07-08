"""Shared model client interfaces."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ModelMessage:
    role: str
    content: str


@dataclass(frozen=True)
class ModelResponse:
    content: str
    model: str
    raw: dict


class ModelClient(Protocol):
    model: str

    def chat(self, messages: list[ModelMessage], *, temperature: float = 0.2) -> ModelResponse:
        """Send chat messages and return a normalized model response."""
