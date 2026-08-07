from __future__ import annotations

import json
import importlib.util
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tga.evidence.store import EvidenceStore
from tga.sandbox.config import (
    SandboxConfig,
    load_sandbox_config,
)
from tga.sandbox.readiness import (
    ensure_kali_profile_ready,
    inspect_kali_runtime_readiness,
)
from tga.sandbox.docker_provider import DockerSandboxProvider
from tga.sandbox.manager import SandboxManager
from tga.sandbox.models import ProcessSpec, SandboxHandle, SandboxState
from tga.sandbox.provider import SandboxError
from tga.sandbox.repository import SandboxInstanceRepository
from tga.tools.mcp_config import MCPServerConfig, load_mcp_config
from tga.tools.mcp_sdk import MCPClientConfigurationError, discover_server


def _config(**updates) -> SandboxConfig:
    payload = {
        "version": 1,
        "runtime": "enforced",
        "docker_sandbox": {
            "template": "docker.io/example/shell@sha256:" + "f" * 64,
        },
        "sandboxd": {
            "run_root": "/var/lib/tga/runs",
            "allowed_client_uids": [1001],
        },
        "profiles": {
            "offline-analysis": {
                "id": "offline-analysis",
                "provider": "docker_sandbox",
                "image": "example.invalid/offline@sha256:" + "0" * 64,
                "network_mode": "none",
                "allowed_executables": ["file", "binwalk"],
                "toolset_digest": "a" * 64,
            },
            "raw-network": {
                "id": "raw-network",
                "provider": "sandboxd",
                "image": "example.invalid/network@sha256:" + "1" * 64,
                "network_mode": "target_allowlist",
                "allow_net_raw": True,
                "allowed_executables": ["nmap"],
                "toolset_digest": "b" * 64,
            },
        },
    }
    payload.update(updates)
    return SandboxConfig.model_validate(payload)


class FakeProvider:
    provider_name = "docker_sandbox"

    def __init__(self, config):
        self.config = config
        self.calls = 0
        self.destroy_calls = 0
        self.fail_destroy_once = False
        self.replacement_instance_id = None

    def acquire(self, **values):
        self.calls += 1
        return SandboxHandle(
            instance_id=self.replacement_instance_id or f"tga-instance-{values['solver_run_id']}",
            task_id=values["task_id"],
            solver_id=values["solver_id"],
            solver_run_id=values["solver_run_id"],
            profile_id=values["profile_id"],
            provider="docker_sandbox",
            config_digest=self.config.digest,
            image_digest="0" * 64,
            fencing_token=values["fencing_token"],
            state=SandboxState.READY,
        )

    def destroy(self, handle):
        self.destroy_calls += 1
        if self.fail_destroy_once:
            self.fail_destroy_once = False
            raise RuntimeError("transient destroy failure")
        return None

    def release(self, handle):
        return None


class FakeRepository:
    def __init__(self):
        self.handles = {}
        self.transitions = []

    def get_active(self, *, task_id, solver_id, solver_run_id):
        return self.handles.get((task_id, solver_id, solver_run_id))

    def put(self, handle):
        self.handles[(handle.task_id, handle.solver_id, handle.solver_run_id)] = handle

    def transition(self, instance_id, state, *, destroy_after=None):
        self.transitions.append((instance_id, state, destroy_after))
        for key, handle in tuple(self.handles.items()):
            if handle.instance_id == instance_id:
                self.handles[key] = handle.model_copy(update={"state": state})


def test_manager_fails_closed_when_provider_is_missing() -> None:
    manager = SandboxManager(config=_config(), providers={})
    with pytest.raises(SandboxError) as error:
        manager.acquire(
            task_id="task-1",
            solver_id="solver-1",
            solver_run_id="run-1",
            profile_id="offline-analysis",
            fencing_token=1,
            idempotency_key="key",
        )
    assert error.value.code == "PROVIDER_UNAVAILABLE"


