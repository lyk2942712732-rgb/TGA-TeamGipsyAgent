"""Persistent Solver worker speaking one JSON object per stdio line."""

from __future__ import annotations

import argparse
import json
import sys
import traceback

from tga.contracts import Hypothesis, TGATask
from tga.runtime.solver import build_runtime_solver


def _write(value: dict) -> None:
    sys.stdout.write(json.dumps(value, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--solver-id", required=True)
    args = parser.parse_args()
    solver = build_runtime_solver()
    _write({"type": "ready", "solver_id": args.solver_id, "model_name": solver.model_name})
    for line in sys.stdin:
        request = None
        try:
            request = json.loads(line)
            request_id = request.get("id")
            method = request.get("method")
            params = request.get("params") or {}
            if method == "shutdown":
                _write({"id": request_id, "ok": True, "result": {"stopped": True}})
                return 0
            task = TGATask.model_validate(params.get("task"))
            if method == "initial_hypotheses":
                result = [item.__dict__ for item in solver.initial_hypotheses(task=task, solver_id=args.solver_id)]
            elif method == "propose_action":
                hypothesis = Hypothesis.model_validate(params.get("hypothesis"))
                action = solver.propose_action(
                    task=task, solver_id=args.solver_id, hypothesis=hypothesis,
                    snapshot=params.get("snapshot") or {},
                )
                result = action.model_dump(mode="json") if action is not None else None
            else:
                raise ValueError(f"unknown solver RPC method: {method}")
            _write({
                "id": request_id,
                "ok": True,
                "result": result,
                "meta": {
                    "last_plan_reason": str(getattr(solver, "last_plan_reason", "")),
                    "last_plan_failure_kind": str(getattr(solver, "last_plan_failure_kind", "")),
                },
            })
        except Exception as exc:
            _write({
                "id": request.get("id") if isinstance(request, dict) else None,
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "trace": traceback.format_exc(limit=4),
            })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
