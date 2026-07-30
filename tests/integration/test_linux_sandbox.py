"""Privileged smoke suite invoked only on the dedicated integration runner."""

from __future__ import annotations

import os
import subprocess
import time

from tga.sandbox.config import load_sandbox_config
from tga.sandbox.docker_provider import DockerSandboxProvider
from tga.sandbox.models import NetworkGrant, ProcessSpec
from tga.sandbox.provider import SandboxError
from tga.sandbox.sandboxd_provider import SandboxdProvider


def output(frames) -> bytes:
    return b"".join(frame.data for frame in frames if frame.stream == "stdout")


def main() -> int:
    config, _ = load_sandbox_config()
    target = os.environ["TGA_INTEGRATION_TARGET_IP"]
    daemon = SandboxdProvider(config)
    daemon.health()
    raw = daemon.acquire(
        task_id="integration-raw",
        solver_id="solver-1",
        profile_id="raw-network",
        fencing_token=2,
        idempotency_key="integration-raw",
    )
    try:
        inspected = daemon.inspect(raw)
        assert inspected.runtime == "runsc"
        docker_runtime = subprocess.check_output(
            ["docker", "inspect", raw.instance_id, "--format", "{{.HostConfig.Runtime}}"],
            text=True,
        ).strip()
        assert docker_runtime == "runsc"
        grants = (
            NetworkGrant(cidr=f"{target}/32", ports=(80,)),
            NetworkGrant(cidr=f"{target}/32"),
        )
        frames, result = daemon.exec(
            raw,
            ProcessSpec(
                argv=("nmap", "-n", "-Pn", "-sS", "-p", "80", target),
                network_grants=grants,
                timeout_seconds=20,
            ),
        )
        assert result.exit_code == 0 and b"80/tcp open" in output(frames)
        _, ping = daemon.exec(
            raw,
            ProcessSpec(
                argv=("ping", "-n", "-c", "1", target),
                network_grants=grants,
                timeout_seconds=10,
            ),
        )
        assert ping.exit_code == 0
        _, metadata = daemon.exec(
            raw,
            ProcessSpec(
                argv=("timeout", "3", "bash", "-c", "echo >/dev/tcp/169.254.169.254/80"),
                network_grants=(NetworkGrant(cidr="0.0.0.0/0", ports=(80,)),),
                timeout_seconds=8,
            ),
        )
        assert metadata.exit_code != 0
        process = daemon.open_process(
            raw,
            ProcessSpec(argv=("cat",), network_grants=grants, timeout_seconds=30),
        )
        process.send(b"stream-check\n")
        assert process.receive(5).data == b"stream-check\n"
        daemon.stop_process(process.process_id, fencing_token=raw.fencing_token)
        try:
            daemon.inspect(raw.model_copy(update={"fencing_token": 1}))
            raise AssertionError("stale fencing token was accepted")
        except SandboxError:
            pass
    finally:
        daemon.destroy(raw)

    standard = DockerSandboxProvider(config)
    handle = standard.acquire(
        task_id="integration-sbx",
        solver_id="solver-1",
        profile_id="offline-analysis",
        fencing_token=1,
        idempotency_key="integration-sbx",
    )
    try:
        frames, result = standard.exec(
            handle,
            ProcessSpec(argv=("sh", "-c", "printf sandbox-ok")),
        )
        assert result.exit_code == 0
        assert output(frames) == b"sandbox-ok"
    finally:
        standard.destroy(handle)

    time.sleep(1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