def test_manager_rejects_disabled_runtime() -> None:
    config = _config(runtime="disabled")
    manager = SandboxManager(config=config, providers={"docker_sandbox": FakeProvider(config)})
    with pytest.raises(SandboxError) as error:
        manager.acquire(
            task_id="task-1",
            solver_id="solver-1",
            solver_run_id="run-1",
            profile_id="offline-analysis",
            fencing_token=1,
            idempotency_key="key",
        )
    assert error.value.code == "KALI_PROFILE_NOT_READY"
    assert error.value.reason == "runtime_disabled"


def test_manager_isolates_solver_runs_and_reuses_only_the_same_run() -> None:
    config = _config()
    provider = FakeProvider(config)
    repository = FakeRepository()
    manager = SandboxManager(
        config=config,
        providers={"docker_sandbox": provider},
        repository=repository,
    )

    first = manager.acquire(
        task_id="task-1", solver_id="solver-1", solver_run_id="run-1",
        profile_id="offline-analysis", fencing_token=1, idempotency_key="first",
    )
    reused = manager.acquire(
        task_id="task-1", solver_id="solver-1", solver_run_id="run-1",
        profile_id="offline-analysis", fencing_token=2, idempotency_key="reuse",
    )
    second = manager.acquire(
        task_id="task-1", solver_id="solver-2", solver_run_id="run-2",
        profile_id="offline-analysis", fencing_token=1, idempotency_key="second",
    )

    assert reused.instance_id == first.instance_id
    assert reused.fencing_token == 2
    assert second.instance_id != first.instance_id
    assert provider.calls == 3


def test_manager_revalidates_provider_even_with_same_fencing_token() -> None:
    config = _config()
    provider = FakeProvider(config)
    repository = FakeRepository()
    manager = SandboxManager(
        config=config,
        providers={"docker_sandbox": provider},
        repository=repository,
    )
    values = {
        "task_id": "task-1", "solver_id": "solver-1", "solver_run_id": "run-1",
        "profile_id": "offline-analysis", "fencing_token": 1,
    }
    manager.acquire(**values, idempotency_key="first")
    manager.acquire(**values, idempotency_key="health-check")
    assert provider.calls == 2


def test_manager_accepts_healthy_replacement_for_missing_external_sandbox() -> None:
    config = _config()
    provider = FakeProvider(config)
    repository = FakeRepository()
    manager = SandboxManager(
        config=config,
        providers={"docker_sandbox": provider},
        repository=repository,
    )
    original = manager.acquire(
        task_id="task-1", solver_id="solver-1", solver_run_id="run-1",
        profile_id="offline-analysis", fencing_token=1, idempotency_key="first",
    )
    provider.replacement_instance_id = "tga-recovered-run-1"
    recovered = manager.acquire(
        task_id="task-1", solver_id="solver-1", solver_run_id="run-1",
        profile_id="offline-analysis", fencing_token=1, idempotency_key="recover",
    )
    assert recovered.instance_id == "tga-recovered-run-1"
    assert any(
        instance_id == original.instance_id and state == SandboxState.DESTROYED
        for instance_id, state, _ in repository.transitions
    )


def test_destroy_failure_returns_instance_to_immediate_retry_state() -> None:
    config = _config()
    provider = FakeProvider(config)
    repository = FakeRepository()
    manager = SandboxManager(
        config=config,
        providers={"docker_sandbox": provider},
        repository=repository,
    )
    handle = manager.acquire(
        task_id="task-1", solver_id="solver-1", solver_run_id="run-1",
        profile_id="offline-analysis", fencing_token=1, idempotency_key="first",
    )
    provider.fail_destroy_once = True
    with pytest.raises(RuntimeError, match="transient destroy failure"):
        manager.destroy(handle)
    assert repository.transitions[-1][1] == SandboxState.RELEASED
    assert repository.transitions[-1][2] is not None
    manager.destroy(handle)
    assert repository.transitions[-1][1] == SandboxState.DESTROYED
    assert provider.destroy_calls == 2


