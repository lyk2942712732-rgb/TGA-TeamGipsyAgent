"""Docker lifecycle adapter. Workers are created per task, never by Compose."""

from __future__ import annotations

import asyncio
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


class ContainerRuntime(Protocol):
    async def launch(self, spec: LaunchSpec) -> str: ...
    async def stop(self, task_id: UUID, agent_id: str) -> None: ...
    async def image_status(self, image: str) -> dict[str, object]: ...


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
        inputs.mkdir(parents=True, exist_ok=True)
        self._ensure_worker_directory(workspace)
        self._ensure_worker_directory(artifacts)
        return workspace, inputs, artifacts

    def _launch_sync(self, spec: LaunchSpec) -> str:
        runtime = self.config.runtime
        docker_cfg = runtime.docker
        binding = spec.agent
        workspace, inputs, artifacts = self._task_paths(spec.task_id, binding.agent_id)
        image = runtime.worker_images[binding.agent_id]
        blackboard_mcp_url = f"{runtime.blackboard_mcp_url.rstrip('/')}/"
        environment = {
            "HOME": "/home/tga3",
            "TGA3_TASK_ID": str(spec.task_id),
            "TGA3_AGENT_ID": binding.agent_id,
            "TGA3_AGENT_DISPLAY_NAME": binding.display_name,
            "TGA3_AGENT_RUNTIME": binding.runtime,
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
            volumes={
                str(workspace): {"bind": "/workspace", "mode": "rw"},
                str(inputs): {"bind": "/inputs", "mode": "ro"},
                str(artifacts): {"bind": "/artifacts", "mode": "rw"},
            },
            working_dir="/workspace",
            network=runtime.docker.network,
            nano_cpus=int(docker_cfg.cpu_cores * 1_000_000_000),
            mem_limit=f"{docker_cfg.memory_mb}m",
            pids_limit=docker_cfg.pids_limit,
            cap_drop=["ALL"],
            cap_add=docker_cfg.cap_add,
            security_opt=["no-new-privileges:true"],
            extra_hosts={"host.docker.internal": "host-gateway"},
        )
        return container.id

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

    async def stop(self, task_id: UUID, agent_id: str) -> None:
        self.stopped.append((task_id, agent_id))

    async def image_status(self, image: str) -> dict[str, object]:
        return {"status": "healthy", "available": True, "detail": "test image", "image_id": image}


__all__ = ["ContainerRuntime", "DockerContainerRuntime", "FakeContainerRuntime", "LaunchSpec"]
