"""Process-owned sandbox cleanup and orphan reconciliation."""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tga.deployment.paths import run_root as resolve_run_root
from tga.evidence.store import EvidenceStore
from tga.sandbox.config import SandboxConfig, load_sandbox_config
from tga.sandbox.docker_provider import DockerSandboxProvider
from tga.sandbox.manager import SandboxManager
from tga.sandbox.repository import SandboxInstanceRepository
from tga.sandbox.sandboxd_provider import SandboxdProvider


LOGGER = logging.getLogger(__name__)


class SandboxLifecycleService:
    def __init__(self, run_root: str | Path | None = None, *, config: SandboxConfig | None = None):
        self.run_root = resolve_run_root(run_root)
        self.config = config or load_sandbox_config()[0]
        self.providers = {
            "docker_sandbox": DockerSandboxProvider(self.config),
            "sandboxd": SandboxdProvider(self.config),
        }
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self.config.runtime != "enforced" or self._thread is not None:
            return
        # Reconciliation starts in the background without an eager health
        # probe: readiness is reported per profile at the execution boundary,
        # so an absent sandboxd must degrade the deployment, not block boot.
        self._thread = threading.Thread(
            target=self._loop,
            name="tga-sandbox-reconcile",
            daemon=True,
        )
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10)
            self._thread = None

    def run_once(self) -> tuple[str, ...]:
        valid: set[str] = set()
        cleaned: list[str] = []
        for database in sorted(self.run_root.glob("*/evidence.db")):
            store = EvidenceStore(database)
            try:
                repository = SandboxInstanceRepository(store)
                manager = SandboxManager(
                    config=self.config,
                    providers=self.providers,
                    repository=repository,
                    event_repository=store,
                )
                cleaned.extend(manager.cleanup_due())
                valid.update(repository.active_instance_ids())
            finally:
                store.close()
        cutoff = datetime.now(UTC) - timedelta(seconds=self.config.terminal_grace_seconds)
        for provider in self.providers.values():
            try:
                removed = provider.reconcile(tuple(sorted(valid)), grace_before=cutoff)
                cleaned.extend(removed)
                for instance_id in removed:
                    LOGGER.warning("SANDBOX_RECONCILED instance_id=%s provider=%s", instance_id, provider.provider_name)
            except Exception:
                LOGGER.exception("SANDBOX_RECONCILE_FAILED provider=%s", provider.provider_name)
                raise
        return tuple(cleaned)

    def _loop(self) -> None:
        while not self._stop.wait(self.config.reconcile_interval_seconds):
            try:
                self.run_once()
            except Exception:
                LOGGER.exception("periodic sandbox reconciliation failed")
