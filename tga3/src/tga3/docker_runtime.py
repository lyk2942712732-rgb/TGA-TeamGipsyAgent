"""Docker lifecycle adapter. Workers are created per task, never by Compose."""

from __future__ import annotations

import asyncio
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


class DockerContainerRuntime:
    def __init__(self, config: TGA3Config) -> None:
        self.config = config
        try:
            import docker

            self.client = docker.from_env()
        except Exception as exc:
            raise RuntimeUnavailableError(f"Docker SDK/daemon unavailable: {exc}") from exc

    def _task_paths(self, task_id: UUID, agent_id: str) -> tuple[Path, Path, Path]:
        workspace = self.config.resolve_path(self.config.runtime.workspace_root) / str(task_id) / agent_id
        inputs = self.config.resolve_path(self.config.runtime.input_root) / str(task_id)
        artifacts = self.config.resolve_path(self.config.runtime.artifact_root) / str(task_id)
        for path in (workspace, inputs, artifacts):
            path.mkdir(parents=True, exist_ok=True)
        return workspace, inputs, artifacts

    def _launch_sync(self, spec: LaunchSpec) -> str:
        runtime = self.config.runtime
        docker_cfg = runtime.docker
        binding = spec.agent
        workspace, inputs, artifacts = self._task_paths(spec.task_id, binding.agent_id)
        image = runtime.worker_images[binding.agent_id]
        environment = {
            "TGA3_TASK_ID": str(spec.task_id),
            "TGA3_AGENT_ID": binding.agent_id,
            "TGA3_AGENT_RUNTIME": binding.runtime,
            "TGA3_CONTROL_WS_URL": runtime.control_ws_url,
            "TGA3_BLACKBOARD_MCP_URL": runtime.blackboard_mcp_url,
            "TGA3_PROVIDER_ID": binding.provider.id,
            "TGA3_PROVIDER_PROTOCOL": binding.provider.protocol,
            "TGA3_MODEL_ID": binding.model.id,
            "TGA3_MODEL_NAME": binding.model.name,
            "TGA3_API_KEY": binding.api_key.get_secret_value(),
            "TGA3_BASE_URL": binding.provider.base_url or "",
            "TGA3_MAX_TURNS_PER_CYCLE": str(binding.max_turns_per_cycle),
            "TGA3_SYNC_SECONDS": str(runtime.cadence.worker_sync_seconds),
        }
        container = self.client.containers.run(
            image=image,
            name=f"tga3-{str(spec.task_id)[:8]}-{binding.agent_id}",
            detach=True,
            auto_remove=False,
            environment=environment,
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


__all__ = ["ContainerRuntime", "DockerContainerRuntime", "FakeContainerRuntime", "LaunchSpec"]
