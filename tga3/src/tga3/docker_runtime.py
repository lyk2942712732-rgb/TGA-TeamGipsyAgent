"""Docker lifecycle adapter. Workers are created per task, never by Compose."""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol
from uuid import UUID, uuid4

from .action_logs import read_action_log
from .config import ResolvedAgent, TGA3Config
from .errors import RuntimeUnavailableError


@dataclass(frozen=True)
class LaunchSpec:
    task_id: UUID
    agent: ResolvedAgent
    report_input: str | None = None
    scout_phase: Literal["collecting", "executing", "extracting", "merging"] | None = None
    phase_budget_seconds: float | None = None
    aoe_parent_id: UUID | None = None
    fresh_session: bool = False
    attempt_id: str | None = None
    attempt_deadline: float | None = None
    scout_question_id: UUID | None = None
    target_mode: str = "unknown"
    target_addresses: tuple[str, ...] = ()
    evidence_agents: tuple[str, ...] = ()


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
        workspace = self.config.task_path(task_id, "workspaces", agent_id)
        inputs = self.config.task_path(task_id, "inputs")
        logs = self.config.task_path(task_id, "logs")
        if agent_id in self.config.agents.agents:
            logs = logs / agent_id
        inputs.mkdir(parents=True, exist_ok=True)
        self._ensure_worker_directory(workspace)
        self._ensure_worker_directory(workspace / "result")
        self._ensure_worker_directory(logs)
        return workspace, inputs, logs

    def _launch_sync(self, spec: LaunchSpec) -> str:
        runtime = self.config.runtime
        docker_cfg = runtime.docker
        binding = spec.agent
        workspace, inputs, logs = self._task_paths(spec.task_id, binding.agent_id)
        is_reporter = binding.role == "reporter"
        extracting = binding.role == "scout" and spec.scout_phase in {"extracting", "merging"}
        phase_budget = int(spec.phase_budget_seconds if spec.phase_budget_seconds is not None else
                           runtime.scout_timeout_seconds)
        if is_reporter:
            (workspace / "report-input.json").write_text(spec.report_input or "[]", encoding="utf-8")
            (workspace / "result").mkdir(exist_ok=True)
            (workspace / "result" / "writeup.md").unlink(missing_ok=True)
            (workspace / "experience-cards.json").unlink(missing_ok=True)
        volumes = {
            str(workspace): {"bind": "/workspace", "mode": "rw"},
            str(inputs): {"bind": "/inputs", "mode": "ro"},
            str(logs): {"bind": "/logs", "mode": "ro" if extracting else "rw"},
            str(self.config.skills_root): {"bind": "/skills", "mode": "ro"},
        }
        if binding.role == "scout":
            intels = self.config.task_path(spec.task_id, "intels", "scout")
            self._ensure_worker_directory(intels)
            for stage in ("raw", "merged"):
                self._ensure_worker_directory(intels / stage)
            volumes[str(intels)] = {"bind": "/intels", "mode": "ro"}
            if spec.aoe_parent_id is not None:
                volumes[str(self.config.task_path(spec.aoe_parent_id))] = {"bind": "/aoe", "mode": "ro"}
        elif spec.aoe_parent_id == spec.task_id:
            raise RuntimeUnavailableError(
                'Batch uploads are readable only by Scout; Workers require an assigned question')
        if is_reporter:
            volumes[str(self.config.task_path(spec.task_id, "logs"))] = {"bind": "/evidence", "mode": "ro"}
            for agent_id in self.config.agents.agents:
                if agent_id != binding.agent_id:
                    root = self.config.task_path(spec.task_id, "workspaces", agent_id)
                    self._ensure_worker_directory(root / "result")
                    volumes[str(root / "result")] = {"bind": f"/evidence-files/{agent_id}/result", "mode": "ro"}
                    actions = read_action_log(self.config.task_path(spec.task_id, "logs", agent_id))["actions"]
                    for action in actions:
                        for relative in action.get("artifact", {}).get("attachments", []):
                            path = (root / relative).resolve()
                            if not path.is_relative_to(root.resolve()):
                                raise RuntimeUnavailableError("Reporter attachment escapes the agent workspace")
                            if path.is_file():
                                volumes[str(path)] = {"bind": f"/evidence-files/{agent_id}/{relative}", "mode": "ro"}
            volumes[str(workspace / "report-input.json")] = {"bind": "/workspace/report-input.json", "mode": "ro"}
        if spec.evidence_agents:
            if binding.role != 'worker' or not self.config.question_scope(spec.task_id):
                raise RuntimeUnavailableError('Prior-attempt evidence is scoped to an assigned AOE question')
            for agent_id in spec.evidence_agents:
                if agent_id not in self.config.worker_agent_ids or agent_id == binding.agent_id:
                    raise RuntimeUnavailableError('Invalid prior-attempt Worker')
                root = self.config.task_path(spec.task_id, 'workspaces', agent_id)
                self._ensure_worker_directory(root / 'result')
                volumes[str(root / 'result')] = {'bind': f'/evidence-files/{agent_id}/result', 'mode': 'ro'}
                log = self.config.task_path(spec.task_id, 'logs', agent_id) / f'{agent_id}-actions.json'
                if log.is_file():
                    volumes[str(log)] = {'bind': f'/evidence-logs/{agent_id}-actions.json', 'mode': 'ro'}
                for action in read_action_log(log.parent)['actions']:
                    for relative in action.get('artifact', {}).get('attachments', []):
                        path = (root / relative).resolve()
                        if not path.is_relative_to(root.resolve()):
                            raise RuntimeUnavailableError('Prior-attempt attachment escapes its workspace')
                        if path.is_file():
                            volumes[str(path)] = {'bind': f'/evidence-files/{agent_id}/{relative}', 'mode': 'ro'}
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
                phase_budget + 30 if binding.role == "scout" else runtime.cli_timeout_seconds
            ),
            "TGA3_SCOUT_TIMEOUT_SECONDS": str(phase_budget if not extracting else runtime.scout_timeout_seconds),
            "TGA3_SCOUT_PHASE": spec.scout_phase or "executing",
            "TGA3_SCOUT_INTEL_MCP_URL": runtime.blackboard_mcp_url.rstrip("/").removesuffix("/mcp") + "/scout-mcp/",
            "TGA3_SYSTEM_PROMPT": binding.system_prompt,
            "TGA3_REPORT_EXPERIENCE": "1" if is_reporter and "experience-cards.json" in binding.system_prompt else "0",
            "TGA3_CONTROL_WS_URL": runtime.control_ws_url,
            "TGA3_BLACKBOARD_MCP_URL": blackboard_mcp_url,
            "TGA3_PROVIDER_ID": binding.provider.id,
            "TGA3_PROVIDER_PROTOCOL": binding.protocol,
            "TGA3_MODEL_ID": binding.model.id,
            "TGA3_MODEL_NAME": binding.model.name,
            "TGA3_API_KEY": binding.api_key.get_secret_value(),
            "TGA3_BASE_URL": binding.provider.sdk_base_url(binding.protocol) or "",
            "TGA3_STARTUP_PROMPT": runtime.worker_cycle_prompts.startup,
            "TGA3_PERIODIC_PROMPT": runtime.worker_cycle_prompts.periodic,
            "TGA3_BLACKBOARD_CHANGED_PROMPT": runtime.worker_cycle_prompts.blackboard_changed,
        }
        if binding.role == "worker":
            from .experience import ExperienceStore

            view = ExperienceStore(self.config).prepare_view(spec.task_id)
            volumes[str(view)] = {"bind": "/experience", "mode": "ro"}
            environment['TGA3_WEB_SEARCH_MCP_URL'] = (
                runtime.blackboard_mcp_url.rstrip('/').removesuffix('/mcp')+'/web-mcp/')
            findings = self.config.task_path(spec.task_id, 'findings', binding.agent_id)
            self._ensure_worker_directory(findings)
            volumes[str(findings)] = {'bind': '/findings', 'mode': 'rw'}
            environment["TGA3_CTF_KNOWLEDGE_MCP_URL"] = (
                runtime.blackboard_mcp_url.rstrip('/').removesuffix('/mcp') + '/knowledge-mcp/'
                if runtime.ctf_knowledge_mcp_url.strip() else '')
        if question_scope := self.config.question_scope(spec.task_id):
            # Reporter also launches through the ordinary report path. Its
            # journal must retain question provenance even without attempt args.
            environment["TGA3_QUESTION_ID"] = question_scope["question_id"]
        if spec.aoe_parent_id is not None:
            environment.update(TGA3_AOE_PARENT_ID=str(spec.aoe_parent_id),
                               TGA3_SCOUT_START_PHASE=spec.scout_phase or "collecting")
            if binding.role in {'scout', 'worker'}:
                overview = self.config.task_path(spec.aoe_parent_id) / 'overview'
                if not all((overview / name).is_file() for name in ['scene.md', 'task.md']):
                    raise RuntimeUnavailableError('AOE overview originals must be prepared before launch')
                volumes[str(overview)] = {'bind': '/task-overview', 'mode': 'ro'}
                environment['TGA3_TASK_OVERVIEW_ROOT'] = '/task-overview'
            if binding.role == "scout":
                environment["TGA3_SCOUT_INTEL_MCP_URL"] = (
                    runtime.blackboard_mcp_url.rstrip("/").removesuffix("/mcp") + "/aoe-scout-mcp/"
                )
            if spec.task_id != spec.aoe_parent_id:
                environment["TGA3_QUESTION_ID"] = str(spec.task_id)
            if binding.role == "worker":
                if spec.evidence_agents:
                    environment['TGA3_PEER_EVIDENCE'] = json.dumps(spec.evidence_agents)
                source = self.config.task_path(spec.task_id) / 'source.json'
                if source.is_file():
                    volumes[str(source)] = {'bind': '/question-source.json', 'mode': 'ro'}
                    environment['TGA3_QUESTION_SOURCE'] = '/question-source.json'
                environment['TGA3_STARTUP_PROMPT'] = (
                    'In a NEW CLI context, read /task-overview/scene.md and /task-overview/task.md separately. '
                    'When resuming the same context, use the originals already read without reading them again. '
                    'These are the complete shared scene and initial user instructions, including platform credentials. '
                    'Use native Read or cat with sufficient output allowance (Codex max_output_tokens=12000). '
                    'Then solve only the assigned question from the initial question blackboard. '
                    'Do not reread the blackboard or overview each turn; hooks deliver subsequent question updates.')
                environment.update(TGA3_FORCE_NEW_SESSION="1" if spec.fresh_session else "0",
                    TGA3_TARGET_MODE=spec.target_mode,
                    TGA3_TARGET_ADDRESSES=json.dumps(spec.target_addresses),
                    TGA3_ATTEMPT_ID=spec.attempt_id or "", TGA3_ATTEMPT_DEADLINE=str(spec.attempt_deadline or ""),
                    TGA3_BOARD_START_TOKEN=uuid4().hex)
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
        """Drive three work phases inside one container and one resumed CLI session."""
        if spec.aoe_parent_id is not None:
            await self._aoe_scout(spec, emit)
            return
        if not hasattr(self, "_scouts"):
            self._scouts = {}
        phase = spec.scout_phase or "executing"
        workspace = self.config.task_path(spec.task_id, "workspaces", spec.agent.agent_id)
        state_dir = workspace / ".tga3"
        result_path = state_dir / f"scout-{phase}-result.json"
        launch = None
        try:
            first_phase = "collecting" if spec.aoe_parent_id else "executing"
            if phase == first_phase or spec.aoe_parent_id and spec.task_id not in self._scouts:
                for name in ("scout-executing-result.json", "scout-extracting-result.json", "scout-extract-request",
                             "scout-merging-result.json", "scout-merge-request", "scout-collecting-result.json",
                             "scout-execute-request"):
                    (state_dir / name).unlink(missing_ok=True)
                launch = asyncio.create_task(self.launch(spec))
                container_id = await asyncio.shield(launch)
                self._scouts[spec.task_id] = {"id": container_id, "offset": 0, "pending": b""}
                await emit("container.started", {"container_id": container_id})
            else:
                if spec.task_id not in self._scouts:
                    raise RuntimeUnavailableError("Scout Intel phases require their original running container")
                container_id = self._scouts[spec.task_id]["id"]
                # The coordinator has already switched MCP permissions to the next work phase.
                signal = {"executing": "scout-execute-request", "extracting": "scout-extract-request",
                          "merging": "scout-merge-request"}[phase]
                (state_dir / signal).touch()
            state = self._scouts[spec.task_id]
            container = await asyncio.to_thread(self.client.containers.get, container_id)
            while True:
                await asyncio.to_thread(container.reload)
                raw = await asyncio.to_thread(container.logs)
                state["pending"] += raw[state["offset"]:]
                state["offset"] = len(raw)
                lines = state["pending"].split(b"\n")
                state["pending"] = lines.pop()
                for line in lines:
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(event, dict) and event.get("method") in {
                        "agent.session", "agent.action.started", "agent.action.completed",
                        "agent.output.delta", "agent.recovery",
                    }:
                        await emit(event["method"], event.get("params", {}))
                if result_path.is_file():
                    result = json.loads(result_path.read_text(encoding="utf-8"))
                    if result["finish_reason"] == "timeout":
                        raise TimeoutError
                    if result["finish_reason"] == "error":
                        raise RuntimeUnavailableError(result["error"])
                    return
                if container.status in {"exited", "dead"}:
                    key = spec.agent.api_key.get_secret_value()
                    detail = raw.decode(errors="replace")[-12000:]
                    raise RuntimeUnavailableError(detail.replace(key, "[redacted]") if key else detail)
                await asyncio.sleep(0.5)
        finally:
            try:
                if launch is not None:
                    container_id = await launch
                    self._scouts.setdefault(spec.task_id, {"id": container_id, "offset": 0, "pending": b""})
            finally:
                if phase == "merging":
                    self._scouts.pop(spec.task_id, None)
                    await self.stop(spec.task_id, spec.agent.agent_id)

    async def _aoe_scout(self, spec: LaunchSpec, emit) -> None:
        """Run the collection pre-phase while Workers claim registered questions."""
        if spec.scout_phase != "collecting" or spec.scout_question_id is not None:
            raise RuntimeUnavailableError("AOE Scout supports only batch collection")
        if not hasattr(self, "_scouts"):
            self._scouts = {}
        state_dir = self.config.task_path(spec.task_id, "workspaces", "scout") / ".tga3"
        request_id = uuid4().hex
        result_path = state_dir / "aoe-scout-results" / f"{request_id}.json"
        launch = None
        try:
            if spec.task_id not in self._scouts:
                # Reclaim a container left by an interrupted controller before
                # publishing a request that only the resumed runner may consume.
                await self.stop(spec.task_id, "scout")
            self._ensure_worker_directory(state_dir)
            request = {"id": request_id, "phase": spec.scout_phase,
                       "question_id": str(spec.scout_question_id) if spec.scout_question_id else None,
                       "budget_seconds": spec.phase_budget_seconds}
            temporary = state_dir / f"aoe-scout-request.{request_id}.tmp"
            temporary.write_text(json.dumps(request), encoding="utf-8")
            temporary.replace(state_dir / "aoe-scout-request.json")
            if spec.task_id not in self._scouts:
                launch = asyncio.create_task(self.launch(spec))
                container_id = await asyncio.shield(launch)
                self._scouts[spec.task_id] = {"id": container_id, "offset": 0, "pending": b""}
                await emit("container.started", {"container_id": container_id})
            state = self._scouts[spec.task_id]
            container = await asyncio.to_thread(self.client.containers.get, state["id"])
            while True:
                await asyncio.to_thread(container.reload)
                raw = await asyncio.to_thread(container.logs)
                state["pending"] += raw[state["offset"]:]
                state["offset"] = len(raw)
                lines = state["pending"].split(b"\n")
                state["pending"] = lines.pop()
                for line in lines:
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(event, dict) and event.get("method") in {
                        "agent.session", "agent.action.started", "agent.action.completed",
                        "agent.output.delta", "agent.recovery",
                    }:
                        await emit(event["method"], event.get("params", {}))
                if result_path.is_file():
                    result = json.loads(result_path.read_text(encoding="utf-8"))
                    if result.get("question_id") != request["question_id"] or result.get("phase") != spec.scout_phase:
                        raise RuntimeUnavailableError("Scout response does not match the assigned question phase")
                    if result["finish_reason"] == "timeout":
                        raise TimeoutError
                    if result["finish_reason"] == "error":
                        raise RuntimeUnavailableError(result["error"])
                    return
                if container.status in {"exited", "dead"}:
                    key = spec.agent.api_key.get_secret_value()
                    detail = raw.decode(errors="replace")[-12000:]
                    raise RuntimeUnavailableError(detail.replace(key, "[redacted]") if key else detail)
                await asyncio.sleep(0.25)
        finally:
            if launch is not None:
                container_id = await launch
                self._scouts.setdefault(spec.task_id, {"id": container_id, "offset": 0, "pending": b""})

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
                    "agent.recovery",
                    "agent.action.started",
                    "agent.action.completed",
                }:
                    await emit(event["method"], event.get("params", {}))
            if container.attrs["State"].get("ExitCode") != 0:
                detail = raw.decode(errors="replace")[-12000:]
                raise RuntimeUnavailableError(detail.replace(key, "[redacted]") if key else detail)
            workspace, _, _ = self._task_paths(spec.task_id, spec.agent.agent_id)
            path = workspace / "result" / "writeup.md"
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
        if agent_id == "scout":
            getattr(self, "_scouts", {}).pop(task_id, None)

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
        if spec.scout_phase == ("collecting" if spec.aoe_parent_id else "executing"):
            container_id = await self.launch(spec)
            await emit("container.started", {"container_id": container_id})
        elif spec.scout_phase == "merging" and spec.aoe_parent_id is None:
            await self.stop(spec.task_id, spec.agent.agent_id)

    async def stop(self, task_id: UUID, agent_id: str) -> None:
        self.stopped.append((task_id, agent_id))

    async def image_status(self, image: str) -> dict[str, object]:
        return {"status": "healthy", "available": True, "detail": "test image", "image_id": image}


__all__ = ["ContainerRuntime", "DockerContainerRuntime", "FakeContainerRuntime", "LaunchSpec"]
