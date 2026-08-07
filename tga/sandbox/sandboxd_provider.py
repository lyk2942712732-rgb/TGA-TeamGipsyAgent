"""Lazy gRPC client for the Linux-only privileged sandbox daemon."""

from __future__ import annotations

import platform
import queue
import threading
import time
from collections.abc import Iterator
from datetime import datetime

from tga.sandbox.config import SandboxConfig
from tga.sandbox.models import (
    ExecFrame,
    ExecResult,
    ProcessSpec,
    SandboxHandle,
    SandboxInspection,
    SandboxState,
)
from tga.sandbox.provider import SandboxError, SandboxProcess


class SandboxdProvider:
    provider_name = "sandboxd"

    def __init__(self, config: SandboxConfig, *, stub=None):
        self.config = config
        self._stub = stub
        self._health_checked = False

    def _client(self):
        if platform.system() != "Linux" and self._stub is None:
            raise SandboxError("tga-sandboxd is Linux-only", code="PROVIDER_UNAVAILABLE")
        if self._stub is not None:
            return self._stub
        try:
            import grpc
            from tga.sandbox.api.sandbox.v1 import sandbox_pb2_grpc
        except ImportError as exc:
            raise SandboxError("sandboxd gRPC dependencies are unavailable", code="PROVIDER_UNAVAILABLE") from exc
        target = f"unix://{self.config.sandboxd.socket_path}"
        self._stub = sandbox_pb2_grpc.SandboxServiceStub(grpc.insecure_channel(target))
        return self._stub

    def acquire(
        self,
        *,
        task_id: str,
        solver_id: str,
        solver_run_id: str,
        profile_id: str,
        fencing_token: int,
        idempotency_key: str,
        profile=None,
    ) -> SandboxHandle:
        profile = profile or self.config.profile(profile_id)
        if profile.provider != self.provider_name:
            raise SandboxError("profile does not belong to sandboxd", code="PROFILE_PROVIDER_MISMATCH")
        self.health()
        try:
            from tga.sandbox.api.sandbox.v1 import sandbox_pb2

            response = self._client().Acquire(
                sandbox_pb2.AcquireRequest(
                    task_id=task_id,
                    solver_id=solver_id,
                    solver_run_id=solver_run_id,
                    profile_id=profile_id,
                    config_digest=self.config.digest,
                    fencing_token=fencing_token,
                    idempotency_key=idempotency_key,
                ),
                timeout=self.config.sandboxd.rpc_timeout_seconds,
            )
        except SandboxError:
            raise
        except Exception as exc:
            raise SandboxError(f"sandboxd acquire failed: {exc}", code="PROVIDER_FAILED", retryable=True) from exc
        return SandboxHandle(
            instance_id=response.instance_id,
            task_id=task_id,
            solver_id=solver_id,
            solver_run_id=solver_run_id,
            profile_id=profile_id,
            provider=self.provider_name,
            config_digest=response.config_digest,
            image_digest=response.image_digest,
            toolset_digest=response.toolset_digest or None,
            fencing_token=response.fencing_token,
            state=SandboxState.READY,
        )

    def health(self) -> None:
        if self._health_checked:
            return
        from tga.sandbox.api.sandbox.v1 import sandbox_pb2

        try:
            response = self._client().Health(
                sandbox_pb2.HealthRequest(
                    protocol_major=self.config.sandboxd.protocol_major,
                    config_digest=self.config.digest,
                ),
                timeout=self.config.sandboxd.rpc_timeout_seconds,
            )
        except Exception as exc:
            raise SandboxError(f"sandboxd health failed: {exc}", code="PROVIDER_UNAVAILABLE") from exc
        if response.protocol_major != self.config.sandboxd.protocol_major:
            raise SandboxError("sandboxd protocol is incompatible", code="PROVIDER_VERSION_INCOMPATIBLE")
        if response.config_digest != self.config.digest:
            raise SandboxError("sandboxd configuration digest differs", code="CONFIG_DIGEST_MISMATCH")
        if not all(
            (
                response.docker_available,
                response.runsc_available,
                response.nftables_available,
                response.cgroup_v2_available,
                response.client_uid_policy_active,
            )
        ):
            raise SandboxError("sandboxd host capabilities are incomplete", code="PROVIDER_UNAVAILABLE")
        self._health_checked = True

    def exec(self, handle: SandboxHandle, spec: ProcessSpec, *, profile=None) -> tuple[Iterator[ExecFrame], ExecResult]:
        from tga.sandbox.api.sandbox.v1 import sandbox_pb2

        events = self._client().Exec(
            sandbox_pb2.ExecRequest(
                instance_id=handle.instance_id,
                fencing_token=handle.fencing_token,
                process=_process_message(spec),
                idempotency_key=f"exec:{handle.instance_id}:{time.time_ns()}",
                solver_id=handle.solver_id,
            )
        )
        frames: list[ExecFrame] = []
        result: ExecResult | None = None
        try:
            for event in events:
                kind = event.WhichOneof("event")
                if kind == "frame":
                    frames.append(_frame(event.frame))
                elif kind == "result":
                    has_code = event.result.HasField("exit_code")
                    result = ExecResult(
                        exit_code=event.result.exit_code if has_code else None,
                        signal=event.result.signal or None,
                        timed_out=event.result.timed_out,
                        truncated=event.result.truncated,
                        stdout=b"".join(item.data for item in frames if item.stream == "stdout"),
                        stderr=b"".join(item.data for item in frames if item.stream == "stderr"),
                    )
        except Exception as exc:
            raise SandboxError(f"sandboxd exec failed: {exc}", code="PROVIDER_FAILED", retryable=True) from exc
        if result is None:
            raise SandboxError("sandboxd closed Exec without a result", code="INVALID_PROVIDER_RESPONSE")
        return iter(frames), result

    def open_process(self, handle: SandboxHandle, spec: ProcessSpec, *, profile=None) -> SandboxProcess:
        process = _GrpcProcess(
            self._client(),
            handle=handle,
            spec=spec,
            idempotency_key=f"process:{handle.instance_id}:{time.time_ns()}",
        )
        return process

    def stop_process(self, process_id: str, *, fencing_token: int) -> None:
        from tga.sandbox.api.sandbox.v1 import sandbox_pb2

        self._client().StopProcess(
            sandbox_pb2.StopProcessRequest(process_id=process_id, fencing_token=fencing_token),
            timeout=self.config.sandboxd.rpc_timeout_seconds,
        )

    def inspect(self, handle: SandboxHandle) -> SandboxInspection:
        from tga.sandbox.api.sandbox.v1 import sandbox_pb2

        response = self._client().Inspect(
            sandbox_pb2.InspectRequest(
                instance_id=handle.instance_id,
                fencing_token=handle.fencing_token,
            ),
            timeout=self.config.sandboxd.rpc_timeout_seconds,
        )
        return SandboxInspection(
            handle=handle,
            runtime=response.runtime,
            active_processes=response.active_processes,
            created_at=response.created_at,
        )

    def release(self, handle: SandboxHandle) -> None:
        # Desired-state retention is owned by Python; no privileged action.
        return None

    def destroy(self, handle: SandboxHandle) -> None:
        from tga.sandbox.api.sandbox.v1 import sandbox_pb2

        self._client().Destroy(
            sandbox_pb2.DestroyRequest(
                instance_id=handle.instance_id,
                fencing_token=handle.fencing_token,
                task_id=handle.task_id,
                solver_run_id=handle.solver_run_id,
            ),
            timeout=self.config.sandboxd.rpc_timeout_seconds,
        )

    def reconcile(
        self,
        valid_instance_ids: tuple[str, ...],
        *,
        grace_before: datetime,
    ) -> tuple[str, ...]:
        from tga.sandbox.api.sandbox.v1 import sandbox_pb2

        response = self._client().Reconcile(
            sandbox_pb2.ReconcileRequest(
                valid_instance_ids=valid_instance_ids,
                grace_before_unix_ms=int(grace_before.timestamp() * 1000),
            ),
            timeout=max(self.config.sandboxd.rpc_timeout_seconds, 60),
        )
        if response.errors:
            raise SandboxError(
                "sandboxd reconcile partially failed: " + "; ".join(response.errors),
                code="RECONCILE_PARTIAL_FAILURE",
                retryable=True,
            )
        return tuple(response.destroyed_instance_ids)


