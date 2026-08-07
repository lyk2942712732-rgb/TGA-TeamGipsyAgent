"""Central construction of current infrastructure and runtime services."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tga.deployment.paths import run_root as resolve_run_root
from tga.evidence.store import EvidenceStore
from tga.infrastructure.solver_definitions.registry import SolverDefinitionRegistry
from tga.infrastructure.team_templates.registry import TeamTemplateRegistry
from tga.runtime.manager import Manager
from tga.runtime.scheduler import RuntimeScheduler
from tga.runtime.service import TaskRuntimeService
from tga.sandbox import DockerSandboxProvider, SandboxManager, SandboxdProvider, load_sandbox_config
from tga.sandbox.repository import SandboxInstanceRepository


@dataclass(frozen=True, slots=True)
class Container:
    run_root: Path

    def __init__(self, run_root: str | Path | None = None) -> None:
        object.__setattr__(self, "run_root", resolve_run_root(run_root))

    def evidence_store(self, task_id: str) -> EvidenceStore:
        return EvidenceStore(self.task_root(task_id) / "evidence.db")

    def manager(self, *, store: EvidenceStore | None = None) -> Manager:
        return Manager(
            store=store,
            run_root=self.run_root,
        )

    def runtime_service(self) -> TaskRuntimeService:
        return TaskRuntimeService(run_root=self.run_root, manager=self.manager())

    def scheduler(self) -> RuntimeScheduler:
        service = self.runtime_service()
        return RuntimeScheduler(run_root=self.run_root, run_task=service.run_task)

    def solver_definitions(self) -> SolverDefinitionRegistry:
        return SolverDefinitionRegistry.builtin()

    def team_templates(self) -> TeamTemplateRegistry:
        return TeamTemplateRegistry.builtin(definitions=self.solver_definitions())

    def sandbox_manager(self, task_id: str) -> SandboxManager:
        config, _ = load_sandbox_config()
        providers = {
            "docker_sandbox": DockerSandboxProvider(config),
            "sandboxd": SandboxdProvider(config),
        }
        store = self.evidence_store(task_id)
        return SandboxManager(
            config=config,
            providers=providers,
            repository=SandboxInstanceRepository(store),
            event_repository=store,
        )

    def task_root(self, task_id: str) -> Path:
        return TaskRuntimeService(run_root=self.run_root).task_root(task_id)
