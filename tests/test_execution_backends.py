from __future__ import annotations

import base64
import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from tga.capabilities.registry import build_default_registry
from tga.contracts import (
    ExecutionPolicy,
    HighImpactExecutionPolicy,
    LocalComputeExecutionPolicy,
    NetworkExecutionPolicy,
    TGATask,
)
from tga.evidence.store import EvidenceStore
from tga.infrastructure.solver_definitions.registry import SolverDefinitionRegistry
from tga.runtime.tooling.catalog import RuntimeToolCatalog
from tga.runtime.tooling.execution import (
    ArtifactIngestionService,
    AuthorizedExecutionRequest,
    ExecutionBackendRouter,
    ExecutionResult,
    KaliSandboxBackend,
    ProducedFile,
)
from tga.runtime.tooling.execution.adapters import process_spec
from tga.sandbox.config import SandboxConfig
from tga.sandbox.models import ExecResult, SandboxHandle, SandboxState


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
            "ctf-web-v1": {
                "id": "ctf-web-v1",
                "provider": "sandboxd",
                "image": "example.invalid/kali@sha256:" + "a" * 64,
                "network_mode": "target_allowlist",
                "allowed_executables": ["python3", "curl", "ffuf", "nuclei"],
                "toolset_digest": "b" * 64,
                "limits": {"timeout_seconds": 60},
            },
        },
        "tools": {},
    })


def _task() -> TGATask:
    return TGATask(
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
        "capability": "workspace.write",
        "backend": "sandbox",
        "arguments": {"relative_path": "outputs/result.txt", "content": "ok"},
        "task_id": "task_execution",
        "solver_id": "solver_execution",
        "solver_run_id": "run_execution",
        "intent_id": "intent_execution",
        "execution_profile_id": "ctf-web-v1",
        "sandbox_config_digest": "a" * 64,
        "fencing_token": 7,
        "idempotency_key": "execution-once",
        "resolved_target": "workspace:solver_execution:outputs/result.txt",
    }
    payload.update(updates)
    return AuthorizedExecutionRequest.model_validate(payload)


class _FakeManager:
    def __init__(self, config: SandboxConfig, workspace: Path) -> None:
        self.config = config
        self.workspace = workspace
        self.acquire_calls: list[dict] = []
        self.specs = []

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

    def exec(self, handle, spec):
        del handle
        self.specs.append(spec)
        if spec.argv[0] == "python3" and "outputs/result.txt" in spec.argv:
            output = self.workspace / "outputs" / "result.txt"
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text("ok", encoding="utf-8")
        return iter(()), ExecResult(exit_code=0, stdout=b"completed\n")


def test_catalog_routes_each_capability_to_one_backend() -> None:
    registry = build_default_registry()
    tool_names = {
        f"tga_{item['name'].replace('.', '_')}": item["name"]
        for item in registry.snapshot()["capabilities"]
    }
    remote = SimpleNamespace(
        provider_name="remote_search",
        server_id="external",
        method="search",
        description="remote search",
        input_schema={"type": "object", "properties": {}},
    )
    catalog = RuntimeToolCatalog.from_runtime(
        task=_task(),
        solver_definition=SolverDefinitionRegistry.builtin().require("ctf-web-solver"),
        registry=registry, tool_names=tool_names,
        mcp_snapshot=SimpleNamespace(routes=(remote,)),
    )
    by_capability = {}
    for entry in catalog.entries:
        by_capability.setdefault(entry.capability, set()).add(entry.backend)

    assert by_capability["workspace.shell"] == {"sandbox"}
    assert by_capability["workspace.python"] == {"sandbox"}
    assert by_capability["http.request"] == {"sandbox"}
    assert by_capability["nmap.scan"] == {"sandbox"}
    assert by_capability["workspace.read"] == {"host_retrieval"}
    assert by_capability["create_intent"] == {"host_control"}
    assert by_capability["mcp:external:search"] == {"remote_mcp"}


def test_authorized_request_is_frozen_and_carries_execution_identity() -> None:
    request = _request()
    assert request.solver_run_id == "run_execution"
    assert request.fencing_token == 7
    assert request.execution_profile_id == "ctf-web-v1"
    assert request.sandbox_config_digest == "a" * 64
    with pytest.raises(Exception):
        request.arguments = {}


