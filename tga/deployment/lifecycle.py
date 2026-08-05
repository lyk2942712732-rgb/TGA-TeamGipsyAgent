"""The `up` / `down` / `status` / `doctor` state machine.

One implementation backs every surface: the Linux CLI calls it directly, and
the Windows launcher calls it through WSL.  Keeping a single implementation is
what stops the two platforms from drifting into different startup semantics.
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from tga.deployment import image_manager, readiness, service_manager, state as state_module
from tga.deployment.errors import DeploymentError, ErrorCode
from tga.deployment.paths import ensure_run_root, log_dir, web_dist
from tga.sandbox.config import load_sandbox_config

DEFAULT_PORT = 8123
DEFAULT_HOST = "127.0.0.1"


@dataclass
class StepResult:
    name: str
    ok: bool
    detail: str = ""
    code: ErrorCode | None = None
    skipped: bool = False


@dataclass
class UpResult:
    ok: bool
    status: str
    url: str = ""
    steps: list[StepResult] = field(default_factory=list)
    readiness: dict | None = None
    error: dict | None = None

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "status": self.status,
            "url": self.url,
            "steps": [
                {
                    "name": step.name,
                    "ok": step.ok,
                    "skipped": step.skipped,
                    "detail": step.detail,
                    **({"code": str(step.code)} if step.code else {}),
                }
                for step in self.steps
            ],
            **({"readiness": self.readiness} if self.readiness is not None else {}),
            **({"error": self.error} if self.error else {}),
        }


def detect_platform() -> str:
    """Identify the execution surface for reporting and command routing."""
    if os.name == "nt":
        return "windows/native"
    if _in_wsl():
        return "linux/wsl"
    return f"linux/{platform.machine()}"


def _in_wsl() -> bool:
    try:
        return "microsoft" in Path("/proc/version").read_text(encoding="utf-8").lower()
    except OSError:
        return False


def up(
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    open_browser: bool = True,
    timeout_seconds: float = 90.0,
    pull_images: bool = False,
) -> UpResult:
    """Bring the deployment to a serving state, resuming partial progress."""
    with state_module.locked():
        current = state_module.load()

        # Idempotency: an already-serving deployment is reported, not restarted.
        if current.phase in {"ready", "degraded"} and _already_serving(current):
            report = _fetch_readiness(current.api_url)
            return UpResult(
                ok=True,
                status=current.phase,
                url=current.api_url,
                steps=[StepResult("already_running", True, f"pid {current.api_pid}", skipped=True)],
                readiness=report,
            )

        current.phase = "starting"
        current.host = host
        current.port = port
        state_module.save(current)

        steps: list[StepResult] = []
        try:
            steps.append(_step_detect_platform(current))
            steps.append(_step_configuration(current))
            steps.append(_step_web_bundle(current))
            steps.append(_step_container_engine(current))
            steps.append(_step_images(current, pull=pull_images))
            steps.append(_step_sandboxd(current))
            steps.append(_step_start_api(current, host=host, port=port))
            ready_step, report = _step_wait_readiness(current, timeout_seconds=timeout_seconds)
            steps.append(ready_step)
        except DeploymentError as exc:
            current.phase = "failed"
            current.last_error_code = str(exc.code)
            current.last_error_detail = exc.detail
            state_module.save(current)
            steps.append(StepResult("failed", False, exc.detail, exc.code))
            return UpResult(ok=False, status="failed", steps=steps, error=exc.to_dict())

        status = report.get("status", "degraded") if report else "degraded"
        current.phase = "ready" if status == "ready" else "degraded"
        current.api_url = f"http://{host}:{port}"
        current.last_error_code = ""
        current.last_error_detail = ""
        state_module.save(current)

        if open_browser:
            _open_browser(current.api_url)

        return UpResult(
            ok=True,
            status=current.phase,
            url=current.api_url,
            steps=steps,
            readiness=report,
        )


def _step_detect_platform(current) -> StepResult:
    surface = detect_platform()
    current.mark_completed("detect_platform")
    return StepResult("detect_platform", True, surface)


def _step_configuration(current) -> StepResult:
    root = ensure_run_root()
    current.mark_completed("ensure_configuration")
    return StepResult("ensure_configuration", True, f"run_root={root}")


def _step_web_bundle(current) -> StepResult:
    bundle = web_dist()
    current.mark_completed("ensure_web_bundle")
    return StepResult("ensure_web_bundle", True, str(bundle))


def _step_images(current, *, pull: bool) -> StepResult:
    """Say which profile images this host actually has.

    Until now this step existed in name only: resolving the web bundle marked
    `ensure_images` complete, so a host with no images at all recorded a fully
    provisioned deployment. Sandbox availability stays graded -- a missing
    image degrades the deployment rather than failing it -- but it is no longer
    reported as done when nothing was checked.
    """
    try:
        config, _ = load_sandbox_config()
    except Exception as exc:  # configuration problems are reported, not fatal
        return StepResult("ensure_images", False, str(exc)[:200], ErrorCode.SANDBOX_RUNTIME_DISABLED)

    report = image_manager.ensure_images(config, pull=pull)
    if report.ok:
        current.mark_completed("ensure_images")
        return StepResult("ensure_images", True, report.summary())

    if report.unpinned:
        code = ErrorCode.PROFILE_DIGEST_INVALID
    elif not report.docker_available:
        code = ErrorCode.DOCKER_UNAVAILABLE
    else:
        code = ErrorCode.PROFILE_IMAGE_MISSING
    detail = report.summary()
    if not pull and code is ErrorCode.PROFILE_IMAGE_MISSING:
        detail += "; run `tga up --pull-images` to fetch them"
    return StepResult("ensure_images", False, detail, code)


def _step_container_engine(current) -> StepResult:
    """Docker is required only for enforced sandboxing; absence is degraded."""
    import shutil

    docker = shutil.which("docker")
    if not docker:
        return StepResult(
            "start_container_engine", False, "docker not found", ErrorCode.DOCKER_UNAVAILABLE
        )
    try:
        completed = subprocess.run(
            [docker, "info", "--format", "{{.ServerVersion}}"],
            capture_output=True, text=True, timeout=30, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return StepResult("start_container_engine", False, str(exc), ErrorCode.DOCKER_UNAVAILABLE)
    if completed.returncode != 0:
        return StepResult(
            "start_container_engine", False,
            completed.stderr.strip()[:200] or "docker daemon unreachable",
            ErrorCode.DOCKER_UNAVAILABLE,
        )
    current.mark_completed("start_container_engine")
    return StepResult("start_container_engine", True, completed.stdout.strip())


def _step_sandboxd(current) -> StepResult:
    """Start sandboxd where systemd owns it, then confirm it answers.

    This step used to only look at the socket, so on a host where the unit was
    installed but not running, `tga up` reported "no response" and left it
    stopped -- the one command meant to bring the deployment up would not start
    the service it was complaining about.
    """
    try:
        config, _ = load_sandbox_config()
    except Exception as exc:
        return StepResult("start_sandboxd", False, str(exc)[:200], ErrorCode.SANDBOX_RUNTIME_DISABLED)
    if config.runtime != "enforced":
        return StepResult(
            "start_sandboxd", False, "sandbox runtime is disabled",
            ErrorCode.SANDBOX_RUNTIME_DISABLED, skipped=True,
        )

    started = ""
    if service_manager.unit_installed(service_manager.SANDBOXD_UNIT):
        unit_state = service_manager.state(service_manager.SANDBOXD_UNIT)
        if not unit_state.active:
            unit_state = service_manager.start(service_manager.SANDBOXD_UNIT)
        if not unit_state.active:
            return StepResult(
                "start_sandboxd", False,
                f"{service_manager.SANDBOXD_UNIT} did not become active"
                + (f": {unit_state.detail}" if unit_state.detail else ""),
                ErrorCode.SANDBOXD_SOCKET_MISSING,
            )
        started = f"systemd {service_manager.SANDBOXD_UNIT}"
        # The socket appears a moment after the unit reports active.
        _await_socket(config)

    if readiness._sandboxd_health(config) is None:
        return StepResult(
            "start_sandboxd", False,
            f"no response on {config.sandboxd.socket_path}",
            ErrorCode.SANDBOXD_SOCKET_MISSING,
        )
    current.mark_completed("start_sandboxd")
    return StepResult("start_sandboxd", True, started)


def _await_socket(config, *, timeout_seconds: float = 10.0) -> None:
    """Give a just-started sandboxd time to bind before declaring it absent."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if readiness._sandboxd_health(config) is not None:
            return
        time.sleep(0.2)


