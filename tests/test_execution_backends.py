from __future__ import annotations

from pathlib import Path

import pytest

from tests.runtime_fixtures import task as v6_task
from tga.contracts import (
    ExecutionPolicy,
    HighImpactExecutionPolicy,
    LocalComputeExecutionPolicy,
    NetworkExecutionPolicy,
)
from tga.domain.planning.global_plan import GlobalPlan
from tga.domain.planning.intents import Intent
from tga.evidence.database import utc_now
from tga.evidence.store import EvidenceStore
from tga.infrastructure.persistence import PersistenceBundle
from tga.runtime.kali import KaliSessionManager
from tga.runtime.tooling.execution import (
    ArtifactIngestionService,
    AuthorizedExecutionRequest,
    ExecutionBackendRouter,
    ExecutionResult,
    KaliSandboxBackend,
    ProducedFile,
)
from tga.sandbox.config import SandboxConfig
from tga.sandbox.models import ExecFrame, ExecResult, SandboxHandle, SandboxState
from tga.sandbox.provider import SandboxError


def _config(*, runtime: str = "enforced") -> SandboxConfig:
    return SandboxConfig.model_validate({
        "version": 1,
        "runtime": runtime,
        "docker_sandbox": {
            "template": "example.invalid/template@sha256:" + "f" * 64,
        },
        "sandboxd": {
            "run_root": "/var/lib/tga/runs",
            "allowed_client_uids": [1001],
        },
        "profiles": {
            "ctf-test-v1": {
                "id": "ctf-test-v1",
                "provider": "sandboxd",
                "image": "example.invalid/kali@sha256:" + "a" * 64,
                "network_mode": "target_allowlist",
                "supported_capabilities": ["kali.exec", "kali.session"],
                "allowed_executables": ["python3", "gdb"],
                "session_executables": ["gdb"],
                "toolset_digest": "b" * 64,
                "limits": {"timeout_seconds": 60},
            },
        },
    })


def _task():
    return v6_task(
        id="task_execution",
        name="execution",
        mode="ctf",
        goal="test Kali execution",
        execution_policy=ExecutionPolicy(
            preset="custom",
            network=NetworkExecutionPolicy(
                access="custom",
                interaction="interact",
                custom_origins=["https://example.com"],
                custom_cidrs=["8.8.8.8/32"],
            ),
            local_compute=LocalComputeExecutionPolicy(mode="isolated"),
            high_impact=HighImpactExecutionPolicy(mode="forbidden"),
        ),
    )


def _request(**updates) -> AuthorizedExecutionRequest:
    payload = {
        "action_id": "governed_execution",
        "capability": "kali.exec",
        "backend": "sandbox",
        "arguments": {
            "executable": "python3",
            "argv": ["-c", "print('ok')"],
            "cwd": "scratch",
        },
        "task_id": "task_execution",
        "solver_id": "solver_execution",
        "solver_run_id": "run_execution",
        "intent_id": "intent_execution",
        "execution_profile_id": "ctf-test-v1",
        "sandbox_config_digest": "a" * 64,
        "fencing_token": 7,
        "idempotency_key": "execution-once",
        "resolved_target": "kali:solver_execution:python3:scratch",
    }
    payload.update(updates)
    return AuthorizedExecutionRequest.model_validate(payload)


class _FakeProcess:
    def __init__(self) -> None:
        self.sent: list[bytes] = []
        self.sizes: list[tuple[int, int]] = []
        self.closed = False

    def send(self, data: bytes) -> None:
        if self.closed:
            raise SandboxError("closed", code="PROCESS_STREAM_CLOSED")
        self.sent.append(data)

    def receive(self, _timeout_seconds: float) -> ExecFrame:
        raise TimeoutError

    def resize(self, cols: int, rows: int) -> None:
        self.sizes.append((cols, rows))

    def close(self) -> None:
        self.closed = True


