"""JSONL RPC client for an isolated, persistent Solver process."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from typing import Any
from uuid import uuid4

from tga.contracts import ActionResult, ActionSpec, Hypothesis, TGATask
from tga.runtime.board import HypothesisDraft
from tga.runtime.solver import SolverInterpretation


class SolverProcess:
    """One OS process, one model session, one Solver identity."""

    def __init__(self, solver_id: str):
        self.solver_id = solver_id
        self.last_plan_reason = ""
        self.last_plan_failure_kind = ""
        self._lock = threading.Lock()
        self._process = subprocess.Popen(
            [sys.executable, "-u", "-m", "tga.runtime.solver_worker", "--solver-id", solver_id],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        hello = self._read()
        if hello.get("type") != "ready":
            self.close()
            raise RuntimeError(f"solver process did not become ready: {hello}")
        self.model_name = str(hello.get("model_name") or "isolated-solver")

    def initial_hypotheses(self, *, task: TGATask, solver_id: str) -> list[HypothesisDraft]:
        values = self._call("initial_hypotheses", {"task": task.model_dump(mode="json")}) or []
        return [HypothesisDraft(**item) for item in values]

    def propose_action(
        self, *, task: TGATask, solver_id: str, hypothesis: Hypothesis, snapshot: dict,
    ) -> ActionSpec | None:
        response = self._call(
            "propose_action",
            {
                "task": task.model_dump(mode="json"),
                "hypothesis": hypothesis.model_dump(mode="json"),
                "snapshot": snapshot,
            },
            include_meta=True,
        )
        result, meta = response
        self.last_plan_reason = str(meta.get("last_plan_reason") or "")
        self.last_plan_failure_kind = str(meta.get("last_plan_failure_kind") or "")
        return ActionSpec.model_validate(result) if result else None

    @staticmethod
    def result_summary(*, hypothesis: Hypothesis, result: ActionResult) -> str:
        return (result.summary or "No execution summary was returned.")[:800]

    @staticmethod
    def interpret_result(*, hypothesis: Hypothesis, result: ActionResult) -> SolverInterpretation:
        if hypothesis.attack_class == "recon" and result.status == "succeeded" and result.artifact_ids:
            return SolverInterpretation(status="verified", last_result=result.summary[:800], decisive=True)
        return SolverInterpretation(last_result=result.summary[:800])

    def close(self) -> None:
        process = getattr(self, "_process", None)
        if process is None or process.poll() is not None:
            return
        try:
            self._call("shutdown", {})
        except Exception:
            process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)

    def _call(self, method: str, params: dict[str, Any], *, include_meta: bool = False):
        request_id = f"rpc_{uuid4().hex[:12]}"
        with self._lock:
            if self._process.poll() is not None:
                error = self._process.stderr.read()[-2000:] if self._process.stderr else ""
                raise RuntimeError(f"solver process exited {self._process.returncode}: {error}")
            assert self._process.stdin is not None
            self._process.stdin.write(json.dumps({"id": request_id, "method": method, "params": params}, ensure_ascii=False) + "\n")
            self._process.stdin.flush()
            response = self._read()
        if response.get("id") != request_id:
            raise RuntimeError("solver RPC response id mismatch")
        if not response.get("ok"):
            raise RuntimeError(str(response.get("error") or "solver RPC failed"))
        if include_meta:
            return response.get("result"), response.get("meta") or {}
        return response.get("result")

    def _read(self) -> dict[str, Any]:
        assert self._process.stdout is not None
        line = self._process.stdout.readline()
        if not line:
            error = self._process.stderr.read()[-2000:] if self._process.stderr else ""
            raise RuntimeError(f"solver process closed its RPC stream: {error}")
        value = json.loads(line)
        if not isinstance(value, dict):
            raise RuntimeError("solver RPC response must be an object")
        return value


def build_solver_process(solver_id: str) -> SolverProcess:
    return SolverProcess(solver_id)
