"""Provider contract; implementations must fail closed."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from typing import Protocol

from tga.sandbox.models import (
    ExecFrame,
    ExecResult,
    ProcessSpec,
    SandboxHandle,
    SandboxInspection,
)


class SandboxError(RuntimeError):
    def __init__(self, message: str, *, code: str = "SANDBOX_ERROR", retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class SandboxProcess(Protocol):
    process_id: str

    def send(self, data: bytes) -> None: ...
    def receive(self, timeout: float) -> ExecFrame: ...
    def close_stdin(self) -> None: ...
    def wait(self, timeout: float | None = None) -> ExecResult: ...
    def close(self) -> None: ...


class SandboxProvider(Protocol):
    provider_name: str

    def acquire(
        self,
        *,
        task_id: str,
        solver_id: str,
        solver_run_id: str,
        profile_id: str,
        fencing_token: int,
        idempotency_key: str,
    ) -> SandboxHandle: ...

    def exec(self, handle: SandboxHandle, spec: ProcessSpec) -> tuple[Iterator[ExecFrame], ExecResult]: ...
    def open_process(self, handle: SandboxHandle, spec: ProcessSpec) -> SandboxProcess: ...
    def stop_process(self, process_id: str, *, fencing_token: int) -> None: ...
    def inspect(self, handle: SandboxHandle) -> SandboxInspection: ...
    def release(self, handle: SandboxHandle) -> None: ...
    def destroy(self, handle: SandboxHandle) -> None: ...
    def reconcile(
        self,
        valid_instance_ids: tuple[str, ...],
        *,
        grace_before: datetime,
    ) -> tuple[str, ...]: ...