def _step_start_api(current, *, host: str, port: int) -> StepResult:
    """Start the server through systemd when it owns the unit, else directly."""
    # systemd is authoritative where it is installed: starting a competing
    # child here would leave `tga down` unable to stop what systemd restarts.
    if service_manager.manages_api():
        unit_state = service_manager.state()
        if not unit_state.active:
            unit_state = service_manager.start()
        if not unit_state.active:
            raise DeploymentError(
                ErrorCode.API_START_FAILED,
                f"systemd could not start {service_manager.API_UNIT}",
            )
        current.api_pid = unit_state.main_pid
        current.supervisor = "systemd"
        current.mark_completed("start_api")
        return StepResult(
            "start_api", True, f"systemd {service_manager.API_UNIT} pid {unit_state.main_pid}"
        )

    current.supervisor = "launcher"
    if not readiness.port_is_free(host, port):
        if _health_ok(f"http://{host}:{port}"):
            current.mark_completed("start_api")
            return StepResult("start_api", True, "reusing the listener already on this port", skipped=True)
        raise DeploymentError(ErrorCode.PORT_UNAVAILABLE, f"{host}:{port} is in use")

    logs = log_dir()
    logs.mkdir(parents=True, exist_ok=True)
    handle = (logs / "api.log").open("ab")
    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
            subprocess, "DETACHED_PROCESS", 0
        )
    try:
        process = subprocess.Popen(
            [sys.executable, "-m", "tga.cli.internal", "serve",
             "--host", host, "--port", str(port)],
            stdout=handle, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            env={**os.environ}, creationflags=creationflags,
            start_new_session=(os.name != "nt"),
        )
    except OSError as exc:
        raise DeploymentError(ErrorCode.API_START_FAILED, str(exc)) from exc
    finally:
        handle.close()

    current.api_pid = process.pid
    current.mark_completed("start_api")
    return StepResult("start_api", True, f"pid {process.pid}")