def _process_message(spec: ProcessSpec):
    from tga.sandbox.api.sandbox.v1 import sandbox_pb2

    return sandbox_pb2.ProcessSpec(
        argv=spec.argv,
        environment=spec.environment,
        logical_workspace=spec.logical_workspace,
        working_directory=spec.working_directory,
        stdin=spec.stdin or b"",
        interactive=spec.interactive,
        timeout_seconds=spec.timeout_seconds or 0,
        network_grants=[
            sandbox_pb2.NetworkGrant(cidr=grant.cidr, ports=grant.ports)
            for grant in spec.network_grants
        ],
        tool_id=spec.tool_id or "",
    )


def _frame(value) -> ExecFrame:
    return ExecFrame(
        sequence=value.sequence,
        timestamp_unix_ms=value.timestamp_unix_ms,
        stream="stderr" if value.stream == value.STDERR else "stdout",
        data=value.data,
    )


class _GrpcProcess(SandboxProcess):
    def __init__(self, stub, *, handle: SandboxHandle, spec: ProcessSpec, idempotency_key: str):
        from tga.sandbox.api.sandbox.v1 import sandbox_pb2

        self.process_id = ""
        self._requests: queue.Queue[object | None] = queue.Queue(maxsize=32)
        self._frames: queue.Queue[ExecFrame | None] = queue.Queue(maxsize=128)
        self._result: ExecResult | None = None
        self._error: Exception | None = None
        self._done = threading.Event()
        self._opened = threading.Event()
        self._stub = stub
        self._fencing_token = handle.fencing_token
        self._closed = False
        self._requests.put(
            sandbox_pb2.ProcessMessage(
                start=sandbox_pb2.ProcessStart(
                    instance_id=handle.instance_id,
                    fencing_token=handle.fencing_token,
                    process=_process_message(spec),
                    idempotency_key=idempotency_key,
                    solver_id=handle.solver_id,
                )
            )
        )
        responses = stub.OpenProcess(self._request_iterator())

        def read() -> None:
            try:
                for response in responses:
                    kind = response.WhichOneof("message")
                    if kind == "opened":
                        self.process_id = response.opened.process_id
                        self._opened.set()
                    elif kind == "frame":
                        self._frames.put(_frame(response.frame))
                    elif kind == "result":
                        has_code = response.result.HasField("exit_code")
                        self._result = ExecResult(
                            exit_code=response.result.exit_code if has_code else None,
                            signal=response.result.signal or None,
                            timed_out=response.result.timed_out,
                            truncated=response.result.truncated,
                        )
            except Exception as exc:
                self._error = exc
            finally:
                self._frames.put(None)
                self._done.set()

        threading.Thread(target=read, daemon=True).start()
        if not self._opened.wait(10):
            self.close()
            raise SandboxError("sandboxd did not open the process", code="PROVIDER_TIMEOUT", retryable=True)

    def _request_iterator(self):
        while True:
            value = self._requests.get()
            if value is None:
                return
            yield value

    def send(self, data: bytes) -> None:
        from tga.sandbox.api.sandbox.v1 import sandbox_pb2

        if self._done.is_set():
            raise SandboxError("sandbox process is not running", code="PROCESS_NOT_RUNNING")
        self._requests.put(sandbox_pb2.ProcessMessage(input=sandbox_pb2.ProcessInput(data=data)))

    def receive(self, timeout: float) -> ExecFrame:
        try:
            value = self._frames.get(timeout=max(timeout, 0.001))
        except queue.Empty as exc:
            raise TimeoutError("timed out waiting for sandbox process output") from exc
        if value is None:
            if self._error:
                raise SandboxError(f"sandbox process stream failed: {self._error}", code="PROVIDER_FAILED")
            raise SandboxError("sandbox process stream closed", code="PROCESS_STREAM_CLOSED")
        return value

    def resize(self, cols: int, rows: int) -> None:
        from tga.sandbox.api.sandbox.v1 import sandbox_pb2

        if self._done.is_set():
            raise SandboxError("sandbox process is not running", code="PROCESS_NOT_RUNNING")
        self._requests.put(
            sandbox_pb2.ProcessMessage(
                resize=sandbox_pb2.ProcessResize(cols=cols, rows=rows)
            )
        )

    def close_stdin(self) -> None:
        from tga.sandbox.api.sandbox.v1 import sandbox_pb2

        if not self._done.is_set():
            self._requests.put(
                sandbox_pb2.ProcessMessage(input=sandbox_pb2.ProcessInput(close_stdin=True))
            )

    def wait(self, timeout: float | None = None) -> ExecResult:
        if not self._done.wait(timeout):
            return ExecResult(timed_out=True)
        if self._error:
            raise SandboxError(f"sandbox process failed: {self._error}", code="PROVIDER_FAILED")
        return self._result or ExecResult()

    def close(self) -> None:
        from tga.sandbox.api.sandbox.v1 import sandbox_pb2

        if self._closed:
            return
        self._closed = True
        self.close_stdin()
        if self.process_id and not self._done.is_set():
            try:
                self._stub.StopProcess(
                    sandbox_pb2.StopProcessRequest(
                        process_id=self.process_id,
                        fencing_token=self._fencing_token,
                    ),
                    timeout=5,
                )
            except Exception:
                pass
        self._requests.put(None)
