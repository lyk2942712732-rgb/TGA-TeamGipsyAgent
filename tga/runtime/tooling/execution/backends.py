"""Execution backends selected only from the governed capability catalog."""

from __future__ import annotations

import base64
import ipaddress
import json
import re
from pathlib import PurePosixPath, PureWindowsPath
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any, Callable, Protocol
from urllib.parse import urlsplit

from tga.network_policy import authorize_url, enforce_address_policy
from tga.runtime.tooling.execution.adapters import process_spec
from tga.runtime.tooling.execution.models import (
    AuthorizedExecutionRequest,
    ExecutionBackendKind,
    ExecutionResult,
    ProducedFile,
)
from tga.runtime.tooling.results import ExecutionError
from tga.sandbox.models import NetworkGrant
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
    """Compatibility bridge for host control/resource and remote MCP handlers."""

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
        if request.capability == "workspace.read":
            return self._workspace_read(request, started)
        if request.capability == "artifact.inspect":
            return self._artifact_inspect(request, started)
        return HandlerExecutionBackend(self.kind, self.delegated).execute(request)

    def _workspace_read(
        self, request: AuthorizedExecutionRequest, started: str
    ) -> ExecutionResult:
        relative = self._relative_path(str(request.arguments["relative_path"]))
        path = (self.workspace / relative).resolve()
        try:
            path.relative_to(self.workspace)
            size = path.stat().st_size
            offset = int(request.arguments.get("offset") or 0)
            limit = min(int(request.arguments.get("limit") or 16_384), 262_144)
            with path.open("rb") as source:
                source.seek(offset)
                raw = source.read(limit)
        except (OSError, ValueError) as exc:
            return _failure(
                request, "WORKSPACE_READ_FAILED", str(exc)[:800], started_at=started
            )
        text = raw.decode("utf-8", errors="replace")
        return ExecutionResult(
            action_id=request.action_id,
            status="succeeded",
            started_at=started,
            finished_at=_now(),
            structured_result={
                "ok": True,
                "status": "succeeded",
                "summary": f"read {relative} ({size} bytes)",
                "relative_path": relative,
                "offset": offset,
                "size": size,
                "excerpt": text,
                "truncated": offset + len(raw) < size,
                "facts": [f"workspace file observed: {relative}"],
            },
            execution_metadata={"backend": self.kind},
        )

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
    ) -> None:
        self.manager = manager
        self.task = task
        self.workspace = Path(workspace).resolve()

    def execute(self, request: AuthorizedExecutionRequest) -> ExecutionResult:
        started = _now()
        started_clock = perf_counter()
        if self.manager.config.runtime != "enforced":
            return _failure(
                request,
                "SANDBOX_RUNTIME_DISABLED",
                "Local execution is disabled because the Kali sandbox runtime is not enforced.",
                retryable=False,
                started_at=started,
            )
        if request.sandbox_config_digest != self.manager.config.digest:
            return _failure(
                request,
                "SANDBOX_CONFIG_DIGEST_MISMATCH",
                "The authorized request was frozen against a different sandbox configuration.",
                started_at=started,
            )
        if not request.execution_profile_id:
            return _failure(
                request,
                "SANDBOX_PROFILE_NOT_ASSIGNED",
                "The SolverDefinition has no sandbox profile assigned.",
                started_at=started,
            )
        if not request.solver_run_id:
            return _failure(
                request,
                "SOLVER_RUN_ID_REQUIRED",
                "Sandbox execution requires a durable SolverRun identity.",
                started_at=started,
            )
        try:
            profile_id = request.execution_profile_id
            profile = self.manager.config.profile(profile_id)
            if request.capability == "sandbox.exec":
                executable = str(request.arguments.get("executable") or "")
                if executable not in profile.allowed_executables:
                    raise SandboxError(
                        f"executable {executable!r} is not allowed by profile {profile_id}",
                        code="EXECUTABLE_NOT_ALLOWED",
                    )
            handle = self.manager.acquire(
                task_id=request.task_id,
                solver_id=request.solver_id,
                solver_run_id=request.solver_run_id,
                profile_id=profile_id,
                fencing_token=request.fencing_token or 1,
                idempotency_key=request.idempotency_key,
            )
            timeout = profile.limits.timeout_seconds
            self._validate_workspace_paths(request)
            spec = process_spec(request.capability, request.arguments, timeout)
            if request.capability in {
                "http.request", "nmap.scan", "ffuf.directory_scan", "nuclei.scan",
            }:
                network_grants = self._network_grants(request)
                if request.capability == "http.request":
                    approved_addresses = [
                        str(ipaddress.ip_network(grant.cidr).network_address)
                        for grant in network_grants
                    ]
                    spec = process_spec(
                        request.capability,
                        {**request.arguments, "_approved_addresses": approved_addresses},
                        timeout,
                    )
                spec = spec.model_copy(update={
                    "network_grants": network_grants,
                })
            elif request.capability == "sandbox.exec":
                spec = spec.model_copy(update={
                    "network_grants": self._declared_network_grants(request),
                })
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
            structured = self._structured_result(request.capability, stdout)
            status = "failed" if raw.timed_out or (raw.exit_code not in {0, None}) else "succeeded"
            error = None
            if raw.timed_out:
                error = ExecutionError(
                    code="ACTION_TIMEOUT", message="Kali sandbox execution timed out", retryable=True
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
                resource_usage={"duration_ms": max(0, int((perf_counter() - started_clock) * 1000))},
                produced_files=produced,
                structured_result=structured,
                execution_metadata={
                    "backend": "sandbox",
                    "sandbox_instance_id": handle.instance_id,
                    "sandbox_profile_id": handle.profile_id,
                    "sandbox_provider": handle.provider,
                    "sandbox_config_digest": handle.config_digest,
                    "solver_run_id": handle.solver_run_id,
                    "image_digest": handle.image_digest,
                    "toolset_digest": handle.toolset_digest,
                    "fencing_token": handle.fencing_token,
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

    def _network_grants(
        self, request: AuthorizedExecutionRequest
    ) -> tuple[NetworkGrant, ...]:
        profile_id = request.execution_profile_id
        if not profile_id:
            raise SandboxError(
                "sandbox profile is not assigned", code="SANDBOX_PROFILE_NOT_ASSIGNED"
            )
        profile = self.manager.config.profile(profile_id)
        if profile.provider != "sandboxd" or profile.network_mode != "target_allowlist":
            raise SandboxError(
                "network execution requires sandboxd target_allowlist enforcement",
                code="NETWORK_POLICY_UNSUPPORTED",
            )
        policy = self.task.execution_policy.network
        target = request.resolved_target or ""
        if request.capability == "nmap.scan":
            address = ipaddress.ip_address(str(request.arguments["target"]))
            enforce_address_policy(address, policy)
            addresses = [address]
            ports = self._nmap_ports(str(request.arguments.get("ports") or "1-1000"))
        else:
            addresses = [ipaddress.ip_address(item) for item in authorize_url(target, policy)]
            parsed = urlsplit(target)
            ports = (parsed.port or (443 if parsed.scheme == "https" else 80),)
        return tuple(
            NetworkGrant(
                cidr=f"{address}/{32 if address.version == 4 else 128}",
                ports=ports,
            )
            for address in addresses
        )

    def _declared_network_grants(
        self, request: AuthorizedExecutionRequest
    ) -> tuple[NetworkGrant, ...]:
        values = request.arguments.get("network_targets") or ()
        if not values:
            return ()
        profile = self.manager.config.profile(str(request.execution_profile_id))
        if profile.provider != "sandboxd" or profile.network_mode != "target_allowlist":
            raise SandboxError(
                "sandbox.exec network targets require a target_allowlist profile",
                code="NETWORK_POLICY_UNSUPPORTED",
            )
        grants: list[NetworkGrant] = []
        for value in values:
            host = str(value.get("host") or "")
            ports = tuple(int(item) for item in value.get("ports") or ())
            try:
                addresses = [ipaddress.ip_address(host)]
            except ValueError:
                policy = self.task.execution_policy.network
                urls = [f"https://{host}", f"http://{host}"]
                resolved = None
                for url in urls:
                    try:
                        resolved = authorize_url(url, policy)
                        break
                    except (PermissionError, ValueError):
                        continue
                if resolved is None:
                    raise PermissionError("network target is outside the task allowlist")
                addresses = [ipaddress.ip_address(item) for item in resolved]
            for address in addresses:
                enforce_address_policy(address, self.task.execution_policy.network)
                grants.append(NetworkGrant(
                    cidr=f"{address}/{32 if address.version == 4 else 128}",
                    ports=ports,
                ))
        return tuple(grants)

    def _validate_workspace_paths(
        self, request: AuthorizedExecutionRequest
    ) -> None:
        fields = {
            "workspace.write": ("relative_path",),
            "workspace.python": ("script_path",),
            "ffuf.directory_scan": ("wordlist",),
            "binwalk.analyze": ("path",),
            "yara.scan": ("rules_path", "target_path"),
            "radare2.analyze": ("path",),
        }.get(request.capability, ())
        for field in fields:
            value = request.arguments.get(field)
            if value in {None, ""}:
                continue
            relative = PurePosixPath(str(value).replace("\\", "/"))
            if relative.is_absolute() or PureWindowsPath(str(value)).is_absolute():
                raise ValueError("workspace path must remain relative")
            resolved = (self.workspace / Path(*relative.parts)).resolve()
            try:
                resolved.relative_to(self.workspace)
            except ValueError as exc:
                raise ValueError(
                    "workspace path resolves outside the Solver workspace"
                ) from exc

    @staticmethod
    def _nmap_ports(value: str) -> tuple[int, ...]:
        ports: set[int] = set()
        for item in value.split(","):
            if re.fullmatch(r"[0-9]+", item):
                ports.add(int(item))
            elif re.fullmatch(r"[0-9]+-[0-9]+", item):
                start, end = (int(part) for part in item.split("-", 1))
                if end < start:
                    raise ValueError("invalid nmap port range")
                ports.update(range(start, end + 1))
            else:
                raise ValueError("invalid nmap ports")
            if len(ports) > 4096:
                raise ValueError("nmap network grant exceeds 4096 ports")
        if not ports or min(ports) < 1 or max(ports) > 65535:
            raise ValueError("invalid nmap ports")
        return tuple(sorted(ports))

    def _output_snapshot(self) -> dict[Path, tuple[int, int]]:
        root = self.workspace / "outputs"
        root.mkdir(parents=True, exist_ok=True)
        return {
            item.resolve(): (item.stat().st_mtime_ns, item.stat().st_size)
            for item in root.rglob("*") if item.is_file()
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
    def _structured_result(capability: str, stdout: bytes) -> dict[str, Any]:
        text = stdout.decode("utf-8", errors="replace").strip()
        if capability == "http.request" and text:
            try:
                payload = json.loads(text.splitlines()[-1])
            except json.JSONDecodeError:
                return {"summary": "HTTP request completed in Kali"}
            body = base64.b64decode(payload.pop("body_base64", "") or b"")
            payload["body_excerpt"] = body[:16_000].decode("utf-8", errors="replace")
            payload["body_bytes"] = len(body)
            payload["summary"] = f"HTTP {payload.get('status')} from {payload.get('final_url')}"
            return payload
        return {"summary": f"{capability} completed in Kali"}


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
            task=self.task,
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
