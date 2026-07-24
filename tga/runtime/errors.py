"""Structured runtime errors that can be resolved outside the active runner."""

from __future__ import annotations


class RuntimeConfigurationError(RuntimeError):
    def __init__(self, *, code: str, message: str, phase: str = "provider") -> None:
        super().__init__(message)
        self.code = code
        self.phase = phase
        self.retryable = True
