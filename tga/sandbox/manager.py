"""Policy-enforcing provider router."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

from tga.sandbox.config import SandboxConfig
from tga.sandbox.models import (
    ExecFrame,
    ExecResult,
    ProcessSpec,
    SandboxHandle,
    SandboxProfile,
    SandboxState,
)
from tga.sandbox.provider import SandboxError, SandboxProvider
from tga.sandbox.repository import SandboxInstanceRepository


@dataclass(slots=True)
class SandboxManager:
    config: SandboxConfig
    providers: dict[str, SandboxProvider]
    repository: SandboxInstanceRepository | None = None
    event_repository: object | None = None

    def acquire(
        self,
        *,
        task_id: str,
        solver_id: str,
        solver_run_id: str,
        profile_id: str,
        fencing_token: int,
        idempotency_key: str,
        profile: SandboxProfile | None = None,
    ) -> SandboxHandle:
        if self.config.runtime != "enforced":
            raise SandboxError(
                "sandbox runtime is not enforced",
                code="SANDBOX_RUNTIME_DISABLED",
            )
        profile = profile or self.config.profile(profile_id)
        if profile.provider == "remote_http":
            raise SandboxError("remote HTTP does not create a local sandbox", code="REMOTE_PROFILE")
        existing = (
            self.repository.get_active(
                task_id=task_id,
                solver_id=solver_id,
                solver_run_id=solver_run_id,
            )
            if self.repository
            else None
        )
        if existing:
            if existing.config_digest != self.config.digest:
                raise SandboxError("active sandbox configuration changed", code="CONFIG_DIGEST_MISMATCH")
            if existing.profile_id != profile_id or existing.provider != profile.provider:
                raise SandboxError(
                    "a SolverRun may not change its sandbox profile",
                    code="SOLVER_RUN_SANDBOX_CONFLICT",
                )
            if fencing_token < existing.fencing_token:
                raise SandboxError("stale fencing token", code="STALE_FENCING_TOKEN")
            if fencing_token == existing.fencing_token:
                reused = existing
                self._event("SANDBOX_REUSED", reused)
                return reused
            provider = self.providers.get(profile.provider)
            if provider is None:
                raise SandboxError(
                    f"sandbox provider {profile.provider} is unavailable",
                    code="PROVIDER_UNAVAILABLE",
                    retryable=True,
                )
            refreshed = provider.acquire(
                task_id=task_id,
                solver_id=solver_id,
                solver_run_id=solver_run_id,
                profile_id=profile_id,
                fencing_token=fencing_token,
                idempotency_key=idempotency_key,
                profile=profile,
            )
            if (
                refreshed.instance_id != existing.instance_id
                or refreshed.config_digest != existing.config_digest
                or refreshed.profile_id != existing.profile_id
                or refreshed.provider != existing.provider
                or refreshed.image_digest != existing.image_digest
                or refreshed.toolset_digest != existing.toolset_digest
            ):
                raise SandboxError(
                    "provider returned a different sandbox during fencing refresh",
                    code="SANDBOX_IDENTITY_MISMATCH",
                )
            reused = refreshed
            if self.repository:
                self.repository.put(reused)
            self._event("SANDBOX_REUSED", reused)
            return reused
        provider = self.providers.get(profile.provider)
        if provider is None:
            raise SandboxError(
                f"sandbox provider {profile.provider} is unavailable",
                code="PROVIDER_UNAVAILABLE",
                retryable=True,
            )
        handle = provider.acquire(
            task_id=task_id,
            solver_id=solver_id,
            solver_run_id=solver_run_id,
            profile_id=profile_id,
            fencing_token=fencing_token,
            idempotency_key=idempotency_key,
            profile=profile,
        )
        if handle.config_digest != self.config.digest:
            provider.destroy(handle)
            raise SandboxError("provider returned a different config digest", code="CONFIG_DIGEST_MISMATCH")
        if self.repository:
            self.repository.put(handle)
        self._event("SANDBOX_ACQUIRED", handle)
        return handle

    def provider_for(self, handle: SandboxHandle) -> SandboxProvider:
        provider = self.providers.get(handle.provider)
        if provider is None:
            raise SandboxError("sandbox provider is unavailable", code="PROVIDER_UNAVAILABLE")
        return provider

    def exec(
        self, handle: SandboxHandle, spec: ProcessSpec,
        *, profile: SandboxProfile | None = None,
    ) -> tuple[Iterator[ExecFrame], ExecResult]:
        profile = profile or self.config.profile(handle.profile_id)
        if spec.argv and spec.argv[0] not in profile.allowed_executables:
            raise SandboxError(
                f"executable {spec.argv[0]!r} is not allowed by profile {handle.profile_id}",
                code="EXECUTABLE_NOT_ALLOWED",
            )
        self._event("SANDBOX_EXEC_STARTED", handle)
        try:
            frames, result = self.provider_for(handle).exec(
                handle, spec, profile=profile
            )
        except Exception:
            self._event("SANDBOX_EXEC_FAILED", handle)
            raise
        self._event(
            "SANDBOX_EXEC_FINISHED",
            handle,
            {"exit_code": result.exit_code, "timed_out": result.timed_out, "truncated": result.truncated},
        )
        return frames, result

    def open_process(
        self, handle: SandboxHandle, spec: ProcessSpec,
        *, profile: SandboxProfile | None = None,
    ):
        profile = profile or self.config.profile(handle.profile_id)
        if spec.argv[0] not in profile.session_executables:
            raise SandboxError(
                f"executable {spec.argv[0]!r} is not session-enabled by profile {handle.profile_id}",
                code="SESSION_EXECUTABLE_NOT_ALLOWED",
            )
        process = self.provider_for(handle).open_process(
            handle, spec, profile=profile
        )
        self._event("SANDBOX_PROCESS_OPENED", handle, {"process_id": process.process_id})
        return process

    def release(self, handle: SandboxHandle, *, grace_seconds: int | None = None) -> None:
        self.provider_for(handle).release(handle)
        grace = self.config.terminal_grace_seconds if grace_seconds is None else grace_seconds
        destroy_after = (
            datetime.now(UTC) + timedelta(seconds=max(0, grace))
        ).isoformat().replace("+00:00", "Z")
        if self.repository:
            self.repository.transition(
                handle.instance_id,
                SandboxState.RELEASED,
                destroy_after=destroy_after,
            )
        self._event("SANDBOX_RELEASED", handle, {"destroy_after": destroy_after})

    def destroy(self, handle: SandboxHandle) -> None:
        if self.repository:
            self.repository.transition(handle.instance_id, SandboxState.DESTROYING)
        self.provider_for(handle).destroy(handle)
        if self.repository:
            self.repository.transition(handle.instance_id, SandboxState.DESTROYED)
        self._event("SANDBOX_DESTROYED", handle)

    def cleanup_due(self, now: str | None = None) -> tuple[str, ...]:
        if self.repository is None:
            return ()
        destroyed = []
        for handle in self.repository.due_for_destroy(now):
            self.destroy(handle)
            destroyed.append(handle.instance_id)
        return tuple(destroyed)

    def reconcile(self, *, grace_before: datetime | None = None) -> tuple[str, ...]:
        if self.repository is None:
            return ()
        cutoff = grace_before or datetime.now(UTC)
        valid = self.repository.active_instance_ids()
        destroyed: list[str] = []
        for provider in self.providers.values():
            reconcile = getattr(provider, "reconcile", None)
            if not callable(reconcile):
                continue
            try:
                removed = reconcile(valid, grace_before=cutoff)
                destroyed.extend(removed)
            except Exception:
                raise
        return tuple(destroyed)

    def _event(self, kind: str, handle: SandboxHandle, extra: dict | None = None) -> None:
        append = getattr(self.event_repository, "append_agent_event", None)
        if not callable(append):
            return
        append(
            handle.task_id,
            kind,
            {
                "instance_id": handle.instance_id,
                "provider": handle.provider,
                "profile_id": handle.profile_id,
                "solver_run_id": handle.solver_run_id,
                "image_digest": handle.image_digest,
                "toolset_digest": handle.toolset_digest,
                "config_digest": handle.config_digest,
                "fencing_token": handle.fencing_token,
                **(extra or {}),
            },
            solver_id=handle.solver_id,
        )