def _step_wait_readiness(current, *, timeout_seconds: float) -> tuple[StepResult, dict | None]:
    origin = f"http://{current.host}:{current.port}"
    deadline = time.monotonic() + timeout_seconds
    report: dict | None = None
    while time.monotonic() < deadline:
        report = _fetch_readiness(origin)
        if report is not None and report.get("ready"):
            current.mark_completed("wait_for_readiness")
            return StepResult("wait_for_readiness", True, report.get("status", "")), report
        if current.api_pid and not state_module.process_alive(current.api_pid):
            raise DeploymentError(
                ErrorCode.API_START_FAILED,
                "the API process exited during startup; see `tga logs --component api`",
            )
        time.sleep(0.3)
    raise DeploymentError(
        ErrorCode.READINESS_TIMEOUT, f"{origin} did not become ready in {timeout_seconds:.0f}s"
    )


def _fetch_readiness(origin: str) -> dict | None:
    if not origin:
        return None
    import json

    try:
        with urlopen(f"{origin}/api/v2/system/readiness", timeout=2.0) as response:
            if response.status != 200:
                return None
            return json.loads(response.read().decode("utf-8"))
    except (OSError, URLError, ValueError):
        return None


def _health_ok(origin: str) -> bool:
    try:
        with urlopen(f"{origin}/api/health", timeout=1.5) as response:
            return response.status == 200
    except (OSError, URLError):
        return False


def _already_serving(current) -> bool:
    url = current.api_url or (f"http://{current.host}:{current.port}" if current.port else "")
    if not url:
        return False
    if service_manager.manages_api() and not service_manager.state().active:
        # systemd stopped it out of band; the recorded state is stale.
        return False
    return _health_ok(url)


def _open_browser(url: str) -> None:
    import webbrowser

    try:
        webbrowser.open(url, new=2)
    except Exception:
        # A headless server has no browser; the URL is printed instead.
        pass


def down() -> dict:
    """Stop the serving process while preserving all data."""
    with state_module.locked():
        current = state_module.load()
        current.phase = "stopping"
        state_module.save(current)

        # Stop through the same backend that started it, or systemd would
        # simply restart whatever we killed.
        supervisor = "launcher"
        if service_manager.manages_api():
            supervisor = "systemd"
            stopped = not service_manager.stop().active
        elif current.api_pid and state_module.process_alive(current.api_pid):
            stopped = _terminate(current.api_pid)
        else:
            stopped = False

        # `tga up` starts sandboxd, so `tga down` has to stop it. Leaving a
        # privileged runtime running after the user asked for everything to
        # stop is the kind of surprise that only shows up as a puzzling
        # container on the next boot.
        sandboxd_stopped = False
        if service_manager.unit_installed(service_manager.SANDBOXD_UNIT):
            sandboxd_stopped = not service_manager.stop(
                service_manager.SANDBOXD_UNIT
            ).active

        current.phase = "stopped"
        current.api_pid = None
        current.api_url = ""
        current.supervisor = ""
        current.reset_steps()
        state_module.save(current)
        return {
            "ok": True,
            "status": "stopped",
            "stopped_process": stopped,
            "stopped_sandboxd": sandboxd_stopped,
            "supervisor": supervisor,
        }


