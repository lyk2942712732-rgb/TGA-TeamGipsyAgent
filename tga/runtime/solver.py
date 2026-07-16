"""Small solver protocol; execution is always delegated to an ActionExecutor."""

from __future__ import annotations

import json
from typing import Any, Protocol
from uuid import uuid4

from tga.contracts import ActionResult, ActionSpec, Hypothesis, TGATask
from tga.models.base import ModelClient, ModelMessage
from tga.models.bootstrap import build_model_client_from_env
from tga.runtime.board import HypothesisDraft


class Solver(Protocol):
    model_name: str

    def initial_hypotheses(self, *, task: TGATask, solver_id: str) -> list[HypothesisDraft]: ...

    def propose_action(self, *, task: TGATask, solver_id: str, hypothesis: Hypothesis, snapshot: dict) -> ActionSpec | None: ...

    def result_summary(self, *, hypothesis: Hypothesis, result: ActionResult) -> str: ...

    def interpret_result(self, *, hypothesis: Hypothesis, result: ActionResult) -> "SolverInterpretation": ...


class SolverInterpretation:
    """A solver-proposed, evidence-bounded update for its tested hypothesis."""

    def __init__(self, *, status: str | None = None, last_result: str = "", decisive: bool = False):
        self.status = status
        self.last_result = last_result
        self.decisive = decisive


class MainSolver:
    """A conservative fallback when no configured model is available."""

    model_name = "deterministic-main"

    def initial_hypotheses(self, *, task: TGATask, solver_id: str) -> list[HypothesisDraft]:
        entry = task.target.rstrip("/") or task.target
        return [
            HypothesisDraft(
                statement="The landing surface exposes a reachable interaction contract.", attack_class="recon",
                entry_point=entry, rationale="No verified endpoint inventory exists yet.",
                next_test="Make one in-scope passive HTTP request to observe the landing surface.", confidence=0.8,
            ),
            HypothesisDraft(
                statement="Observed inputs may support an evidence-backed CTF attack path.", attack_class="web",
                entry_point=entry, rationale="This is a candidate only until an input is observed.",
                next_test="Inspect the first observed form or link before selecting a targeted verification action.", confidence=0.3,
            ),
        ]

    def propose_action(self, *, task: TGATask, solver_id: str, hypothesis: Hypothesis, snapshot: dict) -> ActionSpec | None:
        if hypothesis.attack_class != "recon":
            self.last_plan_reason = "No runtime model is configured; add a hint or configure a model to select the next evidence-backed action."
            return None
        return ActionSpec(
            id=f"act_{uuid4().hex[:12]}", task_id=task.id, solver_id=solver_id, hypothesis_id=hypothesis.id,
            kind="http", capability="http.request", target=task.target, arguments={"method": "GET", "path": "/"},
            rationale=hypothesis.next_test, risk="passive",
        )

    def result_summary(self, *, hypothesis: Hypothesis, result: ActionResult) -> str:
        return (result.summary or "No execution summary was returned.")[:800]

    def interpret_result(self, *, hypothesis: Hypothesis, result: ActionResult) -> SolverInterpretation:
        if hypothesis.attack_class == "recon" and result.status == "succeeded" and result.artifact_ids:
            return SolverInterpretation(status="verified", last_result=self.result_summary(hypothesis=hypothesis, result=result), decisive=True)
        # A failed payload narrows a boundary, but does not reject a whole
        # attack class. Retry limits are enforced by the manager.
        return SolverInterpretation(last_result=self.result_summary(hypothesis=hypothesis, result=result))


class LLMRuntimeSolver(MainSolver):
    """Persistent-runtime solver that turns compact evidence into one ActionSpec.

    The model never executes a command directly.  It only proposes a typed
    action, which still traverses Manager ownership checks and B's capability,
    scope, budget, timeout and artifact controls.
    """

    def __init__(self, client: ModelClient):
        self.client = client
        self.model_name = getattr(client, "model", "configured-runtime-model")
        self.last_plan_reason = ""

    def propose_action(self, *, task: TGATask, solver_id: str, hypothesis: Hypothesis, snapshot: dict) -> ActionSpec | None:
        prompt = {
            "role": "TGA persistent solver",
            "contract": {
                "response": "JSON only: {action:{kind,capability,target,arguments,rationale,risk}} or {no_action_reason:string}",
                "constraints": [
                    "propose exactly one minimal evidence-linked action",
                    "never use shell commands or claim a flag/finding",
                    "use artifact.inspect before relying on an artifact summary that is insufficient",
                    "do not repeat the same semantic action after a failure boundary",
                    "stay inside task scope and listed capabilities",
                ],
            },
            "active_hypothesis": hypothesis.model_dump(mode="json"),
            "context": snapshot,
        }
        try:
            response = self.client.chat(
                [
                    ModelMessage(role="system", content="You are a controlled cybersecurity task solver. Return only the requested JSON object."),
                    ModelMessage(role="user", content=json.dumps(prompt, ensure_ascii=False)),
                ],
                temperature=0.1,
            )
            raw = _json_object(response.content)
            payload = json.loads(raw)
        except Exception as exc:  # model failure remains an auditable empty plan, never an execution bypass
            self.last_plan_reason = f"runtime model plan failed: {str(exc)[:300]}"
            return None
        if not isinstance(payload, dict):
            self.last_plan_reason = "runtime model returned a non-object action plan"
            return None
        if payload.get("no_action_reason"):
            self.last_plan_reason = str(payload["no_action_reason"])[:500]
            return None
        raw_action = payload.get("action")
        if not isinstance(raw_action, dict):
            self.last_plan_reason = "runtime model did not provide an action object"
            return None
        try:
            action = ActionSpec.model_validate(
                {
                    "id": f"act_{uuid4().hex[:12]}",
                    "task_id": task.id,
                    "solver_id": solver_id,
                    "hypothesis_id": hypothesis.id,
                    "kind": raw_action.get("kind"),
                    "capability": raw_action.get("capability"),
                    "target": raw_action.get("target") or task.target,
                    "arguments": raw_action.get("arguments") or {},
                    "rationale": raw_action.get("rationale") or hypothesis.next_test,
                    "risk": raw_action.get("risk") or "passive",
                }
            )
        except Exception as exc:
            self.last_plan_reason = f"runtime model proposed an invalid ActionSpec: {str(exc)[:300]}"
            return None
        self.last_plan_reason = ""
        return action


def build_runtime_solver() -> Solver:
    """Prefer a configured persistent model; retain a truthful local fallback."""
    client = build_model_client_from_env()
    return LLMRuntimeSolver(client) if client is not None else MainSolver()


def _json_object(text: str) -> str:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in model response")
    return text[start : end + 1]
