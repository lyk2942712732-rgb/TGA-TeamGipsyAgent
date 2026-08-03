"""Execution backends selected only from the governed capability catalog."""

from __future__ import annotations

import ipaddress
from pathlib import PurePosixPath, PureWindowsPath
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any, Callable, Protocol
from tga.network_policy import authorize_url, enforce_address_policy
from tga.domain.kali import KaliExecArguments
from tga.runtime.kali import KaliSessionManager
from tga.runtime.tooling.execution.models import (
    AuthorizedExecutionRequest,
    ExecutionBackendKind,
    ExecutionResult,
    ProducedFile,
)
from tga.runtime.tooling.results import ExecutionError
from tga.sandbox.models import NetworkGrant, ProcessSpec
from tga.sandbox.provider import SandboxError


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class ExecutionBackend(Protocol):
    kind: ExecutionBackendKind

    def execute(self, request: AuthorizedExecutionRequest) -> ExecutionResult: ...


class ExecutionBackendRouter:
    def __init__(
        self,
        backends: dict[ExecutionBackendKind, ExecutionBackend],
        *,
        artifact_ingestion=None,
    ) -> None:
        self.backends = backends
        self.artifact_ingestion = artifact_ingestion

    def execute(self, request: AuthorizedExecutionRequest) -> ExecutionResult:
        backend = self.backends.get(request.backend)
        if backend is None:
            return _failure(
                request,
                "EXECUTION_BACKEND_UNAVAILABLE",
                f"No backend is configured for {request.backend}.",
            )
        result = backend.execute(request)
        if self.artifact_ingestion is not None:
            result = self.artifact_ingestion.ingest(request, result)
        return result


class HandlerExecutionBackend:
    """Execute Host control/resource and remote MCP handlers."""

    def __init__(
        self,
        kind: ExecutionBackendKind,
        execute: Callable[[AuthorizedExecutionRequest], Any],
    ) -> None:
        self.kind = kind
        self._execute = execute

    def execute(self, request: AuthorizedExecutionRequest) -> ExecutionResult:
        started = _now()
        value = self._execute(request)
        if isinstance(value, ExecutionResult):
            return value
        payload = dict(value or {})
        error_payload = payload.get("error") if isinstance(payload.get("error"), dict) else None
        status = str(payload.get("status") or ("succeeded" if payload.get("ok") else "blocked"))
        if status not in {
            "pending_approval", "succeeded", "failed", "blocked", "rejected",
            "expired", "cancelled",
        }:
            status = "succeeded" if payload.get("ok") else "blocked"
        return ExecutionResult(
            action_id=request.action_id,
            status=status,
            structured_result=payload,
            artifact_ids=[str(item) for item in payload.get("artifact_ids") or ()],
            execution_metadata={"backend": self.kind},
            started_at=started,
            finished_at=_now(),
            error=ExecutionError.model_validate(error_payload) if error_payload else None,
        )