def test_zero_grace_release_destroys_immediately_without_poisoning_task_completion() -> None:
    config = _config(terminal_grace_seconds=0)
    provider = FakeProvider(config)
    repository = FakeRepository()
    manager = SandboxManager(
        config=config,
        providers={"docker_sandbox": provider},
        repository=repository,
    )
    handle = manager.acquire(
        task_id="task-1", solver_id="solver-1", solver_run_id="run-1",
        profile_id="offline-analysis", fencing_token=1, idempotency_key="first",
    )
    provider.fail_destroy_once = True
    manager.release(handle)
    assert repository.transitions[-1][1] == SandboxState.RELEASED
    assert repository.transitions[-1][2] is not None
    manager.destroy(handle)
    assert repository.transitions[-1][1] == SandboxState.DESTROYED


def test_unresolved_unused_profile_does_not_block_ready_profile() -> None:
    payload = _config().model_dump(mode="json")
    payload["profiles"]["raw-network"]["image"] = (
        "example.invalid/network@sha256:REPLACE_WITH_RELEASE_DIGEST"
    )
    config = SandboxConfig.model_validate(payload)
    provider = FakeProvider(config)
    manager = SandboxManager(
        config=config,
        providers={"docker_sandbox": provider},
    )

    handle = manager.acquire(
        task_id="task-1",
        solver_id="solver-1",
        solver_run_id="run-1",
        profile_id="offline-analysis",
        fencing_token=1,
        idempotency_key="ready-profile",
    )

    assert handle.profile_id == "offline-analysis"
    assert provider.calls == 1


def test_unresolved_selected_profile_is_blocked_before_provider_call() -> None:
    payload = _config().model_dump(mode="json")
    payload["profiles"]["offline-analysis"]["image"] = (
        "example.invalid/offline@sha256:REPLACE_WITH_RELEASE_DIGEST"
    )
    config = SandboxConfig.model_validate(payload)
    provider = FakeProvider(config)
    manager = SandboxManager(
        config=config,
        providers={"docker_sandbox": provider},
    )

    with pytest.raises(SandboxError) as error:
        manager.acquire(
            task_id="task-1",
            solver_id="solver-1",
            solver_run_id="run-1",
            profile_id="offline-analysis",
            fencing_token=1,
            idempotency_key="unresolved-profile",
        )

    assert error.value.code == "KALI_PROFILE_NOT_READY"
    assert error.value.profile_id == "offline-analysis"
    assert error.value.reason == "unresolved_image_digest"
    assert provider.calls == 0


def test_manager_rejects_profile_change_within_solver_run() -> None:
    config = _config()
    provider = FakeProvider(config)
    repository = FakeRepository()
    manager = SandboxManager(
        config=config,
        providers={"docker_sandbox": provider},
        repository=repository,
    )
    manager.acquire(
        task_id="task-1", solver_id="solver-1", solver_run_id="run-1",
        profile_id="offline-analysis", fencing_token=1, idempotency_key="first",
    )

    with pytest.raises(SandboxError) as error:
        manager.acquire(
            task_id="task-1", solver_id="solver-1", solver_run_id="run-1",
            profile_id="raw-network", fencing_token=2, idempotency_key="changed",
        )

    assert error.value.code == "SOLVER_RUN_SANDBOX_CONFLICT"


