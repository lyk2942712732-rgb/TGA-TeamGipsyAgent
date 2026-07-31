"""Docker Sandboxes provider using a task VM and constrained inner containers."""

from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import subprocess
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

from tga.sandbox.config import SandboxConfig
from tga.sandbox.models import (
    ExecFrame,
    ExecResult,
    ProcessSpec,
    SandboxHandle,
    SandboxInspection,
    SandboxState,
    IDENTIFIER,
)
from tga.sandbox.provider import SandboxError, SandboxProcess


CommandRunner = Callable[..., subprocess.CompletedProcess[bytes]]
_SEMVER = re.compile(r"\b(\d+)\.(\d+)\.(\d+)\b")


def _safe_name(task_id: str) -> str:
    return f"tga-{hashlib.sha256(task_id.encode()).hexdigest()[:16]}"


def _version(value: str) -> tuple[int, int, int]:
    match = _SEMVER.search(value)
    if not match:
        raise SandboxError("could not parse sbx version", code="PROVIDER_VERSION_INVALID")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


class _SbxProcess(SandboxProcess):
    def __init__(
        self,
        process_id: str,
        process: subprocess.Popen[bytes],
        *,
        max_output_bytes: int,
        cleanup: Callable[[], None],
    ):
        self.process_id = process_id
        self._process = process
        self._max_output_bytes = max_output_bytes
        self._cleanup = cleanup
        self._frames: queue.Queue[ExecFrame | None] = queue.Queue(maxsize=128)
        self._sequence = 0
        self._bytes = 0
        self._truncated = False
        self._closed_streams = 0
        self._lock = threading.Lock()

    def start(self) -> None:
        for stream, pipe in (
            ("stdout", self._process.stdout),
            ("stderr", self._process.stderr),
        ):
            threading.Thread(
                target=self._read_stream,
                args=(stream, pipe),
                daemon=True,
            ).start()

    def _read_stream(self, stream: str, pipe) -> None:
        try:
            if pipe is None:
                return
            while chunk := pipe.read(65536):
                with self._lock:
                    remaining = max(0, self._max_output_bytes - self._bytes)
                    data = chunk[:remaining]
                    self._bytes += len(data)
                    self._truncated = self._truncated or len(data) != len(chunk)
                    if data:
                        self._sequence += 1
                        frame = ExecFrame(
                            sequence=self._sequence,
                            timestamp_unix_ms=int(time.time() * 1000),
                            stream=stream,
                            data=data,
                        )
                    else:
                        frame = None
                if frame is not None:
                    self._frames.put(frame)
        finally:
            cleanup = False
            with self._lock:
                self._closed_streams += 1
                if self._closed_streams == 2:
                    self._frames.put(None)
                    cleanup = True
            if cleanup:
                self._cleanup()

    def send(self, data: bytes) -> None:
        if self._process.stdin is None or self._process.poll() is not None:
            raise SandboxError("sandbox process is not running", code="PROCESS_NOT_RUNNING")
        self._process.stdin.write(data)
        self._process.stdin.flush()

    def receive(self, timeout: float) -> ExecFrame:
        try:
            value = self._frames.get(timeout=max(timeout, 0.001))
        except queue.Empty as exc:
            raise TimeoutError("timed out waiting for sandbox process output") from exc
        if value is None:
            raise SandboxError("sandbox process stream closed", code="PROCESS_STREAM_CLOSED")
        return value

    def close_stdin(self) -> None:
        if self._process.stdin and not self._process.stdin.closed:
            self._process.stdin.close()

    def wait(self, timeout: float | None = None) -> ExecResult:
        try:
            code = self._process.wait(timeout=timeout)
            return ExecResult(exit_code=code, truncated=self._truncated)
        except subprocess.TimeoutExpired:
            return ExecResult(timed_out=True, truncated=self._truncated)

    def close(self) -> None:
        self.close_stdin()
        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=5)
        self._cleanup()


