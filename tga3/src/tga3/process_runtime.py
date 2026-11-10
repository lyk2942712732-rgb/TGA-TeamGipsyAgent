"""Run the four native CLIs in one hosted container without a Docker daemon.

The platform container is the security boundary. Processes have private working
directories and CLI homes, but are not mutually isolated containers. Reuse the
ordinary launch specification, journal, hooks, scheduler and report lifecycle.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from .docker_runtime import DockerContainerRuntime


class WorkerProcess:
    def __init__(self, process, log, labels):
        self.id = f"process-{process.pid}-{uuid4().hex[:8]}"
        self.process, self.log, self.labels = process, log, labels

    def reload(self):
        self.process.poll()

    @property
    def status(self):
        return "running" if self.process.poll() is None else "exited"

    @property
    def attrs(self):
        return {"State": {"ExitCode": self.process.poll()}}

    def logs(self, tail=None):
        data = self.log.read_bytes() if self.log.is_file() else b""
        return b"\n".join(data.splitlines()[-tail:]) if tail else data

    def stop(self, timeout=10):
        # CLIs create their own process groups. Capture descendants before
        # stopping the runner so a long shell command cannot outlive its lease.
        identities = {}
        for proc in Path("/proc").glob("[0-9]*/stat"):
            try:
                fields = proc.read_text().rsplit(") ", 1)[1].split()
                identities[int(proc.parent.name)] = (int(fields[1]), fields[19])
            except (OSError, ValueError, IndexError):
                continue
        descendants = {self.process.pid}
        while True:
            found = {pid for pid, (parent, _) in identities.items() if parent in descendants}
            if found.issubset(descendants):
                break
            descendants |= found

        def send(sig):
            for pid in descendants:
                try:
                    fields = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()
                    if pid in identities and fields[19] == identities[pid][1]:
                        os.kill(pid, sig)
                except (OSError, IndexError):
                    pass

        send(signal.SIGTERM)
        try:
            self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            pass
        send(signal.SIGKILL)
        self.process.wait(timeout=5)

    def remove(self, **_kwargs):
        if self.status == "running":
            self.stop()


class ProcessPool:
    def __init__(self):
        self.items = {}
        self.lock = threading.RLock()

    def run(self, *, environment, volumes, labels, **_kwargs):
        workspace = next(Path(source) for source, mount in volumes.items() if mount["bind"] == "/workspace")
        state = workspace / ".tga3"
        views = state / "views"
        views.mkdir(parents=True, exist_ok=True)
        mapping = {"/workspace": str(workspace)}
        for source, mount in volumes.items():
            logical = Path(mount["bind"])
            if logical == Path("/workspace") or logical.is_relative_to("/workspace"):
                continue
            root = logical.parts[1]
            mapping["/" + root] = str(views / root)
            target = views / str(logical).lstrip("/")
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.is_symlink():
                if target.resolve() == Path(source).resolve():
                    continue
                target.unlink()
            if not target.exists():
                target.symlink_to(source, target_is_directory=Path(source).is_dir())
        environment = dict(environment)
        environment.update(
            TGA3_LOCAL_PATHS=json.dumps(mapping),
            TGA3_WORKSPACE=str(workspace),
            TGA3_LOGS=mapping["/logs"],
            TGA3_INPUTS=mapping["/inputs"],
            TGA3_CTF_KNOWLEDGE_MCP_URL="",
            TGA3_WEB_SEARCH_MCP_URL="",
            TGA3_HOSTED="1",
        )
        if "/findings" in mapping:
            environment["TGA3_FINDINGS"] = mapping["/findings"]
        for key, logical in {
            "TGA3_AOE_ROOT": "/aoe",
            "TGA3_INTELS": "/intels",
            "TGA3_TASK_OVERVIEW_ROOT": "/task-overview",
            "TGA3_QUESTION_SOURCE": "/question-source.json",
            "TGA3_EXPERIENCE_ROOT": "/experience",
        }.items():
            if logical in mapping:
                environment[key] = mapping[logical]
        home = state / "home"
        home.mkdir(exist_ok=True)
        environment.update(
            HOME=str(home),
            XDG_CONFIG_HOME=str(home / ".config"),
            XDG_DATA_HOME=str(home / ".local/share"),
            XDG_CACHE_HOME=str(home / ".cache"),
        )
        allowed = {
            key: os.environ[key]
            for key in ["PATH", "LANG", "LC_ALL", "TERM", "PYTHONUNBUFFERED", "PYTHONDONTWRITEBYTECODE", "PYTHONPATH"]
            if key in os.environ
        }
        allowed.update(environment)
        module = {
            "codex_cli": "openai_worker",
            "claude_cli": "claude_worker",
            "opencode_cli": "opencode_worker",
            "qwen_cli": "qwen_worker",
        }[environment["TGA3_AGENT_RUNTIME"]]
        log = state / f"runner-{uuid4().hex}.log"
        with log.open("wb") as output:
            process = subprocess.Popen(
                [sys.executable, "-m", f"tga3.workers.{module}"],
                cwd=workspace,
                env=allowed,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        item = WorkerProcess(process, log, labels)
        with self.lock:
            self.items[item.id] = item
        return item

    def get(self, ident):
        return self.items[ident]

    def list(self, *, filters, **_kwargs):
        expected = dict(value.split("=", 1) for value in filters.get("label", []))
        with self.lock:
            return [item for item in self.items.values() if all(item.labels.get(k) == v for k, v in expected.items())]

    def close(self):
        with self.lock:
            for item in self.items.values():
                if item.status == "running":
                    item.stop()


class ProcessRuntime(DockerContainerRuntime):
    def __init__(self, config):
        self.config = config
        self.pool = ProcessPool()
        self.client = SimpleNamespace(containers=self.pool)

    async def image_status(self, image):
        binaries = ["codex", "claude", "opencode", "qwen"]
        available = all(shutil.which(binary) for binary in binaries)
        return {
            "status": "healthy" if available else "missing",
            "available": available,
            "detail": "Hosted native CLI processes",
            "image_id": image,
        }