class HostRetrievalBackend:
    kind: ExecutionBackendKind = "host_retrieval"

    def __init__(
        self,
        *,
        workspace: str | Path,
        store,
        artifact_service,
        delegated: Callable[[AuthorizedExecutionRequest], Any],
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self.store = store
        self.artifact_service = artifact_service
        self.delegated = delegated

    def execute(self, request: AuthorizedExecutionRequest) -> ExecutionResult:
        started = _now()
        if request.capability == "artifact.inspect":
            return self._artifact_inspect(request, started)
        return HandlerExecutionBackend(self.kind, self.delegated).execute(request)

    def _artifact_inspect(
        self, request: AuthorizedExecutionRequest, started: str
    ) -> ExecutionResult:
        artifact_id = str(request.arguments["artifact_id"])
        artifact = self.store.get_artifact(artifact_id)
        if artifact is None or artifact.task_id != request.task_id:
            return _failure(
                request, "ARTIFACT_NOT_FOUND", "Artifact does not exist", started_at=started
            )
        limit = min(int(request.arguments.get("limit") or 16_384), 262_144)
        excerpt = self.artifact_service.excerpt(artifact, limit=limit)
        query = str(request.arguments.get("query") or "").casefold()
        if query and query not in excerpt.casefold():
            excerpt = ""
        return ExecutionResult(
            action_id=request.action_id,
            status="succeeded",
            started_at=started,
            finished_at=_now(),
            artifact_ids=[artifact.id],
            structured_result={
                "ok": True,
                "status": "succeeded",
                "summary": f"retrieved {artifact.id}",
                "artifact_ids": [artifact.id],
                "leads": [excerpt] if excerpt else [],
            },
            execution_metadata={"backend": self.kind},
        )

    @staticmethod
    def _relative_path(value: str) -> str:
        normalized = value.replace("\\", "/")
        path = PurePosixPath(normalized)
        if (
            not normalized
            or path.is_absolute()
            or PureWindowsPath(normalized).is_absolute()
            or ".." in path.parts
        ):
            raise ValueError("workspace path escapes its owner")
        return normalized


class KaliSandboxBackend:
    kind: ExecutionBackendKind = "sandbox"

    def __init__(
        self,
        *,
        manager,
        task,
        workspace: str | Path,
        sessions: KaliSessionManager | None = None,
    ) -> None:
        self.manager = manager
        self.task = task
        self.workspace = Path(workspace).resolve()
        self.sessions = sessions or KaliSessionManager(manager)

    def execute(self, request: AuthorizedExecutionRequest) -> ExecutionResult:
        started = _now()
        started_clock = perf_counter()
        early = self._preflight(request, started)
        if early is not None:
            return early
        try:
            assert request.execution_profile_id is not None
            assert request.solver_run_id is not None
            assert request.fencing_token is not None
            profile = self.manager.config.profile(request.execution_profile_id)
            if request.capability not in {"kali.exec", "kali.session"}:
                raise SandboxError(
                    f"unsupported Kali capability: {request.capability}",
                    code="KALI_CAPABILITY_NOT_SUPPORTED",
                )
            if request.capability not in profile.supported_capabilities:
                raise SandboxError(
                    f"profile {profile.id} does not support {request.capability}",
                    code="KALI_CAPABILITY_NOT_SUPPORTED",
                )
            handle = self.manager.acquire(
                task_id=request.task_id,
                solver_id=request.solver_id,
                solver_run_id=request.solver_run_id,
                profile_id=profile.id,
                fencing_token=request.fencing_token,
                idempotency_key=request.idempotency_key,
            )
            if request.capability == "kali.session":
                payload = self.sessions.execute(
                    request=request,
                    handle=handle,
                    profile=profile,
                    workspace=self.workspace,
                )
                return ExecutionResult(
                    action_id=request.action_id,
                    status="succeeded",
                    started_at=started,
                    finished_at=_now(),
                    structured_result={"ok": True, "status": "succeeded", **payload},
                    execution_metadata=self._metadata(handle),
                )

            arguments = KaliExecArguments.model_validate(request.arguments)
            if arguments.executable not in profile.allowed_executables:
                raise SandboxError(
                    f"executable {arguments.executable!r} is not allowed by profile {profile.id}",
                    code="EXECUTABLE_NOT_ALLOWED",
                )
            cwd = self._relative_workspace_path(arguments.cwd)
            self._validate_argv(arguments.argv)
            timeout = min(
                arguments.timeout_seconds or profile.limits.timeout_seconds,
                profile.limits.timeout_seconds,
            )
            spec = ProcessSpec(
                argv=(arguments.executable, *arguments.argv),
                tool_id="kali.exec",
                environment=arguments.env,
                working_directory=cwd,
                stdin=(
                    arguments.stdin.encode("utf-8")
                    if arguments.stdin is not None
                    else None
                ),
                timeout_seconds=timeout,
                network_grants=self._declared_network_grants(request),
            )
            before = self._output_snapshot()
            frames, raw = self.manager.exec(handle, spec)
            values = tuple(frames)
            stdout = raw.stdout or b"".join(
                item.data for item in values if item.stream == "stdout"
            )
            stderr = raw.stderr or b"".join(
                item.data for item in values if item.stream == "stderr"
            )
            produced = tuple(
                ProducedFile(path=str(path), kind="file")
                for path in self._new_outputs(before)
            )
            status = "failed" if raw.timed_out or raw.exit_code not in {0, None} else "succeeded"
            error = None
            if raw.timed_out:
                error = ExecutionError(
                    code="ACTION_TIMEOUT",
                    message="Kali sandbox execution timed out",
                    retryable=True,
                )
            elif raw.exit_code not in {0, None}:
                error = ExecutionError(
                    code="SANDBOX_PROCESS_FAILED",
                    message=f"Kali process exited with code {raw.exit_code}",
                    retryable=False,
                )
            return ExecutionResult(
                action_id=request.action_id,
                status=status,
                exit_code=raw.exit_code,
                stdout_preview=stdout.decode("utf-8", errors="replace"),
                stderr_preview=stderr.decode("utf-8", errors="replace"),
                started_at=started,
                finished_at=_now(),
                resource_usage={
                    "duration_ms": max(
                        0, int((perf_counter() - started_clock) * 1000)
                    )
                },
                produced_files=produced,
                structured_result={
                    "summary": (
                        f"{arguments.executable} exited with code {raw.exit_code}"
                    ),
                    "truncated": raw.truncated,
                },
                execution_metadata={
                    **self._metadata(handle),
                    "timed_out": raw.timed_out,
                    "truncated": raw.truncated,
                },
                error=error,
            )
        except (SandboxError, PermissionError, ValueError, OSError) as exc:
            return _failure(
                request,
                getattr(exc, "code", "SANDBOX_EXECUTION_FAILED"),
                str(exc)[:800],
                retryable=bool(getattr(exc, "retryable", False)),
                started_at=started,
            )

    def _preflight(
        self, request: AuthorizedExecutionRequest, started: str
    ) -> ExecutionResult | None:
        checks = (
            (
                self.manager.config.runtime != "enforced",
                "SANDBOX_RUNTIME_DISABLED",
                "Kali execution requires an enforced sandbox runtime.",
            ),
            (
                request.sandbox_config_digest != self.manager.config.digest,
                "SANDBOX_CONFIG_DIGEST_MISMATCH",
                "The request was frozen against another sandbox configuration.",
            ),
            (
                not request.execution_profile_id,
                "KALI_PROFILE_NOT_ASSIGNED",
                "The Solver has no Kali Profile assigned.",
            ),
            (
                not request.solver_run_id,
                "SOLVER_RUN_ID_REQUIRED",
                "Kali execution requires a durable SolverRun identity.",
            ),
            (
                request.fencing_token is None,
                "FENCING_TOKEN_REQUIRED",
                "Kali execution requires the current SolverRun fencing token.",
            ),
        )
        for blocked, code, message in checks:
            if blocked:
                return _failure(
                    request,
                    code,
                    message,
                    retryable=False,
                    started_at=started,
                )
        return None

    def _declared_network_grants(
        self, request: AuthorizedExecutionRequest
    ) -> tuple[NetworkGrant, ...]:
        values = request.arguments.get("network_targets") or ()
        if not values:
            return ()
        profile = self.manager.config.profile(str(request.execution_profile_id))
        if profile.provider != "sandboxd" or profile.network_mode != "target_allowlist":
            raise SandboxError(
                "kali.exec network targets require a target_allowlist Profile",
                code="NETWORK_POLICY_UNSUPPORTED",
            )
        grants: list[NetworkGrant] = []
        for value in values:
            host = str(value.get("host") or "")
            ports = tuple(int(item) for item in value.get("ports") or ())
            if not ports:
                raise ValueError("network target requires at least one port")
            try:
                addresses = [ipaddress.ip_address(host)]
            except ValueError:
                resolved = None
                for scheme in ("https", "http"):
                    try:
                        resolved = authorize_url(
                            f"{scheme}://{host}", self.task.execution_policy.network
                        )
                        break
                    except (PermissionError, ValueError):
                        continue
                if resolved is None:
                    raise PermissionError(
                        "network target is outside the task allowlist"
                    )
                addresses = [ipaddress.ip_address(item) for item in resolved]
            for address in addresses:
                enforce_address_policy(address, self.task.execution_policy.network)
                grants.append(
                    NetworkGrant(
                        cidr=f"{address}/{32 if address.version == 4 else 128}",
                        ports=ports,
                    )
                )
        return tuple(grants)

    def _relative_workspace_path(self, value: str) -> str:
        normalized = value.replace("\\", "/").strip("/") or "."
        path = PurePosixPath(normalized)
        if (
            PureWindowsPath(value).is_absolute()
            or path.is_absolute()
            or ".." in path.parts
        ):
            raise ValueError("Kali working directory must remain relative")
        resolved = (self.workspace / Path(*path.parts)).resolve()
        try:
            resolved.relative_to(self.workspace)
        except ValueError as exc:
            raise ValueError(
                "Kali working directory escapes the SolverRun workspace"
            ) from exc
        resolved.mkdir(parents=True, exist_ok=True)
        return normalized

    @staticmethod
    def _validate_argv(values: tuple[str, ...]) -> None:
        for value in values:
            if "\x00" in value:
                raise ValueError("Kali argv may not contain NUL")
            if value.startswith("-") or "://" in value:
                continue
            normalized = value.replace("\\", "/")
            path = PurePosixPath(normalized)
            if (
                PureWindowsPath(value).is_absolute()
                or path.is_absolute()
                or ".." in path.parts
            ):
                raise ValueError(
                    "Kali argv paths must remain relative to the SolverRun workspace"
                )

    def _output_snapshot(self) -> dict[Path, tuple[int, int]]:
        root = self.workspace / "outputs"
        root.mkdir(parents=True, exist_ok=True)
        return {
            item.resolve(): (item.stat().st_mtime_ns, item.stat().st_size)
            for item in root.rglob("*")
            if item.is_file()
        }

    def _new_outputs(self, before: dict[Path, tuple[int, int]]) -> tuple[Path, ...]:
        root = self.workspace / "outputs"
        values = []
        for item in root.rglob("*"):
            if not item.is_file():
                continue
            resolved = item.resolve()
            state = (item.stat().st_mtime_ns, item.stat().st_size)
            if before.get(resolved) != state:
                values.append(resolved)
        return tuple(sorted(values))

    @staticmethod
    def _metadata(handle) -> dict[str, Any]:
        return {
            "backend": "sandbox",
            "sandbox_instance_id": handle.instance_id,
            "kali_profile_id": handle.profile_id,
            "sandbox_provider": handle.provider,
            "sandbox_config_digest": handle.config_digest,
            "solver_run_id": handle.solver_run_id,
            "image_digest": handle.image_digest,
            "toolset_digest": handle.toolset_digest,
            "fencing_token": handle.fencing_token,
        }

class RemoteMCPBackend:
    kind: ExecutionBackendKind = "remote_mcp"

    def __init__(self, *, manager, task, workspace: str | Path) -> None:
        self.manager = manager
        self.task = task
        self.workspace = Path(workspace)

    def execute(self, request: AuthorizedExecutionRequest) -> ExecutionResult:
        started = _now()
        provider_name = str(request.execution_metadata.get("provider_tool_name") or "")
        snapshot = self.manager.snapshot_for_task(self.task, workspace=self.workspace)
        route = snapshot.route(provider_name)
        expected_server = str(request.execution_metadata.get("mcp_server_id") or "")
        expected_method = str(request.execution_metadata.get("mcp_method") or "")
        expected_catalog = str(request.execution_metadata.get("mcp_catalog_version") or "")
        if (
            route is None
            or route.server_id != expected_server
            or route.method != expected_method
            or snapshot.version != expected_catalog
        ):
            return _failure(
                request,
                "MCP_ROUTE_STALE",
                "The authorized Remote MCP route changed before execution.",
                started_at=started,
            )
        outcome = self.manager.call_authorized_tool(
            context=self.task,
            route=route,
            arguments=request.arguments,
            catalog_version=snapshot.version,
            workspace=self.workspace,
            trace_id=f"trace_{request.action_id}",
        )
        error = None
        if outcome.error is not None:
            error = ExecutionError(
                code=outcome.error.code,
                message=outcome.error.message,
                retryable=outcome.error.retryable,
            )
        return ExecutionResult(
            action_id=request.action_id,
            status="succeeded" if outcome.ok else "failed",
            exit_code=outcome.returncode,
            stdout_preview=outcome.stdout,
            stderr_preview=outcome.stderr,
            started_at=started,
            finished_at=_now(),
            resource_usage={"duration_ms": outcome.timings.get("total_ms", 0)},
            structured_result={
                "summary": (
                    f"Remote MCP {route.server_id}.{route.method} returned "
                    f"{len(outcome.content)} content block(s)"
                ),
                "server": route.server_id,
                "method": route.method,
                "content": outcome.content,
                "structured_content": outcome.structured_content,
                "is_error": outcome.is_error,
                "truncated": outcome.artifact_truncated or outcome.output_truncated,
            },
            execution_metadata={
                "backend": "remote_mcp",
                "trace_id": outcome.trace_id,
                "request_id": outcome.request_id,
                "catalog_version": outcome.catalog_version,
                "server": route.server_id,
                "method": route.method,
                "protocol_version": outcome.protocol_version,
            },
            error=error,
        )


def _failure(
    request: AuthorizedExecutionRequest,
    code: str,
    message: str,
    *,
    retryable: bool = False,
    started_at: str | None = None,
) -> ExecutionResult:
    error = ExecutionError(code=code, message=message, retryable=retryable)
    return ExecutionResult(
        action_id=request.action_id,
        status="blocked" if code in {"SANDBOX_RUNTIME_DISABLED", "EXECUTION_BACKEND_UNAVAILABLE"} else "failed",
        started_at=started_at or _now(),
        finished_at=_now(),
        structured_result={
            "ok": False,
            "status": "blocked" if code in {"SANDBOX_RUNTIME_DISABLED", "EXECUTION_BACKEND_UNAVAILABLE"} else "failed",
            "error": error.model_dump(mode="json"),
        },
        execution_metadata={"backend": request.backend},
        error=error,
    )


__all__ = [
    "ExecutionBackend", "ExecutionBackendRouter", "HandlerExecutionBackend",
    "HostRetrievalBackend", "KaliSandboxBackend", "RemoteMCPBackend",
]