class _FakeManager:
    def __init__(self, config: SandboxConfig, workspace: Path) -> None:
        self.config = config
        self.workspace = workspace
        self.acquire_calls: list[dict] = []
        self.specs = []
        self.process = _FakeProcess()

    def acquire(self, **values):
        self.acquire_calls.append(values)
        return SandboxHandle(
            instance_id="sandbox_execution",
            task_id=values["task_id"],
            solver_id=values["solver_id"],
            solver_run_id=values["solver_run_id"],
            profile_id=values["profile_id"],
            provider="sandboxd",
            config_digest=self.config.digest,
            image_digest="a" * 64,
            toolset_digest="b" * 64,
            fencing_token=values["fencing_token"],
            state=SandboxState.READY,
        )

    def exec(self, _handle, spec):
        self.specs.append(spec)
        output = self.workspace / "outputs" / "result.txt"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("ok", encoding="utf-8")
        return iter(()), ExecResult(exit_code=0, stdout=b"completed\n")

    def open_process(self, _handle, spec):
        self.specs.append(spec)
        return self.process


def test_authorized_request_is_frozen_and_carries_execution_identity() -> None:
    request = _request()
    assert request.solver_run_id == "run_execution"
    assert request.fencing_token == 7
    assert request.execution_profile_id == "ctf-test-v1"
    with pytest.raises(Exception):
        request.arguments = {}


def test_kali_backend_fails_closed_when_runtime_is_disabled(tmp_path: Path) -> None:
    config = _config(runtime="disabled")
    manager = _FakeManager(config, tmp_path)
    result = KaliSandboxBackend(manager=manager, task=_task(), workspace=tmp_path).execute(
        _request(sandbox_config_digest=config.digest)
    )
    assert result.status == "blocked"
    assert result.error and result.error.code == "SANDBOX_RUNTIME_DISABLED"
    assert manager.acquire_calls == []


def test_kali_exec_uses_typed_argv_and_collects_outputs(tmp_path: Path) -> None:
    config = _config()
    manager = _FakeManager(config, tmp_path)
    result = KaliSandboxBackend(manager=manager, task=_task(), workspace=tmp_path).execute(
        _request(sandbox_config_digest=config.digest)
    )
    assert result.status == "succeeded"
    assert manager.specs[0].argv == ("python3", "-c", "print('ok')")
    assert result.produced_files == (
        ProducedFile(path=str((tmp_path / "outputs" / "result.txt").resolve())),
    )
    assert result.execution_metadata["kali_profile_id"] == "ctf-test-v1"


@pytest.mark.parametrize(
    ("updates", "code"),
    [
        ({"execution_profile_id": None}, "KALI_PROFILE_NOT_ASSIGNED"),
        ({"arguments": {"executable": "bash"}}, "EXECUTABLE_NOT_ALLOWED"),
        ({"arguments": {"executable": "python3", "cwd": "../other"}}, "SANDBOX_EXECUTION_FAILED"),
        ({"arguments": {"executable": "python3", "argv": ["../secret"]}}, "SANDBOX_EXECUTION_FAILED"),
    ],
)
def test_kali_exec_rejects_unassigned_profile_allowlist_and_path_escape(
    tmp_path: Path, updates: dict, code: str
) -> None:
    config = _config()
    manager = _FakeManager(config, tmp_path)
    result = KaliSandboxBackend(manager=manager, task=_task(), workspace=tmp_path).execute(
        _request(sandbox_config_digest=config.digest, **updates)
    )
    assert result.error and result.error.code == code