def test_docker_provider_uses_host_profile_and_no_docker_flags(tmp_path: Path) -> None:
    config = _config(
        docker_sandbox={
            "task_root": str(tmp_path),
            "template": "docker.io/example/shell@sha256:" + "f" * 64,
        }
    )
    commands: list[list[str]] = []

    def runner(command, **kwargs):
        commands.append(command)
        if command[1:] == ["version"]:
            return subprocess.CompletedProcess(command, 0, b"sbx version 0.34.0\n", b"")
        if command[1:] == ["ls", "--json"]:
            return subprocess.CompletedProcess(command, 0, b"[]", b"")
        if command[1:3] == ["exec", "--workdir"] and command[-1] == "pwd":
            return subprocess.CompletedProcess(command, 0, b"/sandbox/workspace\n", b"")
        return subprocess.CompletedProcess(command, 0, b"", b"")

    provider = DockerSandboxProvider(config, runner=runner)
    handle = provider.acquire(
        task_id="task-1",
        solver_id="solver-1",
        solver_run_id="run-1",
        profile_id="offline-analysis",
        fencing_token=2,
        idempotency_key="key",
    )
    assert handle.provider == "docker_sandbox"
    create = next(command for command in commands if command[1] == "create")
    assert create[0:4] == ["sbx", "create", "--name", handle.instance_id]
    assert create[-2] == "shell"
    assert "--template" in create
    provider.exec(handle, ProcessSpec(argv=("file", "sample.bin")))
    inner = commands[-1]
    assert inner[0:3] == ["sbx", "exec", handle.instance_id]
    assert "--network" in inner and "none" in inner
    assert "--cap-drop" in inner and "ALL" in inner
    assert "--read-only" in inner
    assert "--privileged" not in inner
    assert "/var/run/docker.sock" not in " ".join(inner)


def test_docker_provider_rejects_incompatible_sbx_version() -> None:
    config = _config()

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, b"sbx version 0.35.0\n", b"")

    provider = DockerSandboxProvider(config, runner=runner)
    with pytest.raises(SandboxError) as error:
        provider.acquire(
            task_id="task-1",
            solver_id="solver-1",
            solver_run_id="run-1",
            profile_id="offline-analysis",
            fencing_token=2,
            idempotency_key="key",
        )
    assert error.value.code == "PROVIDER_VERSION_INCOMPATIBLE"


def test_docker_provider_uses_profile_image_for_every_execution(tmp_path: Path) -> None:
    config = _config(
        docker_sandbox={
            "task_root": str(tmp_path),
            "template": "docker.io/example/shell@sha256:" + "f" * 64,
        }
    )
    commands: list[list[str]] = []

    def runner(command, **kwargs):
        commands.append(command)
        if command[1:] == ["version"]:
            return subprocess.CompletedProcess(command, 0, b"0.34.0", b"")
        if command[1:] == ["ls", "--json"]:
            return subprocess.CompletedProcess(command, 0, b"[]", b"")
        if command[1:3] == ["exec", "--workdir"] and command[-1] == "pwd":
            return subprocess.CompletedProcess(command, 0, b"/sandbox/workspace\n", b"")
        return subprocess.CompletedProcess(command, 0, b"", b"")

    provider = DockerSandboxProvider(config, runner=runner)
    handle = provider.acquire(
        task_id="task-1",
        solver_id="solver-1",
        solver_run_id="run-1",
        profile_id="offline-analysis",
        fencing_token=2,
        idempotency_key="key",
    )
    provider.exec(handle, ProcessSpec(argv=("binwalk", "sample.bin")))
    profile_image = config.profile("offline-analysis").image
    assert profile_image in commands[-1]
    assert commands[-1][-2:] == ["binwalk", "sample.bin"]