def test_kali_backend_fails_closed_when_runtime_is_disabled(tmp_path: Path) -> None:
    config = _config(runtime="disabled")
    manager = _FakeManager(config, tmp_path)
    result = KaliSandboxBackend(
        manager=manager, task=_task(), workspace=tmp_path
    ).execute(_request(sandbox_config_digest=config.digest))

    assert result.status == "blocked"
    assert result.error and result.error.code == "SANDBOX_RUNTIME_DISABLED"
    assert manager.acquire_calls == []


def test_kali_backend_executes_typed_process_and_collects_outputs(tmp_path: Path) -> None:
    config = _config()
    manager = _FakeManager(config, tmp_path)
    result = KaliSandboxBackend(
        manager=manager, task=_task(), workspace=tmp_path
    ).execute(_request(sandbox_config_digest=config.digest))

    assert result.status == "succeeded"
    assert manager.acquire_calls[0]["profile_id"] == "ctf-web-v1"
    assert manager.acquire_calls[0]["solver_run_id"] == "run_execution"
    assert manager.acquire_calls[0]["fencing_token"] == 7
    assert manager.specs[0].argv[0:3] == ("python3", "-I", "-c")
    assert result.produced_files == (
        ProducedFile(path=str((tmp_path / "outputs" / "result.txt").resolve())),
    )
    assert result.execution_metadata["sandbox_instance_id"] == "sandbox_execution"
    assert result.execution_metadata["image_digest"] == "a" * 64


def test_kali_backend_rejects_missing_profile_without_default_fallback(tmp_path: Path) -> None:
    config = _config()
    manager = _FakeManager(config, tmp_path)
    result = KaliSandboxBackend(manager=manager, task=_task(), workspace=tmp_path).execute(
        _request(execution_profile_id=None, sandbox_config_digest=config.digest)
    )

    assert result.error and result.error.code == "SANDBOX_PROFILE_NOT_ASSIGNED"
    assert manager.acquire_calls == []


def test_sandbox_exec_enforces_profile_executable_allowlist(tmp_path: Path) -> None:
    config = _config()
    manager = _FakeManager(config, tmp_path)
    result = KaliSandboxBackend(manager=manager, task=_task(), workspace=tmp_path).execute(
        _request(
            capability="sandbox.exec",
            arguments={"executable": "gdb", "argv": ["--version"]},
            sandbox_config_digest=config.digest,
        )
    )

    assert result.error and result.error.code == "EXECUTABLE_NOT_ALLOWED"
    assert manager.acquire_calls == []


def test_kali_backend_applies_request_scoped_network_grant(
    tmp_path: Path, monkeypatch
) -> None:
    config = _config()
    manager = _FakeManager(config, tmp_path)
    monkeypatch.setattr(
        "tga.runtime.tooling.execution.backends.authorize_url",
        lambda *_args, **_kwargs: ["93.184.216.34"],
    )
    request = _request(
        capability="http.request",
        arguments={"url": "https://example.com/check", "method": "GET"},
        resolved_target="https://example.com/check",
        sandbox_config_digest=config.digest,
    )
    result = KaliSandboxBackend(
        manager=manager, task=_task(), workspace=tmp_path
    ).execute(request)

    assert result.status == "succeeded"
    assert manager.specs[0].network_grants[0].cidr == "93.184.216.34/32"
    assert manager.specs[0].network_grants[0].ports == (443,)
    frozen = json.loads(base64.b64decode(manager.specs[0].argv[-1]))
    assert frozen["_approved_addresses"] == ["93.184.216.34"]


def test_shell_and_python_cannot_bypass_typed_capabilities() -> None:
    assert process_spec(
        "workspace.shell", {"command": "strings inputs/sample.bin"}, 30
    ).argv[-2:] == ("strings", "inputs/sample.bin")
    for command in (
        "nmap 8.8.8.8", "curl https://example.com", "cat a | grep b",
    ):
        with pytest.raises(ValueError):
            process_spec("workspace.shell", {"command": command}, 30)

    spec = process_spec(
        "workspace.python",
        {"source": "import subprocess; subprocess.run(['echo', 'blocked'])"},
        30,
    )
    completed = subprocess.run(spec.argv, capture_output=True, text=True, check=False)
    assert completed.returncode != 0
    assert "workspace.python audit policy blocked subprocess.Popen" in completed.stderr