def test_kali_exec_applies_only_declared_task_network_targets(
    tmp_path: Path, monkeypatch
) -> None:
    config = _config()
    manager = _FakeManager(config, tmp_path)
    monkeypatch.setattr(
        "tga.runtime.tooling.execution.backends.authorize_url",
        lambda *_args, **_kwargs: ["93.184.216.34"],
    )
    result = KaliSandboxBackend(manager=manager, task=_task(), workspace=tmp_path).execute(
        _request(
            arguments={
                "executable": "python3",
                "network_targets": [{"host": "example.com", "ports": [443]}],
            },
            sandbox_config_digest=config.digest,
        )
    )
    assert result.status == "succeeded"
    assert manager.specs[0].network_grants[0].cidr == "93.184.216.34/32"
    assert manager.specs[0].network_grants[0].ports == (443,)


def test_kali_session_enforces_owner_resize_and_close(tmp_path: Path) -> None:
    config = _config()
    manager = _FakeManager(config, tmp_path)
    sessions = KaliSessionManager(manager)
    backend = KaliSandboxBackend(
        manager=manager, task=_task(), workspace=tmp_path, sessions=sessions
    )
    opened = backend.execute(_request(
        capability="kali.session",
        arguments={"operation": "open", "executable": "gdb"},
        sandbox_config_digest=config.digest,
    ))
    session_id = opened.structured_result["session_id"]
    resized = backend.execute(_request(
        capability="kali.session",
        arguments={"operation": "resize", "session_id": session_id, "cols": 132, "rows": 44},
        sandbox_config_digest=config.digest,
    ))
    assert resized.status == "succeeded"
    assert manager.process.sizes == [(132, 44)]

    foreign = backend.execute(_request(
        capability="kali.session",
        solver_id="solver_other",
        arguments={"operation": "write", "session_id": session_id, "input": "run\n"},
        sandbox_config_digest=config.digest,
    ))
    assert foreign.error and foreign.error.code == "SESSION_OWNER_MISMATCH"

    closed = backend.execute(_request(
        capability="kali.session",
        arguments={"operation": "close", "session_id": session_id},
        sandbox_config_digest=config.digest,
    ))
    assert closed.status == "succeeded"
    assert manager.process.closed is True
    after_close = backend.execute(_request(
        capability="kali.session",
        arguments={"operation": "write", "session_id": session_id, "input": "run\n"},
        sandbox_config_digest=config.digest,
    ))
    assert after_close.error and after_close.error.code == "SESSION_NOT_FOUND"


def test_artifact_ingestion_is_the_execution_output_boundary(tmp_path: Path) -> None:
    task = _task()
    store = EvidenceStore(tmp_path / "evidence.db")
    try:
        store.create_task(task)
        now = utc_now()
        PersistenceBundle(store).plans.save_global_plan(GlobalPlan(
            id=f"global_plan_{task.id}",
            task_id=task.id,
            version=1,
            status="active",
            intents=[Intent(
                id="intent_execution",
                task_id=task.id,
                kind="validation",
                title="Execution intent",
                objective="produce execution artifacts",
                created_at=now,
                updated_at=now,
            )],
            created_at=now,
            updated_at=now,
        ))
        workspace = tmp_path / "runs" / task.id / "workspace" / "solver-runs" / "run_execution"
        output = workspace / "outputs" / "result.txt"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("artifact body", encoding="utf-8")
        request = _request(sandbox_config_digest="b" * 64)
        result = ArtifactIngestionService(
            task=task, store=store, run_root=tmp_path / "runs"
        ).ingest(request, ExecutionResult(
            action_id=request.action_id,
            status="succeeded",
            produced_files=(ProducedFile(path=str(output)),),
            execution_metadata={"backend": "sandbox"},
        ))
        assert len(result.artifact_ids) == 2
        records = [store.get_artifact(item) for item in result.artifact_ids]
        assert {record.kind for record in records if record} == {"file", "tool_output"}
    finally:
        store.close()


def test_backend_router_rejects_an_unconfigured_backend() -> None:
    result = ExecutionBackendRouter({}).execute(_request())
    assert result.status == "blocked"
    assert result.error and result.error.code == "EXECUTION_BACKEND_UNAVAILABLE"