def test_docker_provider_applies_and_verifies_scoped_web_policy(tmp_path: Path) -> None:
    payload = _config(
        docker_sandbox={
            "task_root": str(tmp_path),
            "template": "docker.io/example/shell@sha256:" + "f" * 64,
        }
    ).model_dump(mode="json")
    payload["profiles"]["web-assessment"] = {
        "id": "web-assessment",
        "provider": "docker_sandbox",
        "image": "example.invalid/web@sha256:" + "3" * 64,
        "network_mode": "public_http",
        "web_allow_hosts": ["example.com:443"],
        "toolset_digest": "c" * 64,
    }
    config = SandboxConfig.model_validate(payload)
    commands: list[list[str]] = []

    def runner(command, **kwargs):
        commands.append(command)
        if command[1:] == ["version"]:
            return subprocess.CompletedProcess(command, 0, b"0.34.0", b"")
        if command[1:] == ["ls", "--json"]:
            return subprocess.CompletedProcess(command, 0, b"[]", b"")
        if command[1:3] == ["exec", "--workdir"] and command[-1] == "pwd":
            return subprocess.CompletedProcess(command, 0, b"/sandbox/workspace\n", b"")
        if command[1:3] == ["policy", "ls"]:
            return subprocess.CompletedProcess(
                command, 0, b'{"resources":["example.com:443"]}', b""
            )
        return subprocess.CompletedProcess(command, 0, b"", b"")

    provider = DockerSandboxProvider(config, runner=runner)
    provider.acquire(
        task_id="task-web",
        solver_id="solver-1",
        solver_run_id="run-1",
        profile_id="web-assessment",
        fencing_token=1,
        idempotency_key="web",
    )
    allow = next(command for command in commands if command[1:4] == ["policy", "allow", "network"])
    assert "--sandbox" in allow
    assert allow[-1] == "example.com:443"


def test_schema_adds_sandbox_instances(tmp_path: Path) -> None:
    store = EvidenceStore(tmp_path / "evidence.db")
    try:
        row = store.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='sandbox_instances'"
        ).fetchone()
        assert row is not None
        columns = {
            item["name"]
            for item in store.conn.execute("PRAGMA table_info(sandbox_instances)").fetchall()
        }
        assert {"solver_run_id", "image_digest", "toolset_digest"} <= columns
        index = store.conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' "
            "AND name='uq_active_solver_run_sandbox'"
        ).fetchone()
        assert index is not None and "solver_run_id" in index["sql"]
    finally:
        store.close()


def test_repository_releases_terminal_and_expired_solver_run_sandboxes(tmp_path: Path) -> None:
    store = EvidenceStore(tmp_path / "evidence.db")
    try:
        # This is a focused lifecycle-state test; parent aggregates are not
        # relevant to the join being exercised.
        store.conn.execute("PRAGMA foreign_keys=OFF")
        for run_id, state, lease in (
            ("run-terminal", "completed", None),
            ("run-expired", "running", "2026-08-07T00:00:00Z"),
            ("run-approval", "waiting_approval", None),
        ):
            store.conn.execute(
                "INSERT INTO solver_runs("
                "id,task_id,solver_id,orchestration_role,state,attempt,turn_count,max_turns,"
                "fencing_token,lease_expires_at,version,payload_json,created_at,updated_at"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    run_id, "task-1", "solver-1", "worker", state, 1, 0, 1,
                    1, lease, 1, "{}", "2026-08-07T00:00:00Z", "2026-08-07T00:00:00Z",
                ),
            )
            store.conn.execute(
                "INSERT INTO sandbox_instances("
                "instance_id,task_id,solver_id,solver_run_id,profile_id,provider,config_digest,"
                "image_digest,toolset_digest,fencing_token,state,created_at,updated_at"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "instance-" + run_id, "task-1", "solver-1", run_id,
                    "offline-analysis", "sandboxd", "c" * 64, "d" * 64, "e" * 64,
                    1, "ready", "2026-08-07T00:00:00Z", "2026-08-07T00:00:00Z",
                ),
            )
        store.conn.commit()
        released = SandboxInstanceRepository(store).release_orphans("2026-08-07T01:00:00Z")
        assert {item.solver_run_id for item in released} == {"run-terminal", "run-expired"}
        rows = {
            row["solver_run_id"]: (row["state"], row["destroy_after"])
            for row in store.conn.execute(
                "SELECT solver_run_id,state,destroy_after FROM sandbox_instances"
            ).fetchall()
        }
        assert rows["run-terminal"] == ("released", "2026-08-07T01:00:00Z")
        assert rows["run-expired"] == ("released", "2026-08-07T01:00:00Z")
        assert rows["run-approval"] == ("ready", None)
    finally:
        store.close()