class DockerSandboxProvider:
    provider_name = "docker_sandbox"

    def __init__(
        self,
        config: SandboxConfig,
        *,
        runner: CommandRunner = subprocess.run,
        popen: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
    ):
        self.config = config
        self._runner = runner
        self._popen = popen
        self._processes: dict[str, _SbxProcess] = {}
        self._version_checked = False

    def acquire(
        self,
        *,
        task_id: str,
        solver_id: str,
        profile_id: str,
        fencing_token: int,
        idempotency_key: str,
    ) -> SandboxHandle:
        del idempotency_key
        profile = self.config.profile(profile_id)
        if not IDENTIFIER.fullmatch(task_id) or not IDENTIFIER.fullmatch(solver_id):
            raise SandboxError("invalid task or solver id", code="INVALID_IDENTITY")
        if profile.provider != self.provider_name:
            raise SandboxError("profile does not belong to Docker Sandboxes", code="PROFILE_PROVIDER_MISMATCH")
        self._check_version()
        name = _safe_name(task_id)
        workspace = self._workspace(task_id)
        (workspace / "solvers" / solver_id).mkdir(parents=True, exist_ok=True)
        (workspace / "inputs").mkdir(exist_ok=True)
        (workspace / "shared").mkdir(exist_ok=True)
        existing = self._sandbox(name)
        if existing is not None:
            marker = self._read_marker(task_id)
            if (
                marker is None
                or marker.get("instance_id") != name
                or marker.get("task_id") != task_id
                or marker.get("config_digest") != self.config.digest
                or str(Path(str(marker.get("workspace") or "")).resolve()) != str(workspace)
            ):
                raise SandboxError(
                    "existing sandbox has no trusted host marker",
                    code="SANDBOX_IDENTITY_MISMATCH",
                )
            self._validate_existing(existing, workspace)
        else:
            self._run(
                [
                    self.config.docker_sandbox.executable,
                    "create",
                    "--name",
                    name,
                    "--template",
                    self.config.docker_sandbox.template,
                    "shell",
                    str(workspace),
                ],
                timeout=180,
            )
        sandbox_workspace = self._resolve_sandbox_workspace(name, workspace)
        self._write_marker(task_id, name, workspace, sandbox_workspace)
        if profile.network_mode == "public_http":
            self._apply_web_policy(name, profile.web_allow_hosts)
        return SandboxHandle(
            instance_id=name,
            task_id=task_id,
            solver_id=solver_id,
            profile_id=profile_id,
            provider=self.provider_name,
            config_digest=self.config.digest,
            fencing_token=fencing_token,
            state=SandboxState.READY,
        )

    def health(self) -> None:
        self._check_version()
        self._list_sandboxes()

    def exec(self, handle: SandboxHandle, spec: ProcessSpec) -> tuple[Iterator[ExecFrame], ExecResult]:
        self._validate(handle)
        profile = self.config.profile(handle.profile_id)
        timeout = min(
            spec.timeout_seconds or profile.limits.timeout_seconds,
            profile.limits.timeout_seconds,
        )
        inner_name = f"tga-p-{uuid.uuid4().hex[:16]}"
        command = self._inner_command(handle, spec, inner_name, interactive=False)
        try:
            completed = self._runner(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
                check=False,
            )
            timed_out = False
        except subprocess.TimeoutExpired as exc:
            self._remove_inner(handle.instance_id, inner_name)
            stdout = exc.stdout or b""
            stderr = exc.stderr or b""
            completed = subprocess.CompletedProcess(command, -1, stdout, stderr)
            timed_out = True
        except FileNotFoundError as exc:
            raise SandboxError("sbx executable is unavailable", code="PROVIDER_UNAVAILABLE") from exc
        stdout = completed.stdout or b""
        stderr = completed.stderr or b""
        limit = profile.limits.max_output_bytes
        frames, captured_out, captured_err, truncated = self._frames(stdout, stderr, limit)
        return iter(frames), ExecResult(
            exit_code=None if timed_out else completed.returncode,
            timed_out=timed_out,
            truncated=truncated,
            stdout=captured_out,
            stderr=captured_err,
        )

    def open_process(self, handle: SandboxHandle, spec: ProcessSpec) -> SandboxProcess:
        self._validate(handle)
        profile = self.config.profile(handle.profile_id)
        process_id = uuid.uuid4().hex
        inner_name = f"tga-p-{process_id[:16]}"
        command = self._inner_command(handle, spec, inner_name, interactive=True)
        kwargs = {
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "env": None,
        }
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        try:
            process = self._popen(command, **kwargs)
        except OSError as exc:
            raise SandboxError(f"could not start sbx process: {exc}", code="PROVIDER_UNAVAILABLE") from exc
        wrapped = _SbxProcess(
            process_id,
            process,
            max_output_bytes=profile.limits.max_output_bytes,
            cleanup=lambda: self._cleanup_process(
                process_id, handle.instance_id, inner_name
            ),
        )
        self._processes[process_id] = wrapped
        wrapped.start()
        return wrapped

    def stop_process(self, process_id: str, *, fencing_token: int) -> None:
        del fencing_token
        process = self._processes.pop(process_id, None)
        if process:
            process.close()

    def inspect(self, handle: SandboxHandle) -> SandboxInspection:
        self._validate(handle)
        value = self._sandbox(handle.instance_id)
        if value is None:
            raise SandboxError("Docker Sandbox is missing", code="SANDBOX_NOT_FOUND")
        return SandboxInspection(
            handle=handle,
            runtime="docker-sandbox",
            active_processes=len(self._processes),
            created_at=_field(value, "createdAt", "created_at", "created"),
        )

    def release(self, handle: SandboxHandle) -> None:
        self._validate(handle)

    def destroy(self, handle: SandboxHandle) -> None:
        self._validate(handle)
        for process_id in tuple(self._processes):
            self.stop_process(process_id, fencing_token=handle.fencing_token)
        self._run(
            [self.config.docker_sandbox.executable, "rm", "--force", handle.instance_id],
            timeout=90,
            check=False,
        )
        marker = self._marker(handle.task_id)
        try:
            marker.unlink()
        except FileNotFoundError:
            pass

    def reconcile(
        self,
        valid_instance_ids: tuple[str, ...],
        *,
        grace_before: datetime,
    ) -> tuple[str, ...]:
        valid = set(valid_instance_ids)
        destroyed: list[str] = []
        for marker in self._markers():
            try:
                payload = json.loads(marker.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            name = str(payload.get("instance_id") or "")
            if (
                not re.fullmatch(r"tga-[a-f0-9]{16}", name)
                or name in valid
                or payload.get("config_digest") != self.config.digest
            ):
                continue
            if datetime.fromtimestamp(marker.stat().st_mtime, UTC) > grace_before:
                continue
            value = self._sandbox(name)
            if value is None:
                marker.unlink(missing_ok=True)
                continue
            workspace = Path(str(payload.get("workspace") or "")).resolve()
            self._validate_existing(value, workspace)
            self._run(
                [self.config.docker_sandbox.executable, "rm", "--force", name],
                timeout=90,
                check=False,
            )
            marker.unlink(missing_ok=True)
            destroyed.append(name)
        return tuple(destroyed)

    def _inner_command(
        self,
        handle: SandboxHandle,
        spec: ProcessSpec,
        inner_name: str,
        *,
        interactive: bool,
    ) -> list[str]:
        profile = self.config.profile(handle.profile_id)
        image = profile.image or ""
        if spec.tool_id:
            tool = self.config.tools.get(spec.tool_id)
            if tool is None or tool.profile_id != handle.profile_id:
                raise SandboxError("tool is not authorized for profile", code="TOOL_IMAGE_NOT_AUTHORIZED")
            image = tool.image
            tool_args = tool.args
        else:
            tool_args = ()
        workspace = self._workspace(handle.task_id)
        marker = self._read_marker(handle.task_id)
        if marker is None:
            raise SandboxError("trusted sandbox marker is missing", code="SANDBOX_IDENTITY_MISMATCH")
        sandbox_workspace = str(marker.get("sandbox_workspace") or "")
        if not sandbox_workspace.startswith("/") or "\x00" in sandbox_workspace:
            raise SandboxError("sandbox workspace path is invalid", code="INVALID_PROVIDER_RESPONSE")
        solver = f"{sandbox_workspace}/solvers/{handle.solver_id}"
        logical = {
            "solver": "/workspace/solver",
            "task_inputs": "/workspace/inputs",
            "task_shared": "/workspace/shared",
        }[spec.logical_workspace]
        docker = [
            "docker",
            "run",
            "--rm",
            "--name",
            inner_name,
            "--read-only",
            "--user",
            "10001:10001",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--memory",
            str(profile.limits.memory_bytes),
            "--cpus",
            str(profile.limits.cpu_count),
            "--pids-limit",
            str(profile.limits.pids_limit),
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=64m,mode=1777",
            "--volume",
            f"{solver}:/workspace/solver:rw",
            "--volume",
            f"{sandbox_workspace}/inputs:/workspace/inputs:ro",
            "--volume",
            f"{sandbox_workspace}/shared:/workspace/shared:ro",
            "--workdir",
            logical,
        ]
        if interactive:
            docker.append("-i")
        if profile.network_mode == "none":
            docker.extend(["--network", "none"])
        for name, value in sorted(spec.environment.items()):
            docker.extend(["--env", f"{name}={value}"])
        docker.append(image)
        docker.extend(tool_args)
        docker.extend(spec.argv)
        return [
            self.config.docker_sandbox.executable,
            "exec",
            handle.instance_id,
            *docker,
        ]

    def _check_version(self) -> None:
        if self._version_checked:
            return
        completed = self._run(
            [self.config.docker_sandbox.executable, "version"],
            timeout=15,
        )
        current = _version((completed.stdout or b"").decode(errors="replace"))
        minimum = _version(self.config.docker_sandbox.min_version)
        maximum = _version(self.config.docker_sandbox.max_version_exclusive)
        if not minimum <= current < maximum:
            raise SandboxError(
                f"unsupported sbx version {current}; expected >= {minimum} and < {maximum}",
                code="PROVIDER_VERSION_INCOMPATIBLE",
            )
        self._version_checked = True

    def _apply_web_policy(self, sandbox: str, resources: tuple[str, ...]) -> None:
        if not resources:
            return
        joined = ",".join(resources)
        self._run(
            [
                self.config.docker_sandbox.executable,
                "policy",
                "allow",
                "network",
                "--sandbox",
                sandbox,
                joined,
            ],
            timeout=30,
        )
        completed = self._run(
            [self.config.docker_sandbox.executable, "policy", "ls", sandbox, "--json"],
            timeout=30,
        )
        payload = (completed.stdout or b"").decode(errors="replace")
        if any(resource not in payload for resource in resources):
            raise SandboxError("effective sbx policy is missing required resources", code="POLICY_NOT_EFFECTIVE")

    def _sandbox(self, name: str) -> dict | None:
        for value in self._list_sandboxes():
            if _field(value, "name", "Name") == name:
                return value
        return None

    def _list_sandboxes(self) -> list[dict]:
        completed = self._run(
            [self.config.docker_sandbox.executable, "ls", "--json"],
            timeout=30,
        )
        try:
            value = json.loads((completed.stdout or b"[]").decode())
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SandboxError("sbx returned invalid JSON", code="INVALID_PROVIDER_RESPONSE") from exc
        if isinstance(value, dict):
            value = value.get("sandboxes", value.get("items", []))
        if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
            raise SandboxError("sbx list response has invalid shape", code="INVALID_PROVIDER_RESPONSE")
        return value

    def _validate_existing(self, value: dict, workspace: Path) -> None:
        paths = _field(value, "workspaces", "paths", "workspace")
        if paths is None:
            raise SandboxError("existing sandbox does not expose workspace identity", code="INVALID_PROVIDER_RESPONSE")
        if isinstance(paths, (str, dict)):
            paths = [paths]
        resolved = set()
        for item in paths:
            if isinstance(item, dict):
                item = _field(item, "path", "source", "hostPath", "host_path")
            if item:
                resolved.add(str(Path(str(item).removesuffix(":ro")).resolve()))
        if str(workspace.resolve()) not in resolved:
            raise SandboxError("existing sandbox belongs to another workspace", code="SANDBOX_IDENTITY_MISMATCH")

    def _workspace(self, task_id: str) -> Path:
        root = Path(self.config.docker_sandbox.task_root).expanduser().resolve()
        value = (root / task_id / "workspace").resolve()
        try:
            value.relative_to(root)
        except ValueError as exc:
            raise SandboxError("workspace escapes task root", code="INVALID_WORKSPACE") from exc
        return value

    def _marker(self, task_id: str) -> Path:
        return self._workspace(task_id).parent / "sandbox-instance.json"

    def _read_marker(self, task_id: str) -> dict | None:
        try:
            value = json.loads(self._marker(task_id).read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    def _write_marker(
        self,
        task_id: str,
        instance_id: str,
        workspace: Path,
        sandbox_workspace: str,
    ) -> None:
        marker = self._marker(task_id)
        marker.parent.mkdir(parents=True, exist_ok=True)
        temporary = marker.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {
                    "instance_id": instance_id,
                    "task_id": task_id,
                    "workspace": str(workspace),
                    "sandbox_workspace": sandbox_workspace,
                    "config_digest": self.config.digest,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        os.chmod(temporary, 0o600)
        temporary.replace(marker)

    def _resolve_sandbox_workspace(self, sandbox: str, workspace: Path) -> str:
        completed = self._run(
            [
                self.config.docker_sandbox.executable,
                "exec",
                "--workdir",
                str(workspace),
                sandbox,
                "pwd",
            ],
            timeout=30,
        )
        value = (completed.stdout or b"").decode("utf-8", errors="strict").strip()
        if not value.startswith("/") or "\x00" in value or "\n" in value:
            raise SandboxError(
                "sbx returned an invalid workspace path",
                code="INVALID_PROVIDER_RESPONSE",
            )
        return value.rstrip("/")

    def _markers(self) -> Iterator[Path]:
        root = Path(self.config.docker_sandbox.task_root).expanduser().resolve()
        return root.glob("*/sandbox-instance.json")

    def _remove_inner(self, sandbox: str, name: str) -> None:
        self._run(
            [
                self.config.docker_sandbox.executable,
                "exec",
                sandbox,
                "docker",
                "rm",
                "--force",
                name,
            ],
            timeout=30,
            check=False,
        )

    def _cleanup_process(self, process_id: str, sandbox: str, name: str) -> None:
        self._processes.pop(process_id, None)
        self._remove_inner(sandbox, name)

    def _validate(self, handle: SandboxHandle) -> None:
        if handle.provider != self.provider_name or handle.config_digest != self.config.digest:
            raise SandboxError("invalid sandbox handle", code="INVALID_HANDLE")

    @staticmethod
    def _frames(
        stdout: bytes,
        stderr: bytes,
        limit: int,
    ) -> tuple[list[ExecFrame], bytes, bytes, bool]:
        remaining = limit
        frames: list[ExecFrame] = []
        captured: dict[str, bytes] = {}
        for stream, content in (("stdout", stdout), ("stderr", stderr)):
            data = content[:remaining]
            remaining -= len(data)
            captured[stream] = data
            if data:
                frames.append(
                    ExecFrame(
                        sequence=len(frames) + 1,
                        timestamp_unix_ms=int(time.time() * 1000),
                        stream=stream,
                        data=data,
                    )
                )
        return frames, captured["stdout"], captured["stderr"], len(stdout) + len(stderr) > limit

    def _run(self, command: list[str], *, timeout: int, check: bool = True):
        try:
            completed = self._runner(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            raise SandboxError("sbx executable is unavailable", code="PROVIDER_UNAVAILABLE", retryable=True) from exc
        except subprocess.TimeoutExpired as exc:
            raise SandboxError("sbx operation timed out", code="PROVIDER_TIMEOUT", retryable=True) from exc
        if check and completed.returncode:
            detail = (completed.stderr or b"").decode("utf-8", errors="replace")[:1000]
            raise SandboxError(f"sbx operation failed: {detail}", code="PROVIDER_FAILED")
        return completed


def _field(value: dict, *names: str):
    for name in names:
        if name in value:
            return value[name]
    return None
