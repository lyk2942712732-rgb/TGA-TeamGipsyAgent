from __future__ import annotations

from tga.contracts import Hypothesis, TGATask
from tga.models.base import ModelResponse
from tga.runtime.solver import LLMRuntimeSolver, MainSolver, build_runtime_solver


class FakeModel:
    model = "fake-runtime-model"

    def __init__(self, content: str) -> None:
        self.content = content
        self.messages = []

    def chat(self, messages, *, temperature=0.2):
        self.messages = messages
        return ModelResponse(content=self.content, model=self.model, raw={})


def _task() -> TGATask:
    return TGATask(id="runtime_llm", name="runtime", mode="ctf", target="http://127.0.0.1:8080", scope=["127.0.0.1:8080"], goal="solve")


def _hypothesis() -> Hypothesis:
    return Hypothesis(id="hyp_runtime", task_id="runtime_llm", statement="The observed login form can be checked with its documented method.", attack_class="web", entry_point="http://127.0.0.1:8080/login", rationale="A POST form was observed.", next_test="Inspect the form artifact before selecting a parameter.", confidence=0.6, created_at="2026-01-01T00:00:00Z", updated_at="2026-01-01T00:00:00Z")


def test_llm_runtime_solver_proposes_a_typed_evidence_linked_action() -> None:
    model = FakeModel('{"action":{"kind":"workspace","capability":"artifact.inspect","arguments":{"artifact_id":"artifact_123456789abc","query":"form"},"rationale":"Inspect the observed form artifact.","risk":"passive"}}')
    solver = LLMRuntimeSolver(model)

    action = solver.propose_action(task=_task(), solver_id="solver_1", hypothesis=_hypothesis(), snapshot={"recent_actions": [{"result": {"artifact_ids": ["artifact_123456789abc"]}}]})

    assert action is not None
    assert action.capability == "artifact.inspect"
    assert action.hypothesis_id == "hyp_runtime"
    assert action.target == "http://127.0.0.1:8080"
    assert model.messages


def test_llm_runtime_solver_reports_invalid_or_deferred_plans_without_execution() -> None:
    solver = LLMRuntimeSolver(FakeModel('{"no_action_reason":"Need a user hint describing the encrypted blob."}'))

    action = solver.propose_action(task=_task(), solver_id="solver_1", hypothesis=_hypothesis(), snapshot={})

    assert action is None
    assert "encrypted blob" in solver.last_plan_reason


def test_llm_runtime_solver_treats_invalid_json_as_bounded_empty_plan() -> None:
    solver = LLMRuntimeSolver(FakeModel("this is not JSON"))

    action = solver.propose_action(task=_task(), solver_id="solver_1", hypothesis=_hypothesis(), snapshot={})

    assert action is None
    assert "no JSON object" in solver.last_plan_reason


def test_runtime_solver_factory_uses_local_fallback_without_llm(monkeypatch) -> None:
    import tga.runtime.solver as solver_module

    monkeypatch.setattr(solver_module, "build_model_client_from_env", lambda: None)

    assert isinstance(build_runtime_solver(), MainSolver)


def test_runtime_solver_factory_prefers_configured_model(monkeypatch) -> None:
    import tga.runtime.solver as solver_module

    model = FakeModel('{"no_action_reason":"unused"}')
    monkeypatch.setattr(solver_module, "build_model_client_from_env", lambda: model)

    solver = build_runtime_solver()

    assert isinstance(solver, LLMRuntimeSolver)
    assert solver.model_name == "fake-runtime-model"