def test_workspace_python_cannot_read_a_sibling_solver(tmp_path: Path) -> None:
    current = tmp_path / "workspace" / "solvers" / "solver_current"
    sibling = tmp_path / "workspace" / "solvers" / "solver_sibling"
    current.mkdir(parents=True)
    sibling.mkdir(parents=True)
    (sibling / "secret.txt").write_text("secret", encoding="utf-8")
    spec = process_spec(
        "workspace.python",
        {"source": "from pathlib import Path\nprint(Path('../solver_sibling/secret.txt').read_text())"},
        30,
    )
    completed = subprocess.run(
        spec.argv, cwd=current, capture_output=True, text=True, check=False
    )
    assert completed.returncode != 0
    assert "outside the Solver workspace" in completed.stderr


def test_kali_backend_rejects_workspace_symlink_escape(tmp_path: Path) -> None:
    config = _config()
    sibling = tmp_path.parent / "solver_sibling"
    sibling.mkdir()
    (sibling / "secret.txt").write_text("secret", encoding="utf-8")
    (tmp_path / "outputs").mkdir()
    try:
        (tmp_path / "outputs" / "link.txt").symlink_to(sibling / "secret.txt")
    except OSError:
        pytest.skip("symlink creation is unavailable")
    manager = _FakeManager(config, tmp_path)
    result = KaliSandboxBackend(
        manager=manager, task=_task(), workspace=tmp_path
    ).execute(_request(
        arguments={"relative_path": "outputs/link.txt", "content": "overwrite"},
        sandbox_config_digest=config.digest,
    ))

    assert result.status == "failed"
    assert result.error and result.error.code == "SANDBOX_EXECUTION_FAILED"
    assert manager.specs == []
    assert (sibling / "secret.txt").read_text(encoding="utf-8") == "secret"


def test_artifact_ingestion_is_the_single_execution_output_boundary(tmp_path: Path) -> None:
    task = _task()
    store = EvidenceStore(tmp_path / "evidence.db")
    try:
        store.create_task(task)
        workspace = (
            tmp_path / "runs" / task.id / "workspace" / "solver-runs" / "run_execution"
        )
        output = workspace / "outputs" / "result.txt"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("artifact body", encoding="utf-8")
        request = _request(sandbox_config_digest="b" * 64)
        service = ArtifactIngestionService(
            task=task, store=store, run_root=tmp_path / "runs"
        )
        result = service.ingest(
            request,
            ExecutionResult(
                action_id=request.action_id,
                status="succeeded",
                stdout_preview="completed",
                produced_files=(ProducedFile(path=str(output)),),
                execution_metadata={"backend": "sandbox"},
            ),
        )

        assert len(result.artifact_ids) == 2
        records = [store.get_artifact(item) for item in result.artifact_ids]
        assert all(record is not None for record in records)
        assert {record.kind for record in records if record is not None} == {
            "file", "tool_output",
        }
        assert all(
            record.provenance["solver_run_id"] == "run_execution"
            for record in records if record is not None
        )
        events = store.list_agent_events(task.id, limit=None)
        assert [event.type for event in events[-2:]] == [
            "ARTIFACTS_INGESTED", "EXECUTION_BACKEND_COMPLETED",
        ]
    finally:
        store.close()


def test_backend_router_rejects_an_unconfigured_backend() -> None:
    result = ExecutionBackendRouter({}).execute(_request())
    assert result.status == "blocked"
    assert result.error and result.error.code == "EXECUTION_BACKEND_UNAVAILABLE"


def test_local_execution_modules_do_not_spawn_host_processes() -> None:
    root = Path(__file__).parents[1]
    runtime = (root / "tga" / "capabilities" / "runtime.py").read_text(
        encoding="utf-8"
    )
    assert "import subprocess" not in runtime
    assert "execute_http" not in runtime
    assert "urllib.request" not in runtime

    mcp = (root / "config" / "mcp.json").read_text(encoding="utf-8")
    assert '"servers": {}' in mcp
    assert hashlib.sha256(mcp.encode()).hexdigest()
