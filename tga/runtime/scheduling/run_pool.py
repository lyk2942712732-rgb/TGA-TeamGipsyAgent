"""Bounded parallel execution for durable SolverRun attempts."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Callable

from tga.domain.solver import SolverRun
from tga.runtime.scheduling.concurrency import CancellationError, CancellationToken
from tga.runtime.scheduling.execution import SolverExecutionContext


class ActiveRunRegistry:
    """Process-local bridge from durable controls to active runner tokens."""

    _lock = threading.Lock()
    _contexts: dict[tuple[str, str], SolverExecutionContext] = {}

    @classmethod
    def register(cls, context: SolverExecutionContext) -> None:
        with cls._lock:
            cls._contexts[(context.task_id, context.solver_id)] = context

    @classmethod
    def unregister(cls, context: SolverExecutionContext) -> None:
        with cls._lock:
            if cls._contexts.get((context.task_id, context.solver_id)) is context:
                cls._contexts.pop((context.task_id, context.solver_id), None)

    @classmethod
    def get(cls, task_id: str, solver_id: str) -> SolverExecutionContext | None:
        with cls._lock:
            return cls._contexts.get((task_id, solver_id))

    @classmethod
    def cancel_solver(cls, task_id: str, solver_id: str, reason: str) -> bool:
        with cls._lock:
            context = cls._contexts.get((task_id, solver_id))
        return context.cancellation.cancel(reason) if context is not None else False

    @classmethod
    def cancel_task(cls, task_id: str, reason: str) -> int:
        with cls._lock:
            contexts = [context for (owner_task, _), context in cls._contexts.items() if owner_task == task_id]
        return sum(context.cancellation.cancel(reason) for context in contexts)


@dataclass(frozen=True)
class SolverRunCompletion:
    state: str
    result_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    value: Any = None


@dataclass
class DurableSolverRunContext:
    run_id: str
    task_id: str
    solver_id: str
    owner_id: str
    fencing_token: int
    cancellation: CancellationToken
    _is_valid: Callable[[], bool]

    def assert_active(self) -> None:
        self.cancellation.raise_if_cancelled()
        if not self._is_valid():
            self.cancellation.cancel("solver_run_lease_lost")
            self.cancellation.raise_if_cancelled()


class SolverRunPool:
    """Claim and execute queued Runs with one database connection per worker."""

    def __init__(
        self,
        *,
        repository_factory: Callable[[], Any],
        owner_id: str,
        max_active_workers: int,
        lease_ttl_seconds: float = 120,
    ) -> None:
        self.repository_factory = repository_factory
        self.owner_id = owner_id
        self.max_active_workers = max_active_workers
        self.lease_ttl_seconds = lease_ttl_seconds

    def run(
        self,
        task_id: str,
        runs: tuple[SolverRun, ...],
        operation: Callable[[SolverRun, SolverExecutionContext], SolverRunCompletion],
    ) -> tuple[SolverRunCompletion, ...]:
        candidates = tuple(run for run in runs if run.task_id == task_id)
        if not candidates:
            return ()
        completions: list[SolverRunCompletion] = []
        with ThreadPoolExecutor(
            max_workers=self.max_active_workers,
            thread_name_prefix=f"tga-solver-{task_id}",
        ) as executor:
            futures = [executor.submit(self._execute, run, operation) for run in candidates]
            for future in as_completed(futures):
                value = future.result()
                if value is not None:
                    completions.append(value)
        return tuple(completions)

    def _execute(
        self,
        candidate: SolverRun,
        operation: Callable[[SolverRun, SolverExecutionContext], SolverRunCompletion],
    ) -> SolverRunCompletion | None:
        repositories = self.repository_factory()
        heartbeat_stopped = threading.Event()
        cancellation = CancellationToken()
        try:
            claimed = repositories.orchestration.claim_solver_run(
                candidate.id,
                self.owner_id,
                ttl_seconds=self.lease_ttl_seconds,
                expected_version=candidate.version,
                max_active_workers=self.max_active_workers,
            )
            if claimed is None:
                return None
            started = repositories.orchestration.start_solver_run(
                claimed.id, self.owner_id, claimed.fencing_token
            )
            repositories.events.append_agent_event(
                started.task_id,
                "SOLVER_RUN_STARTED",
                {
                    "run_id": started.id,
                    "assignment_id": started.assignment_id,
                    "attempt": started.attempt,
                    "fencing_token": started.fencing_token,
                },
                solver_id=started.solver_id,
                intent_id=started.intent_id,
            )

            def is_valid() -> bool:
                check = None
                try:
                    check = self.repository_factory()
                    persisted = check.orchestration.get_solver_run(started.id)
                    solver = check.solvers.get_solver(started.solver_id)
                    return bool(
                        persisted
                        and persisted.state in {"leased", "running"}
                        and persisted.lease_owner == self.owner_id
                        and persisted.fencing_token == started.fencing_token
                        and solver is not None
                        and str(solver.status) not in {"paused", "cancelled"}
                    )
                except Exception:
                    return False
                finally:
                    if check is not None:
                        check.close()

            context = SolverExecutionContext(
                run_id=started.id,
                task_id=started.task_id,
                solver_id=started.solver_id,
                owner_id=self.owner_id,
                fencing_token=started.fencing_token,
                cancellation=cancellation,
                _is_valid=is_valid,
            )
            ActiveRunRegistry.register(context)

            def lease_lost(reason: str, message: str | None = None) -> None:
                cancellation.cancel(reason)
                events = None
                try:
                    events = self.repository_factory()
                    events.events.append_agent_event(
                        started.task_id,
                        "SOLVER_RUN_LEASE_LOST",
                        {
                            "run_id": started.id,
                            "reason": reason,
                            "message": message,
                        },
                        solver_id=started.solver_id,
                        intent_id=started.intent_id,
                    )
                except Exception:
                    pass
                finally:
                    if events is not None:
                        events.close()

            def heartbeat() -> None:
                while not heartbeat_stopped.wait(self.lease_ttl_seconds / 3):
                    heartbeat_repositories = None
                    try:
                        heartbeat_repositories = self.repository_factory()
                        renewed = heartbeat_repositories.orchestration.renew_solver_run(
                            started.id,
                            self.owner_id,
                            started.fencing_token,
                            ttl_seconds=self.lease_ttl_seconds,
                        )
                        if renewed is None:
                            lease_lost("solver_run_lease_lost")
                            return
                    except Exception as exc:
                        lease_lost(
                            "solver_run_lease_renew_failed", str(exc)[:500]
                        )
                        return
                    finally:
                        if heartbeat_repositories is not None:
                            heartbeat_repositories.close()

            heartbeat_thread = threading.Thread(
                target=heartbeat,
                name=f"tga-run-heartbeat-{started.id}",
                daemon=True,
            )
            heartbeat_thread.start()
            try:
                completion = operation(started, context)
                context.assert_active()
                if completion.state == "waiting_approval":
                    finished = repositories.orchestration.suspend_solver_run_for_approval(
                        started.id, self.owner_id, started.fencing_token
                    )
                else:
                    finished = repositories.orchestration.finish_solver_run(
                        started.id,
                        self.owner_id,
                        started.fencing_token,
                        state=completion.state,
                        result_id=completion.result_id,
                        error_code=completion.error_code,
                        error_message=completion.error_message,
                    )
                repositories.events.append_agent_event(
                    finished.task_id,
                    {
                        "completed": "SOLVER_RUN_COMPLETED",
                        "waiting_approval": "SOLVER_RUN_WAITING_APPROVAL",
                    }.get(finished.state, "SOLVER_RUN_FAILED"),
                    {
                        "run_id": finished.id,
                        "state": finished.state,
                        "result_id": finished.result_id,
                        "error_code": finished.error_code,
                    },
                    solver_id=finished.solver_id,
                    intent_id=finished.intent_id,
                )
                return completion
            except CancellationError as exc:
                cancelled = SolverRunCompletion(
                    state="cancelled",
                    error_code="SOLVER_RUN_CANCELLED",
                    error_message=str(exc)[:1000],
                    value=exc,
                )
                try:
                    repositories.orchestration.finish_solver_run(
                        started.id,
                        self.owner_id,
                        started.fencing_token,
                        state="cancelled",
                        error_code=cancelled.error_code,
                        error_message=cancelled.error_message,
                    )
                except Exception:
                    pass
                return cancelled
            except Exception as exc:
                failed = SolverRunCompletion(
                    state="failed",
                    error_code="SOLVER_RUN_EXECUTION_FAILED",
                    error_message=str(exc)[:1000],
                    value=exc,
                )
                try:
                    repositories.orchestration.finish_solver_run(
                        started.id,
                        self.owner_id,
                        started.fencing_token,
                        state="failed",
                        error_code="SOLVER_RUN_EXECUTION_FAILED",
                        error_message=str(exc)[:1000],
                    )
                except Exception:
                    pass
                return failed
            finally:
                heartbeat_stopped.set()
                heartbeat_thread.join(timeout=max(1.0, self.lease_ttl_seconds / 3))
                ActiveRunRegistry.unregister(context)
        finally:
            repositories.close()


__all__ = ["ActiveRunRegistry", "DurableSolverRunContext", "SolverRunCompletion", "SolverRunPool"]