def _terminate(pid: int) -> bool:
    if os.name == "nt":
        completed = subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True, text=True, check=False,
        )
        return completed.returncode == 0
    import signal as signal_module

    try:
        os.kill(pid, signal_module.SIGTERM)
    except OSError:
        return False
    for _ in range(50):
        if not state_module.process_alive(pid):
            return True
        time.sleep(0.1)
    try:
        os.kill(pid, signal_module.SIGKILL)
    except OSError:
        pass
    return True


def status() -> dict:
    """Report the deployment state without changing it."""
    current = state_module.load()
    unit = service_manager.state() if service_manager.manages_api() else None

    # Serving is decided by the service actually answering, not by what the
    # state file last recorded, so an out-of-band systemctl stays visible.
    if unit is not None:
        running = unit.active and _health_ok(current.api_url or f"http://{current.host}:{current.port}")
        api_pid = unit.main_pid
    else:
        running = state_module.process_alive(current.api_pid) and _health_ok(current.api_url)
        api_pid = current.api_pid

    url = current.api_url or (f"http://{current.host}:{current.port}" if current.port else "")
    report = _fetch_readiness(url) if running else None
    return {
        "ok": True,
        "platform": detect_platform(),
        "supervisor": "systemd" if unit is not None else "launcher",
        "phase": current.phase if running else ("stopped" if current.phase != "failed" else "failed"),
        "running": running,
        "url": url if running else "",
        "api_pid": api_pid,
        "last_error_code": current.last_error_code,
        "last_error_detail": current.last_error_detail,
        "readiness": report,
    }


def doctor() -> dict:
    """Diagnose every capability, whether or not the server is running."""
    current = state_module.load()
    running = state_module.process_alive(current.api_pid) and _health_ok(current.api_url)
    report = _fetch_readiness(current.api_url) if running else readiness.evaluate().to_dict()

    checks: list[dict] = [
        {"name": "platform", "status": "ready", "detail": detect_platform()},
        {"name": "api", "status": "ready" if running else "unavailable",
         "detail": current.api_url or "not serving"},
    ]
    if report:
        checks.append({"name": "storage", **_flatten(report.get("storage"))})
        sandbox = report.get("sandbox") or {}
        checks.append({"name": "sandbox.runtime", "status": (
            "ready" if sandbox.get("runtime") == "enforced" else "disabled"
        ), "detail": str(sandbox.get("runtime", "unknown"))})
        for key, value in sandbox.items():
            if key == "runtime":
                continue
            checks.append({"name": f"sandbox.{key}", **_flatten(value)})
        profiles = report.get("profiles") or {}
        not_ready = [name for name, item in profiles.items()
                     if isinstance(item, dict) and item.get("status") != "ready"]
        checks.append({
            "name": "profiles",
            "status": "ready" if profiles and not not_ready else "unavailable",
            "detail": f"{len(profiles) - len(not_ready)}/{len(profiles)} ready",
            **({"code": str(ErrorCode.PROFILE_DIGEST_INVALID)} if not_ready else {}),
        })

    failing = [item for item in checks if item.get("status") not in {"ready", "disabled"}]
    return {
        "ok": not any(item["name"] in {"api", "storage"} for item in failing),
        "status": report.get("status", "failed") if report else "failed",
        "checks": checks,
        "remediation": _remediation_for(checks),
    }


def _flatten(value) -> dict:
    if isinstance(value, dict):
        return {k: v for k, v in value.items() if k in {"status", "detail", "code"}}
    return {"status": str(value)}


def _remediation_for(checks: list[dict]) -> list[dict]:
    from tga.deployment.errors import REMEDIATION, ErrorCode as Code

    seen: list[dict] = []
    for item in checks:
        raw = item.get("code")
        if not raw:
            continue
        try:
            code = Code(raw)
        except ValueError:
            continue
        hint = REMEDIATION.get(code, "")
        if hint and not any(entry["code"] == str(code) for entry in seen):
            seen.append({"code": str(code), "hint": hint})
    return seen


def logs(component: str = "api", lines: int = 200) -> dict:
    """Return the tail of a component log."""
    path = log_dir() / f"{component}.log"
    if not path.is_file():
        return {"ok": False, "component": component, "path": str(path), "lines": []}
    try:
        content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return {"ok": False, "component": component, "path": str(path), "error": str(exc), "lines": []}
    return {
        "ok": True,
        "component": component,
        "path": str(path),
        "lines": content[-max(1, lines):],
    }
