"""Docker lifecycle adapter. Workers are created per task, never by Compose."""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import UUID

from .config import ResolvedAgent, TGA3Config
from .errors import RuntimeUnavailableError


@dataclass(frozen=True)
class LaunchSpec:
    task_id: UUID
    agent: ResolvedAgent
    report_input: str | None = None


class ContainerRuntime(Protocol):
    async def launch(self, spec: LaunchSpec) -> str: ...
    async def stop(self, task_id: UUID, agent_id: str) -> None: ...
    async def image_status(self, image: str) -> dict[str, object]: ...
    async def report(self, spec: LaunchSpec, emit) -> str: ...
    async def scout(self, spec: LaunchSpec, emit) -> None: ...


class DockerContainerRuntime:
    def __init__(self, config: TGA3Config) -> None:
        self.config = config
        try:
            import docker

            self.client = docker.from_env()
        except Exception as exc:
            raise RuntimeUnavailableError(f"Docker SDK/daemon unavailable: {exc}") from exc

    def _ensure_worker_directory(self, path: Path) -> None:
        docker_cfg = self.config.runtime.docker
        path.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            current = path.stat()
            if (current.st_uid, current.st_gid) != (docker_cfg.worker_uid, docker_cfg.worker_gid):
                try:
                    os.chown(path, docker_cfg.worker_uid, docker_cfg.worker_gid)
                except PermissionError as exc:
                    raise RuntimeUnavailableError(
                        f"cannot assign {path} to worker uid/gid "
                        f"{docker_cfg.worker_uid}:{docker_cfg.worker_gid}; "
                        "run the control plane with matching ownership or sufficient permission"
                    ) from exc
        path.chmod(0o770)

    def _task_paths(self, task_id: UUID, agent_id: str) -> tuple[Path, Path, Path]:
        workspace = self.config.resolve_path(self.config.runtime.workspace_root) / str(task_id) / agent_id
        inputs = self.config.resolve_path(self.config.runtime.input_root) / str(task_id)
        artifacts = self.config.resolve_path(self.config.runtime.artifact_root) / str(task_id)
        if self.config.agents.agents[agent_id].role in {"scout", "worker"}:
            artifacts = artifacts / agent_id
        inputs.mkdir(parents=True, exist_ok=True)
        self._ensure_worker_directory(workspace)
        self._ensure_worker_directory(artifacts)
        return workspace, inputs, artifacts

    def _launch_sync(self, spec: LaunchSpec) -> str:
        runtime = self.config.runtime
        docker_cfg = runtime.docker
        binding = spec.agent
        workspace, inputs, artifacts = self._task_paths(spec.task_id, binding.agent_id)
        is_reporter = binding.role == "reporter"
        if is_reporter:
            (workspace / "report-input.json").write_text(spec.report_input or "[]", encoding="utf-8")
            (workspace / "writeup.md").unlink(missing_ok=True)
        volumes = {
            str(workspace): {"bind": "/workspace", "mode": "rw"},
            str(inputs): {"bind": "/inputs", "mode": "ro"},
            str(artifacts): {"bind": "/artifacts", "mode": "ro" if is_reporter else "rw"},
            str(self.config.skills_root): {"bind": "/skills", "mode": "ro"},
        }
        if binding.role == "worker":
            recon = self.config.resolve_path(runtime.artifact_root) / str(spec.task_id) / "scout"
            self._ensure_worker_directory(recon)
            volumes[str(recon)] = {"bind": "/recon", "mode": "ro"}
        if is_reporter:
            volumes[str(workspace / "report-input.json")] = {"bind": "/workspace/report-input.json", "mode": "ro"}
        image = runtime.worker_images[binding.agent_id]
        blackboard_mcp_url = f"{runtime.blackboard_mcp_url.rstrip('/')}/"
        environment = {
            "HOME": "/home/tga3",
            "TGA3_TASK_ID": str(spec.task_id),
            "TGA3_AGENT_ID": binding.agent_id,
            "TGA3_AGENT_DISPLAY_NAME": binding.display_name,
            "TGA3_AGENT_RUNTIME": binding.runtime,
            "TGA3_AGENT_ROLE": binding.role,
            "TGA3_CLI_TIMEOUT_SECONDS": str(
                runtime.scout_timeout_seconds + 30 if binding.role == "scout" else runtime.cli_timeout_seconds
            ),
            "TGA3_SCOUT_TIMEOUT_SECONDS": str(runtime.scout_timeout_seconds),
            "TGA3_SYSTEM_PROMPT": binding.system_prompt,
            "TGA3_CONTROL_WS_URL": runtime.control_ws_url,
            "TGA3_BLACKBOARD_MCP_URL": blackboard_mcp_url,
            "TGA3_PROVIDER_ID": binding.provider.id,
            "TGA3_PROVIDER_PROTOCOL": binding.protocol,
            "TGA3_MODEL_ID": binding.model.id,
            "TGA3_MODEL_NAME": binding.model.name,
            "TGA3_API_KEY": binding.api_key.get_secret_value(),
            "TGA3_BASE_URL": binding.provider.sdk_base_url(binding.protocol) or "",
            "TGA3_MAX_TURNS_PER_CYCLE": str(binding.max_turns_per_cycle),
            "TGA3_SYNC_SECONDS": str(runtime.cadence.worker_sync_seconds),
            "TGA3_STARTUP_PROMPT": runtime.worker_cycle_prompts.startup,
            "TGA3_PERIODIC_PROMPT": runtime.worker_cycle_prompts.periodic,
            "TGA3_BLACKBOARD_CHANGED_PROMPT": runtime.worker_cycle_prompts.blackboard_changed,
        }
        container = self.client.containers.run(
            image=image,
            name=f"tga3-{str(spec.task_id)[:8]}-{binding.agent_id}",
            detach=True,
            auto_remove=False,
            environment=environment,
            user=f"{docker_cfg.worker_uid}:{docker_cfg.worker_gid}",
            labels={"tga3.task_id": str(spec.task_id), "tga3.agent_id": binding.agent_id},
            volumes=volumes,
            working_dir="/workspace",
            network=runtime.docker.network,
            nano_cpus=int(docker_cfg.cpu_cores * 1_000_000_000),
            mem_limit=f"{docker_cfg.memory_mb}m",
            pids_limit=docker_cfg.pids_limit,
            cap_drop=["ALL"],
            cap_add=[] if is_reporter else docker_cfg.cap_add,
            security_opt=["no-new-privileges:true"],
            extra_hosts={"host.docker.internal": "host-gateway"},
        )
        return container.id

    async def scout(self, spec: LaunchSpec, emit) -> None:
        """One CLI turn; the coordinator owns the total deadline and cancels this call."""
        launch = asyncio.create_task(self.launch(spec))
        offset = 0
        pending = b""
        try:
            container_id = await asyncio.shield(launch)
            await emit("container.started", {"container_id": container_id})
            container = await asyncio.to_thread(self.client.containers.get, container_id)
            while True:
                await asyncio.to_thread(container.reload)
                raw = await asyncio.to_thread(container.logs)
                pending += raw[offset:]
                offset = len(raw)
                lines = pending.split(b"\n")
                pending = lines.pop()
                for line in lines:
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(event, dict) and event.get("method") in {
                        "agent.session", "agent.action.started", "agent.action.completed", "agent.output.delta",
                    }:
                        await emit(event["method"], event.get("params", {}))
                if container.status in {"exited", "dead"}:
                    if container.attrs["State"].get("ExitCode") != 0:
                        key = spec.agent.api_key.get_secret_value()
                        detail = raw.decode(errors="replace")[-12000:]
                        raise RuntimeUnavailableError(detail.replace(key, "[redacted]") if key else detail)
                    return
                await asyncio.sleep(0.5)
        finally:
            try:
                await launch
            finally:
                await self.stop(spec.task_id, spec.agent.agent_id)

    async def report(self, spec: LaunchSpec, emit) -> str:
        launch = asyncio.create_task(self.launch(spec))
        try:
            container_id = await asyncio.shield(launch)
            await emit("container.started", {"container_id": container_id})
            container = await asyncio.to_thread(self.client.containers.get, container_id)
            deadline = asyncio.get_running_loop().time() + self.config.runtime.cli_timeout_seconds + 30
            while True:
                await asyncio.to_thread(container.reload)
                if container.status in {"exited", "dead"}:
                    break
                if asyncio.get_running_loop().time() > deadline:
                    raise RuntimeUnavailableError("Reporter container timed out")
                await asyncio.sleep(0.25)
            raw = await asyncio.to_thread(container.logs, tail=200)
            key = spec.agent.api_key.get_secret_value()
            for line in raw.decode(errors="replace").splitlines():
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if isinstance(event, dict) and event.get("method") in {
                    "agent.session",
                    "agent.action.started",
                    "agent.action.completed",
                }:
                    await emit(event["method"], event.get("params", {}))
            if container.attrs["State"].get("ExitCode") != 0:
                detail = raw.decode(errors="replace")[-12000:]
                raise RuntimeUnavailableError(detail.replace(key, "[redacted]") if key else detail)
            workspace, _, _ = self._task_paths(spec.task_id, spec.agent.agent_id)
            path = workspace / "writeup.md"
            if path.is_symlink():
                raise RuntimeUnavailableError("Reporter output must be a regular file")
            result = path.read_text(encoding="utf-8")
            if not result.strip():
                raise RuntimeUnavailableError("Reporter returned an empty report")
            return result
        finally:
            try:
                await launch
            finally:
                await self.stop(spec.task_id, spec.agent.agent_id)

    async def launch(self, spec: LaunchSpec) -> str:
        try:
            return await asyncio.to_thread(self._launch_sync, spec)
        except Exception as exc:
            raise RuntimeUnavailableError(f"failed to launch {spec.agent.agent_id}: {exc}") from exc

    def _stop_sync(self, task_id: UUID, agent_id: str) -> None:
        containers = self.client.containers.list(
            all=True,
            filters={"label": [f"tga3.task_id={task_id}", f"tga3.agent_id={agent_id}"]},
        )
        for container in containers:
            if container.status == "running":
                container.stop(timeout=10)
            container.remove(v=True, force=True)

    async def stop(self, task_id: UUID, agent_id: str) -> None:
        await asyncio.to_thread(self._stop_sync, task_id, agent_id)

    def _image_status_sync(self, image: str) -> dict[str, object]:
        try:
            item = self.client.images.get(image)
        except Exception as exc:
            # Docker's exception types are intentionally not imported here so a
            # daemon/protocol error is reported through the same stable contract.
            name = type(exc).__name__
            if name == "ImageNotFound":
                return {"status": "missing", "available": False, "detail": "镜像尚未构建"}
            return {"status": "unavailable", "available": False, "detail": str(exc)}
        size = int(item.attrs.get("Size") or 0)
        return {
            "status": "healthy",
            "available": True,
            "detail": "Docker 镜像可用",
            "image_id": item.short_id,
            "size_bytes": size,
            "created": item.attrs.get("Created"),
        }

    async def image_status(self, image: str) -> dict[str, object]:
        return await asyncio.to_thread(self._image_status_sync, image)