def test_mcp_v1_remains_readable(tmp_path: Path) -> None:
    path = tmp_path / "mcp.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "servers": {
                    "fixture": {
                        "transport": "stdio",
                        "stdio": {"source": "local_process", "command": "fixture"},
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    config, _ = load_mcp_config(path)
    assert config.version == 1


def test_enforced_mcp_rejects_local_process(monkeypatch) -> None:
    monkeypatch.setenv("TGA_SANDBOX_RUNTIME", "enforced")
    server = MCPServerConfig.model_validate(
        {
            "transport": "stdio",
            "executionProfileId": "offline-analysis",
            "stdio": {"source": "local_process", "command": "fixture"},
        }
    )
    with pytest.raises(MCPClientConfigurationError) as error:
        discover_server(server)
    assert error.value.code == "POLICY_DENIED"


def test_enforced_mcp_rejects_mutable_image_tag(monkeypatch) -> None:
    monkeypatch.setenv("TGA_SANDBOX_RUNTIME", "enforced")
    server = MCPServerConfig.model_validate(
        {
            "transport": "stdio",
            "executionProfileId": "offline-analysis",
            "stdio": {"source": "docker_image", "image": "example.invalid/tool:latest"},
        }
    )
    with pytest.raises(MCPClientConfigurationError) as error:
        discover_server(server)
    assert error.value.code == "POLICY_DENIED"


def test_network_profile_cannot_be_assigned_to_standard_provider() -> None:
    payload = _config().model_dump(mode="json")
    payload["profiles"]["raw-network"]["provider"] = "docker_sandbox"
    with pytest.raises(ValueError):
        SandboxConfig.model_validate(payload)


def test_profile_without_toolset_digest_loads_but_execution_is_blocked() -> None:
    payload = _config().model_dump(mode="json")
    payload["profiles"]["offline-analysis"].pop("toolset_digest")
    config = SandboxConfig.model_validate(payload)
    with pytest.raises(SandboxError) as error:
        ensure_kali_profile_ready("offline-analysis", config)
    assert error.value.reason == "toolset_digest_missing"


def test_mutable_profile_image_loads_but_execution_is_blocked() -> None:
    payload = _config().model_dump(mode="json")
    payload["profiles"]["offline-analysis"]["image"] = "example.invalid/offline:latest"
    config = SandboxConfig.model_validate(payload)
    with pytest.raises(SandboxError) as error:
        ensure_kali_profile_ready("offline-analysis", config)
    assert error.value.reason == "unresolved_image_digest"


def test_valid_config_without_tools_loads() -> None:
    config = _config()
    assert not hasattr(config, "tools")
    assert set(config.profiles) == {"offline-analysis", "raw-network"}
    assert config.profile("offline-analysis").allowed_executables == ("file", "binwalk")


def test_profile_rejects_invalid_allowed_executable() -> None:
    payload = _config().model_dump(mode="json")
    payload["profiles"]["offline-analysis"]["allowed_executables"] = ["/usr/bin/file"]
    with pytest.raises(ValueError, match="invalid allowed executable"):
        SandboxConfig.model_validate(payload)


def test_profile_rejects_duplicate_allowed_executable() -> None:
    payload = _config().model_dump(mode="json")
    payload["profiles"]["offline-analysis"]["allowed_executables"] = ["file", "file"]
    with pytest.raises(ValueError, match="unique"):
        SandboxConfig.model_validate(payload)


def test_process_spec_requires_argv_even_with_audit_tool_id() -> None:
    with pytest.raises(ValueError, match="requires argv"):
        ProcessSpec(tool_id="binwalk")
    spec = ProcessSpec(argv=("binwalk", "sample.bin"), tool_id="binwalk")
    assert spec.tool_id == "binwalk"


def test_top_level_tools_key_is_rejected() -> None:
    payload = _config().model_dump(mode="json")
    payload["tools"] = {
        "binwalk": {
            "profile_id": "offline-analysis",
            "image": "example.invalid/binwalk@sha256:" + "2" * 64,
        }
    }
    with pytest.raises(ValueError, match="tools"):
        SandboxConfig.model_validate(payload)


def test_committed_config_has_no_top_level_tools() -> None:
    path = Path(__file__).parents[1] / "config" / "sandbox.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert "tools" not in payload
    assert set(payload) == {
        "version",
        "runtime",
        "terminal_grace_seconds",
        "reconcile_interval_seconds",
        "docker_sandbox",
        "sandboxd",
        "profiles",
    }
    assert payload["runtime"] == "enforced"


def test_sandboxd_agrees_with_control_plane_config_digest() -> None:
    root = Path(__file__).parents[1]
    if shutil.which("go") is None:
        pytest.skip("the Go toolchain is unavailable on this runner")
    config, _ = load_sandbox_config(root / "config" / "sandbox.json")
    completed = subprocess.run(
        [
            "go", "test", "./internal/config/",
            "-run", "TestCommittedConfigDigestMatchesControlPlane",
            "-count", "1",
        ],
        cwd=root / "sandboxd",
        env={**os.environ, "TGA_EXPECTED_CONFIG_DIGEST": config.digest},
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_unresolved_digest_is_reported_without_blocking_config_load(tmp_path) -> None:
    """The placeholder is injected now that releases pin the committed config.

    What matters is unchanged: an image nobody can pull is surfaced against the
    profile that names it, rather than made fatal at load. A half-provisioned
    host has to boot far enough to say what is missing.
    """
    payload = json.loads(
        (Path(__file__).parents[1] / "config" / "sandbox.json").read_text(
            encoding="utf-8"
        )
    )
    repository = payload["profiles"]["ctf-pwn-v1"]["image"].split("@", 1)[0]
    payload["profiles"]["ctf-pwn-v1"]["image"] = (
        f"{repository}@sha256:REPLACE_WITH_RELEASE_DIGEST"
    )
    config_path = tmp_path / "sandbox.json"
    config_path.write_text(json.dumps(payload), encoding="utf-8")

    report = inspect_kali_runtime_readiness(config_path=config_path)
    assert report.overall == "not_ready"
    assert report.profiles["ctf-pwn-v1"].status == "unresolved_digest"
    assert report.profiles["ctf-pwn-v1"].image.endswith(
        "@sha256:REPLACE_WITH_RELEASE_DIGEST"
    )


def test_disabled_runtime_is_reported_without_hiding_image_status(monkeypatch) -> None:
    monkeypatch.setenv("TGA_SANDBOX_RUNTIME", "disabled")
    report = inspect_kali_runtime_readiness(
        config_path=Path(__file__).parents[1] / "config" / "sandbox.json"
    )
    profile = report.profiles["ctf-pwn-v1"]
    assert report.runtime_mode == "disabled"
    assert profile.runtime_status == "disabled"
    # Turning the runtime off must not blank out what is known about the image;
    # otherwise a disabled deployment looks identical to a healthy one.
    assert profile.image_status not in (None, "", "disabled")
    assert profile.image and "@sha256:" in profile.image


def test_mcp_migration_binds_core_tools_but_leaves_them_disabled() -> None:
    script = Path(__file__).parents[1] / "scripts" / "migrate_mcp_sandbox_v2.py"
    spec = importlib.util.spec_from_file_location("migrate_mcp_sandbox_v2", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    migrated, _ = module.migrate(
        {
            "version": 1,
            "servers": {
                "nmap": {
                    "enabled": True,
                    "transport": "stdio",
                    "stdio": {"source": "docker_image", "image": "nmap:latest"},
                }
            },
        }
    )
    assert migrated["servers"]["nmap"]["executionProfileId"] == "tcp-assessment"
    assert migrated["servers"]["nmap"]["enabled"] is False
