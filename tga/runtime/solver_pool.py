"""Durable child-Solver lifecycle and fair selection."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

from tga.contracts import SolverRecord, SubagentOutput, SubagentRequest
from tga.evidence.store import EvidenceStore, utc_now
from tga.runtime.events import EventStore
from tga.runtime.subagents import merge_output
from tga.runtime.solver_session import SolverSessionState


class SolverPool:
    def __init__(self, *, store: EvidenceStore, run_root: str | Path, max_active: int = 3):
        self.store = store
        self.run_root = Path(run_root)
        self.max_active = max_active

    def start(self, request: SubagentRequest, *, model_name: str = "") -> SolverRecord:
        if request.role == "main":
            raise ValueError("main cannot be spawned as a subagent")
        parent = next((item for item in self.store.list_solvers(request.task_id) if item.id == request.parent_solver_id), None)
        if parent is None or parent.role != "main":
            raise ValueError("subagent parent must be the task's main solver")
        active = [item for item in self.store.list_solvers(request.task_id) if item.role != "main" and item.status in {"starting", "running", "waiting"}]
        if len(active) >= self.max_active:
            raise ValueError("active subagent budget exhausted")
        fingerprint = self.fingerprint(request)
        if any(item["fingerprint"] == fingerprint for item in self.store.list_subagents(request.task_id)):
            raise ValueError("equivalent subagent has already been scheduled")
        solver = SolverRecord(
            id=f"solver_{uuid4().hex[:12]}",
            task_id=request.task_id,
            role=request.role,
            status="running",
            model_name=model_name,
            parent_solver_id=request.parent_solver_id,
            started_at=utc_now(),
        )
        self.store.add_solver(solver)
        SolverSessionState(
            run_root=self.run_root,
            task_id=request.task_id,
            solver_id=solver.id,
        ).ensure(solver)
        self.store.add_subagent_request(request, solver_id=solver.id, fingerprint=fingerprint)
        workspace = self.workspace(solver.id, request.task_id)
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "request.json").write_text(
            request.model_dump_json(indent=2), encoding="utf-8"
        )
        EventStore(self.store).append(
            request.task_id,
            "SUBAGENT_STARTED",
            {"request_id": request.id, "role": request.role, "objective": request.objective, "max_actions": request.max_actions},
            solver_id=solver.id,
        )
        return solver

    def finish(self, request: SubagentRequest, output: SubagentOutput) -> None:
        merge_output(store=self.store, request=request, output=output)
        (self.workspace(output.solver_id, request.task_id) / "output.json").write_text(
            output.model_dump_json(indent=2), encoding="utf-8"
        )
        status = {"completed": "completed", "blocked": "waiting", "failed": "failed"}[output.status]
        self.store.update_solver(output.solver_id, status=status, finished_at=utc_now())

    def recover(self, task_id: str) -> list[SolverRecord]:
        """Clear interrupted actions and leave child Solvers safely resumable."""
        for action in self.store.list_actions(task_id):
            if action.get("status") == "running" and not action.get("result"):
                self.store.update_action_status(action["id"], "cancelled")
                EventStore(self.store).append(
                    task_id,
                    "ACTION_FINISHED",
                    {"action_id": action["id"], "status": "cancelled", "summary": "interrupted action recovered without replay"},
                    solver_id=action.get("solver_id"),
                )
        recovered: list[SolverRecord] = []
        for solver in self.store.list_solvers(task_id):
            if solver.status in {"starting", "running"}:
                recovered.append(self.store.update_solver(solver.id, status="waiting"))
        return recovered

    def resume_all(self, task_id: str) -> None:
        for solver in self.store.list_solvers(task_id):
            if solver.status == "waiting":
                self.store.update_solver(solver.id, status="running", finished_at=None)
                self.store.update_subagent_status(solver.id, "running")

    def stop_all(self, task_id: str, *, status: str, reason: str) -> None:
        terminal = status if status in {"completed", "failed", "cancelled"} else "waiting"
        events = EventStore(self.store)
        for solver in self.store.list_solvers(task_id):
            if solver.status not in {"completed", "failed", "cancelled"}:
                self.store.update_solver(solver.id, status=terminal, finished_at=utc_now())
                events.append(task_id, "SOLVER_STOPPED", {"status": terminal, "reason": reason}, solver_id=solver.id)
                if status not in {"completed", "failed", "cancelled"}:
                    self.store.update_subagent_status(solver.id, "waiting")
        if status not in {"completed", "failed", "cancelled"}:
            return
        output_status = "completed" if status == "completed" else "failed" if status == "failed" else "blocked"
        actions = self.store.list_actions(task_id)
        for record in self.store.list_subagents(task_id):
            if record["status"] != "running" or record.get("output") is not None:
                continue
            request = SubagentRequest.model_validate(record["request"])
            artifact_ids = list(dict.fromkeys(
                artifact_id
                for action in actions
                if action.get("solver_id") == record["solver_id"]
                for artifact_id in ((action.get("result") or {}).get("artifact_ids") or [])
            ))
            output = SubagentOutput(
                request_id=request.id,
                solver_id=record["solver_id"],
                status=output_status,
                artifact_ids=artifact_ids,
                coverage_gaps=[reason] if output_status == "blocked" else [],
                next_recommendation=reason,
            )
            self.store.finish_subagent_request(output)
            (self.workspace(record["solver_id"], task_id) / "output.json").write_text(
                output.model_dump_json(indent=2), encoding="utf-8"
            )
            events.append(
                task_id,
                "SUBAGENT_FINISHED",
                {"request_id": request.id, "role": request.role, "status": output.status, "reason": reason},
                solver_id=record["solver_id"],
            )

    def next_solver(self, task_id: str) -> SolverRecord | None:
        candidates = [item for item in self.store.list_solvers(task_id) if item.role != "main" and item.status in {"running", "waiting"}]
        if not candidates:
            return None
        counts = {item.id: 0 for item in candidates}
        for action in self.store.list_actions(task_id):
            if action.get("solver_id") in counts:
                counts[action["solver_id"]] += 1
        return min(candidates, key=lambda item: (counts[item.id], item.started_at or "", item.id))

    def workspace(self, solver_id: str, task_id: str) -> Path:
        root = (self.run_root / task_id / "solvers").resolve()
        candidate = (root / solver_id).resolve()
        candidate.relative_to(root)
        return candidate

    @staticmethod
    def fingerprint(request: SubagentRequest) -> str:
        # Objective wording is intentionally excluded: rephrasing the same
        # route must not bypass duplicate-spawn protection.
        payload = [request.role, sorted(request.hypothesis_ids), sorted(request.input_artifact_ids)]
        return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
