import sys
from types import ModuleType, SimpleNamespace

import pytest

from tga3.host_agents import OpenAIHostModel, SupervisorDecision


class FakeAgent:
    last_kwargs: dict = {}

    def __init__(self, **kwargs):
        type(self).last_kwargs = kwargs


class FakeRunner:
    final_output = None

    @classmethod
    async def run(cls, _agent, _snapshot, *, max_turns):
        assert max_turns == 3
        return SimpleNamespace(final_output=cls.final_output)


def fake_agents_module() -> ModuleType:
    module = ModuleType("agents")
    module.Agent = FakeAgent
    module.Runner = FakeRunner
    module.function_tool = lambda function: function
    module.set_tracing_disabled = lambda _value: None
    return module


def binding(protocol: str) -> SimpleNamespace:
    return SimpleNamespace(
        display_name="Supervisor",
        protocol=protocol,
        system_prompt="Coordinate workers.",
        max_turns_per_cycle=3,
    )


@pytest.mark.asyncio
async def test_chat_compatible_supervisor_uses_text_json_and_pydantic(monkeypatch):
    monkeypatch.setitem(sys.modules, "agents", fake_agents_module())
    monkeypatch.setattr(OpenAIHostModel, "_model", staticmethod(lambda _binding: "model"))
    FakeRunner.final_output = """```json
    {
      "progress":"working",
      "advice":"check input",
      "advice_reason":"user_request",
      "addressed_to":["worker-openai"],
      "question":null
    }
    ```"""
    model = OpenAIHostModel(SimpleNamespace(), SimpleNamespace(list=lambda: [], read=lambda _name: ""))

    decision = await model.supervisor("[]", binding("openai_chat_completions"))

    assert decision == SupervisorDecision(
        progress="working",
        advice="check input",
        advice_reason="user_request",
        addressed_to=["worker-openai"],
    )
    assert "output_type" not in FakeAgent.last_kwargs
    assert "最终回复必须是一个 JSON 对象" in FakeAgent.last_kwargs["instructions"]
    assert '"progress"' in FakeAgent.last_kwargs["instructions"]


@pytest.mark.asyncio
async def test_responses_supervisor_keeps_native_structured_output(monkeypatch):
    monkeypatch.setitem(sys.modules, "agents", fake_agents_module())
    monkeypatch.setattr(OpenAIHostModel, "_model", staticmethod(lambda _binding: "model"))
    FakeRunner.final_output = SupervisorDecision(progress="native")
    model = OpenAIHostModel(SimpleNamespace(), SimpleNamespace(list=lambda: [], read=lambda _name: ""))

    decision = await model.supervisor("[]", binding("openai_responses"))

    assert decision.progress == "native"
    assert FakeAgent.last_kwargs["output_type"] is SupervisorDecision
    assert FakeAgent.last_kwargs["instructions"] == "Coordinate workers."


def test_supervisor_text_json_must_match_schema():
    with pytest.raises(ValueError):
        OpenAIHostModel._parse_supervisor_decision('{"advice":"missing progress"}')


def test_supervisor_normalizes_nullable_addressed_to():
    decision = OpenAIHostModel._parse_supervisor_decision(
        '{"progress":"done","advice":null,"addressed_to":null,"question":null}'
    )

    assert decision.addressed_to == []


def test_supervisor_requires_a_reason_for_each_advice():
    with pytest.raises(ValueError, match="advice and advice_reason"):
        SupervisorDecision(progress="working", advice="change direction")