class FakeContainerRuntime:
    """Test runtime recording lifecycle operations without Docker."""

    def __init__(self) -> None:
        self.launched: list[LaunchSpec] = []
        self.stopped: list[tuple[UUID, str]] = []

    async def launch(self, spec: LaunchSpec) -> str:
        self.launched.append(spec)
        return f"fake-{spec.task_id}-{spec.agent.agent_id}"

    async def report(self, spec: LaunchSpec, emit) -> str:
        container_id = await self.launch(spec)
        try:
            await emit("container.started", {"container_id": container_id})
            return "# Test report\n\n" + (spec.report_input or "")
        finally:
            await self.stop(spec.task_id, spec.agent.agent_id)

    async def scout(self, spec: LaunchSpec, emit) -> None:
        container_id = await self.launch(spec)
        try:
            await emit("container.started", {"container_id": container_id})
        finally:
            await self.stop(spec.task_id, spec.agent.agent_id)

    async def stop(self, task_id: UUID, agent_id: str) -> None:
        self.stopped.append((task_id, agent_id))

    async def image_status(self, image: str) -> dict[str, object]:
        return {"status": "healthy", "available": True, "detail": "test image", "image_id": image}


__all__ = ["ContainerRuntime", "DockerContainerRuntime", "FakeContainerRuntime", "LaunchSpec"]
