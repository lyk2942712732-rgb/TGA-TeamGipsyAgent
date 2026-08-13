from __future__ import annotations

import ast
import io
import json
import shutil
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command
from pydantic import Field

from apps.api.main import app
from tga2.agent.middleware import (
    _bounded_network_command,
    _high_impact_action,
    _network_observation_key,
)
from tga2.agent.roles import LangChainAgentSuite, OfflineAgentSuite, RoutedAgentSuite
from tga2.agent.schemas import (
    PlanDraft,
    PlanIntentDraft,
    ReportDraft,
    ReviewDraft,
    SupervisorDecision,
    WorkerDraft,
)
from tga2.agent.service import TaskRuntimeService
from tga2.bootstrap import get_container, reset_containers
from tga2.config import DEFAULT_KALI_IMAGE, DEFAULT_KALI_IMAGE_DIGEST
from tga2.core.models import CreateTaskRequest, Task, TaskSpec
from tga2.core.policy import ExecutionPolicy, ToolPolicy
from tga2.integrations.mcp import MCPConfig, MCPServer
from tga2.integrations.model import ModelSettings


class _VerifiedResponse:
    id = "verification-response"


class _VerificationModel:
    def with_structured_output(self, _schema, **_kwargs):
        return self

    def invoke(self, _prompt):
        from tga2.agent.schemas import PlanDraft, PlanIntentDraft

        rendered = "\n".join(
            str(item.get("content", ""))
            if isinstance(item, dict)
            else str(getattr(item, "content", item))
            for item in _prompt
        )
        assert '"intents"' in rendered
        assert '"priority"' in rendered
        assert "do not rename fields" in rendered
        return {
            "raw": _VerifiedResponse(),
            "parsed": PlanDraft(
                summary="Verified",
                intents=[
                    PlanIntentDraft(
                        title="Inspect",
                        objective="Inspect input",
                        success_criteria=["The input is inspected."],
                        expected_evidence=["An Artifact-backed input observation."],
                    )
                ],
            ),
            "parsing_error": None,
        }


class _CheckpointSuite(OfflineAgentSuite):
    def __init__(self) -> None:
        self.reviews = 0
        self.worker_attempts: list[int] = []
        self.retry_contexts: list[dict] = []

    def plan(self, _task):
        return PlanDraft(
            summary="Two bounded intents",
            intents=[
                PlanIntentDraft(
                    title="First",
                    objective="Inspect first",
                    success_criteria=["The first item is inspected."],
                    expected_evidence=["A first-item observation."],
                ),
                PlanIntentDraft(
                    title="Second",
                    objective="Inspect second",
                    success_criteria=["The second item is inspected."],
                    expected_evidence=["A second-item observation."],
                ),
            ],
        )

    def work(self, task, intent, tools, feedback, middleware=()):
        self.worker_attempts.append(int(intent["attempt"]))
        self.retry_contexts.append(intent.get("retry_context") or {})
        return WorkerDraft(
            summary=f"Attempt {intent['attempt']}",
            completion_status="completed",
            criterion_assessments=[
                {
                    "criterion_index": 0,
                    "status": "met",
                    "artifact_ids": [],
                    "note": "Test criterion completed.",
                }
            ],
        )

    def review(self, task, packet):
        self.reviews += 1
        if self.reviews == 1:
            return ReviewDraft(
                verdict="retry",
                reason_codes=["insufficient_evidence"],
                feedback="Collect stronger evidence.",
                criterion_results=[
                    {
                        "criterion_index": 0,
                        "status": "not_verified",
                        "reason": "First review intentionally retries.",
                    }
                ],
            )
        return ReviewDraft(
            verdict="pass",
            feedback="Accepted.",
            criterion_results=[
                {
                    "criterion_index": 0,
                    "status": "verified",
                    "reason": "Acceptance criterion verified.",
                }
            ],
        )

    def decide(self, task, packet):
        verdict = (packet.review_result or {}).get("verdict")
        pending = [
            item for item in packet.plan["intents"] if item["status"] == "pending"
        ]
        if verdict == "retry":
            return SupervisorDecision(
                action="retry", reason="Retry once.", feedback="Use reviewer feedback."
            )
        return SupervisorDecision(
            action="next_intent" if pending else "finish",
            reason="Continue the bounded plan.",
        )

    def report(self, task, snapshot):
        return ReportDraft(executive_summary="Checkpoint flow completed.")


class _AcceptanceArtifactSuite(OfflineAgentSuite):
    reviewer_claims: list[dict]

    def __init__(self) -> None:
        self.reviewer_claims = []

    def work(self, task, intent, tools, feedback, middleware=()):
        read_input = next(tool for tool in tools if tool.name == "read_input")
        payload = json.loads(read_input.invoke({"path": "target.txt"}))
        return WorkerDraft(
            summary="The input supports the acceptance criterion.",
            completion_status="completed",
            criterion_assessments=[
                {
                    "criterion_index": 0,
                    "status": "met",
                    "artifact_ids": [payload["artifact_id"]],
                    "note": "The target marker is present in the supplied input.",
                }
            ],
            claims=[],
        )

    def review(self, task, packet):
        self.reviewer_claims = [item.claim for item in packet.evidence]
        ids = [str(item["id"]) for item in self.reviewer_claims]
        return ReviewDraft(
            verdict="pass",
            feedback="The acceptance evidence is visible.",
            confirmed_claim_ids=ids,
            criterion_results=[
                {
                    "criterion_index": 0,
                    "status": "verified",
                    "evidence_claim_ids": ids,
                    "reason": "Artifact-backed acceptance evidence is present.",
                }
            ],
        )


def test_langchain_json_mode_receives_the_full_pydantic_schema() -> None:
    suite = LangChainAgentSuite(_VerificationModel())
    draft = suite.plan(
        Task(
            name="schema",
            mode="ctf",
            spec=TaskSpec(objective="Verify schema delivery"),
        )
    )
    assert draft.intents[0].priority == 50


class _RecordingToolModel(BaseChatModel):
    tool_choices: list[object | None] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "recording-tool-model"

    def bind_tools(self, tools, *, tool_choice=None, **_kwargs):
        self.tool_choices.append(tool_choice)
        return self

    def _generate(self, _messages, stop=None, run_manager=None, **_kwargs):
        return ChatResult(
            generations=[
                ChatGeneration(
                    message=AIMessage(
                        content=(
                            "```json\n"
                            '{"summary":"Checked with ordinary tools.",'
                            '"completion_status":"completed",'
                            '"criterion_assessments":[],'
                            '"claims":[],"limitations":[]}\n'
                            "```"
                        )
                    )
                )
            ]
        )


@tool
def _inspect_authorized_value(value: str) -> str:
    """Inspect an authorized test value."""

    return value


def test_deepseek_thinking_worker_does_not_force_tool_choice() -> None:
    model = _RecordingToolModel()
    suite = LangChainAgentSuite(model, force_prompt_worker_output=True)
    draft = suite.work(
        Task(
            name="deepseek tools",
            mode="ctf",
            spec=TaskSpec(objective="Use tools without forced tool_choice"),
        ),
        {"id": "intent-1", "objective": "Inspect target"},
        [_inspect_authorized_value],
        "",
    )

    assert model.tool_choices == [None]
    assert draft.summary == "Checked with ordinary tools."


class _ApprovalToolModel(BaseChatModel):
    calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "approval-tool-model"

    def bind_tools(self, tools, *, tool_choice=None, **_kwargs):
        return self

    def _generate(self, _messages, stop=None, run_manager=None, **_kwargs):
        self.calls += 1
        message = (
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "approval_probe", "args": {"value": "ok"}, "id": "call-1"}
                ],
            )
            if self.calls == 1
            else AIMessage(
                content=(
                    '{"summary":"approved once","completion_status":"completed",'
                    '"criterion_assessments":[{"criterion_index":0,"status":"met",'
                    '"artifact_ids":[],"note":"Approved tool ran."}],'
                    '"claims":[],"limitations":[]}'
                )
            )
        )
        return ChatResult(generations=[ChatGeneration(message=message)])


class _ToolUntilFinalModel(BaseChatModel):
    calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "tool-until-final-model"

    def bind_tools(self, tools, *, tool_choice=None, **_kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **_kwargs):
        self.calls += 1
        final_call = any(
            "INVESTIGATION COMPLETE" in str(getattr(message, "content", ""))
            for message in messages
        )
        message = (
            AIMessage(
                content=(
                    '{"summary":"bounded","completion_status":"completed",'
                    '"criterion_assessments":[],"claims":[],"limitations":[]}'
                )
            )
            if final_call
            else AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "budget_probe",
                        "args": {"value": str(self.calls)},
                        "id": f"budget-{self.calls}",
                    }
                ],
            )
        )
        return ChatResult(generations=[ChatGeneration(message=message)])


class _DeepSeekDsmlFinalizerModel(BaseChatModel):
    calls: int = 0
    finalizer_calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "deepseek-dsml-finalizer-model"

    def bind_tools(self, tools, *, tool_choice=None, **_kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **_kwargs):
        self.calls += 1
        is_finalizer = any(
            "INVESTIGATION COMPLETE" in str(getattr(message, "content", ""))
            for message in messages
        )
        if is_finalizer:
            self.finalizer_calls += 1
            content = (
                '<｜DSML｜tool_calls><｜DSML｜invoke name="budget_probe">'
                '<｜DSML｜parameter name="value" string="true">7'
                "</｜DSML｜parameter></｜DSML｜invoke></｜DSML｜tool_calls>"
                if self.finalizer_calls == 1
                else (
                    '{"summary":"recovered","completion_status":"completed",'
                    '"criterion_assessments":[],"claims":[],"limitations":[]}'
                )
            )
            message = AIMessage(content=content)
        else:
            message = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "budget_probe",
                        "args": {"value": str(self.calls)},
                        "id": f"dsml-budget-{self.calls}",
                    }
                ],
            )
        return ChatResult(generations=[ChatGeneration(message=message)])


class _WorkerLimitFailureSuite(OfflineAgentSuite):
    def work(self, *args, **kwargs):
        raise RuntimeError("Model call limits exceeded: run limit (8/8)")


class _UncheckedReviewSuite(OfflineAgentSuite):
    guarded_verdict: str | None = None

    def review(self, task, packet):
        return ReviewDraft(
            verdict="pass",
            feedback="Passed without checking the Intent criteria.",
            criterion_results=[],
        )

    def decide(self, task, packet):
        self.guarded_verdict = (packet.review_result or {}).get("verdict")
        return SupervisorDecision(
            action="fail",
            reason="Stop after exercising the deterministic review guard.",
        )


def test_worker_hitl_resumes_the_original_nested_tool_call_once() -> None:
    executed: list[str] = []

    @tool
    def approval_probe(value: str) -> str:
        """Record an approved test value."""

        executed.append(value)
        return value

    model = _ApprovalToolModel()
    suite = LangChainAgentSuite(model, force_prompt_worker_output=True)
    task = Task(name="approval", spec=TaskSpec(objective="approve one tool call"))

    def worker_node(_state: dict):
        draft = suite.work(
            task,
            {"id": "intent-1", "attempt": 1},
            [approval_probe],
            "",
            [HumanInTheLoopMiddleware({"approval_probe": True})],
        )
        return {"summary": draft.summary}

    builder = StateGraph(dict)
    builder.add_node("worker", worker_node)
    builder.add_edge(START, "worker")
    builder.add_edge("worker", END)
    graph = builder.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "outer-task"}}

    paused = graph.invoke({}, config=config)
    assert paused["__interrupt__"][0].value["kind"] == "tool_approval"
    assert model.calls == 1
    assert executed == []

    resumed = graph.invoke(
        Command(resume={"decisions": [{"type": "approve"}]}), config=config
    )
    assert resumed["summary"] == "approved once"
    assert model.calls == 2
    assert executed == ["ok"]


def test_runtime_approval_resume_does_not_duplicate_or_replay_tool(
    tmp_path: Path,
) -> None:
    executed: list[str] = []

    @tool
    def approval_probe(value: str) -> str:
        """Record an approved integration-test value."""

        executed.append(value)
        return value

    worker = LangChainAgentSuite(_ApprovalToolModel(), force_prompt_worker_output=True)
    offline = OfflineAgentSuite()
    service = TaskRuntimeService(
        run_root=tmp_path / "runs",
        agents=RoutedAgentSuite(
            {
                "supervisor": offline,
                "worker": worker,
                "reviewer": offline,
                "reporter": offline,
            }
        ),
        external_tools=[approval_probe],
    )
    made = service.create_task(
        CreateTaskRequest(
            name="approval integration",
            objective="Execute one governed tool",
            mode="vulnerability_research",
            execution_policy=ExecutionPolicy(
                tool=ToolPolicy(
                    allowed_tools=frozenset({"approval_probe"}),
                    approval_required=frozenset({"approval_probe"}),
                )
            ),
        )
    )

    paused = service.run_task(made["task_id"])
    snapshot = service.snapshot(made["task_id"])
    assert paused["status"] == "awaiting_approval"
    assert len(snapshot["approvals"]) == 1
    assert snapshot["approvals"][0]["action"]["arguments"] == {"value": "ok"}
    assert executed == []

    resumed = service.decide_tool_action(made["task_id"], "call-1", approved=True)
    snapshot = service.snapshot(made["task_id"])
    assert resumed["status"] == "completed"
    assert executed == ["ok"]
    assert snapshot["approvals"] == []
    assert [event["type"] for event in snapshot["events"]].count(
        "APPROVAL_REQUESTED"
    ) == 1
    assert (
        next(item for item in snapshot["actions"] if item["action_id"] == "call-1")[
            "status"
        ]
        == "succeeded"
    )


def test_high_impact_classifier_does_not_gate_read_only_shell_setup() -> None:
    assert _high_impact_action("pwd") is None
    assert _high_impact_action("id && curl -s http://target.test/") is None
    assert _high_impact_action("curl -X POST -d x=1 http://target.test/") == (
        "state_changing_http"
    )
    assert _high_impact_action("hydra -l admin -P passwords.txt ssh://target") == (
        "credential_attack"
    )
    assert _bounded_network_command("curl -s https://target.test/").startswith(
        "timeout 20s bash -lc "
    )
    assert (
        _bounded_network_command("curl --max-time 5 https://target.test/")
        == "curl --max-time 5 https://target.test/"
    )
    assert _network_observation_key("curl -skv https://TARGET.test/") == (
        "GET",
        "https://target.test/",
    )
    assert _network_observation_key("curl -k -i https://target.test/") == (
        "GET",
        "https://target.test/",
    )
    post_key = _network_observation_key(
        "curl -k -d 'code=id' https://target.test/"
    )
    assert post_key is not None
    assert post_key[0] == "POST"
    assert post_key[1].startswith("https://target.test/#payload-")
    assert post_key != _network_observation_key(
        "curl -k -d 'code=whoami' https://target.test/"
    )


def test_worker_uses_a_clean_finalizer_after_bounded_tool_investigation() -> None:
    executed: list[str] = []

    @tool
    def budget_probe(value: str) -> str:
        """Record one bounded investigation step."""

        executed.append(value)
        return value

    model = _ToolUntilFinalModel()
    suite = LangChainAgentSuite(
        model,
        model_call_limit=8,
        force_prompt_worker_output=True,
    )
    task = Task(name="budget", spec=TaskSpec(objective="finish within budget"))

    draft = suite.work(
        task,
        {"id": "intent-budget", "attempt": 1},
        [budget_probe],
        "",
    )

    assert draft.summary == "bounded"
    assert model.calls == 7
    assert executed == ["1", "2", "3", "4", "5", "6"]


def test_worker_corrects_deepseek_dsml_from_the_clean_finalizer() -> None:
    executed: list[str] = []

    @tool
    def budget_probe(value: str) -> str:
        """Record one bounded investigation step."""

        executed.append(value)
        return f"observation {value}; TGA artifact_id=artifact-{value}"

    model = _DeepSeekDsmlFinalizerModel()
    suite = LangChainAgentSuite(
        model,
        model_call_limit=8,
        force_prompt_worker_output=True,
    )
    task = Task(name="dsml", spec=TaskSpec(objective="finish after DSML"))

    draft = suite.work(
        task,
        {"id": "intent-dsml", "attempt": 1},
        [budget_probe],
        "",
    )

    assert draft.summary == "recovered"
    assert model.calls == 8
    assert model.finalizer_calls == 2
    assert executed == ["1", "2", "3", "4", "5", "6"]
    assert suite.take_model_calls("worker") == 8


def test_failure_marks_active_intent_and_solver_instead_of_leaving_running(
    tmp_path: Path,
) -> None:
    service = TaskRuntimeService(
        run_root=tmp_path / "runs", agents=_WorkerLimitFailureSuite()
    )
    made = service.create_task(
        CreateTaskRequest(
            name="worker limit",
            objective="exercise terminal failure projection",
            mode="vulnerability_research",
        )
    )

    with pytest.raises(RuntimeError, match="run limit"):
        service.run_task(made["task_id"])
    snapshot = service.snapshot(made["task_id"])

    assert snapshot["session"]["status"] == "failed"
    assert snapshot["session"]["task_budget_usage"]["model_calls"] == 8
    assert snapshot["intents"][0]["status"] == "failed"
    worker = next(item for item in snapshot["solvers"] if item["solver_id"] == "worker")
    supervisor = next(
        item for item in snapshot["solvers"] if item["solver_id"] == "supervisor"
    )
    assert worker["status"] == "failed"
    assert worker["assigned_intent_id"] == snapshot["intents"][0]["intent_id"]
    assert supervisor["status"] == "stopped"
    assert supervisor["assigned_intent_id"] is None


def test_workspace_gives_kali_a_writable_scratch_copy_of_inputs(
    tmp_path: Path,
) -> None:
    from tga2.core.workspace import TaskWorkspace

    source = tmp_path / "sample.bin"
    source.write_bytes(b"sample")
    workspace = TaskWorkspace(tmp_path / "runs", "task-scratch")
    workspace.ingest_input(source)

    sandbox_inputs = workspace.prepare_sandbox_inputs()

    assert (sandbox_inputs / "sample.bin").read_bytes() == b"sample"
    assert workspace.scratch.stat().st_mode & 0o777 == 0o777


def test_deepseek_thinking_capability_disables_forced_tool_choice() -> None:
    deepseek = ModelSettings(
        preset_id="deepseek",
        provider="openai",
        model="deepseek-v4-pro",
        base_url="https://api.deepseek.com",
        reasoning_mode="auto",
    )
    standard = ModelSettings(
        preset_id="openai",
        provider="openai",
        model="gpt-4.1-mini",
        reasoning_mode="disabled",
    )

    assert deepseek.supports_forced_tool_choice is False
    assert standard.supports_forced_tool_choice is True


class _AskUserSuite(OfflineAgentSuite):
    def __init__(self) -> None:
        self.decisions = 0

    def decide(self, task, packet):
        self.decisions += 1
        if self.decisions == 1:
            return SupervisorDecision(
                action="ask_user",
                reason="A target detail is missing.",
                user_question="Which authorized target should be used?",
            )
        return SupervisorDecision(action="finish", reason="User supplied the detail.")


@pytest.fixture(autouse=True)
def copy_tracked_configuration(tmp_path: Path) -> None:
    source = Path(__file__).parents[1] / "runs2" / ".config"
    shutil.copytree(source, tmp_path / "runs" / ".config")


def test_offline_vertical_slice(tmp_path: Path) -> None:
    source = tmp_path / "target.txt"
    source.write_text("TGA2 evidence marker", encoding="utf-8")
    service = TaskRuntimeService(run_root=tmp_path / "runs")
    made = service.create_task(
        CreateTaskRequest(
            name="offline",
            objective="Inspect target",
            mode="vulnerability_research",
            input_paths=[str(source)],
        )
    )
    result = service.run_task(made["task_id"])
    snapshot = service.snapshot(made["task_id"])
    assert result["status"] == "completed"
    assert snapshot["schema_version"] == 6
    assert len(snapshot["artifacts"]) == 1
    assert len(snapshot["evidence_claims"]) == 1
    assert len(snapshot["findings"]) == 1
    intent = snapshot["intents"][0]
    assert intent["success_criteria"]
    assert intent["expected_evidence"]
    assert "read_input" in intent["allowed_tools"]
    assert any("model calls" in item for item in intent["stop_conditions"])
    created = next(
        item for item in snapshot["events"] if item["type"] == "INTENT_CREATED"
    )
    assert created["payload"]["success_criteria"] == intent["success_criteria"]


def test_runtime_rejects_reviewer_pass_without_criterion_coverage(
    tmp_path: Path,
) -> None:
    suite = _UncheckedReviewSuite()
    service = TaskRuntimeService(run_root=tmp_path / "runs", agents=suite)
    made = service.create_task(
        CreateTaskRequest(
            name="review guard",
            objective="Ensure every Intent criterion is explicitly reviewed",
            mode="vulnerability_research",
        )
    )

    service.run_task(made["task_id"])
    snapshot = service.snapshot(made["task_id"])
    review = next(
        item for item in snapshot["events"] if item["type"] == "REVIEW_COMPLETED"
    )

    assert suite.guarded_verdict == "retry"
    assert review["payload"]["verdict"] == "retry"
    assert "incomplete_objective" in review["payload"]["reason_codes"]
    assert "Unverified Intent criteria: 1" in review["payload"]["feedback"]


def test_acceptance_artifact_is_promoted_to_reviewer_visible_claim(
    tmp_path: Path,
) -> None:
    source = tmp_path / "target.txt"
    source.write_text("acceptance marker", encoding="utf-8")
    suite = _AcceptanceArtifactSuite()
    service = TaskRuntimeService(run_root=tmp_path / "runs", agents=suite)
    made = service.create_task(
        CreateTaskRequest(
            name="acceptance evidence",
            objective="Inspect target",
            mode="vulnerability_research",
            input_paths=[str(source)],
        )
    )

    result = service.run_task(made["task_id"])
    snapshot = service.snapshot(made["task_id"])

    assert result["status"] == "completed"
    assert len(suite.reviewer_claims) == 1
    assert suite.reviewer_claims[0]["created_by"] == (
        "runtime_from_acceptance_assessment"
    )
    assert snapshot["evidence_claims"][0]["status"] == "confirmed"


def test_supervisor_checkpoint_retries_then_advances_plan(tmp_path: Path) -> None:
    service = TaskRuntimeService(run_root=tmp_path / "runs")
    suite = _CheckpointSuite()
    service.agents = suite
    made = service.create_task(
        CreateTaskRequest(
            name="checkpoint",
            objective="Exercise dynamic supervisor routing",
            mode="vulnerability_research",
        )
    )
    result = service.run_task(made["task_id"])
    snapshot = service.snapshot(made["task_id"])

    assert result["status"] == "completed"
    assert suite.worker_attempts == [1, 2, 1]
    assert suite.retry_contexts[1]["previous_reviews"][0]["feedback"] == (
        "Collect stronger evidence."
    )
    assert snapshot["global_plan"]["version"] == 1
    assert len(snapshot["intents"]) == 2
    assert all(item["status"] == "completed" for item in snapshot["intents"])
    event_types = [item["type"] for item in snapshot["events"]]
    assert event_types.count("SUPERVISOR_DECIDED") == 3
    assert "INTENT_RETRY_REQUESTED" in event_types


def test_supervisor_user_input_interrupt_has_distinct_status_and_resumes(
    tmp_path: Path,
) -> None:
    service = TaskRuntimeService(run_root=tmp_path / "runs")
    suite = _AskUserSuite()
    service.agents = suite
    made = service.create_task(
        CreateTaskRequest(
            name="user input",
            objective="Exercise the explicit user-input checkpoint",
            mode="vulnerability_research",
        )
    )

    paused = service.run_task(made["task_id"])
    assert paused["status"] == "awaiting_user_input"
    assert paused["interrupts"][0]["kind"] == "user_input"
    paused_snapshot = service.snapshot(made["task_id"])
    assert paused_snapshot["session"]["status"] == "awaiting_user_input"
    assert paused_snapshot["session"]["user_input_request"]["question"] == (
        "Which authorized target should be used?"
    )
    supervisor = next(
        item for item in paused_snapshot["solvers"] if item["solver_id"] == "supervisor"
    )
    assert supervisor["status"] == "awaiting_user_input"
    assert supervisor["current_summary"] == "Which authorized target should be used?"

    resumed = service.resume_task(
        made["task_id"], {"content": "Use 192.0.2.10 within the existing scope."}
    )
    assert resumed["status"] == "completed"
    event_types = [item["type"] for item in service.snapshot(made["task_id"])["events"]]
    assert "USER_INPUT_REQUIRED" in event_types
    assert "USER_INPUT_RECEIVED" in event_types


def test_runtime_json_is_the_single_budget_source(tmp_path: Path) -> None:
    service = TaskRuntimeService(run_root=tmp_path / "runs")
    runtime = service.configuration.runtime
    assert runtime.schema_version == 5
    assert runtime.budget.task.model_dump() == {
        "max_intents": 4,
        "max_model_calls": 60,
        "max_tool_calls": 80,
        "max_duration_minutes": 20,
    }
    assert runtime.budget.intent.max_attempts == 3
    assert runtime.budget.roles.worker.calls_per_attempt == 8
    assert runtime.budget.roles.worker.tool_calls_per_attempt == 15
    payload = json.loads((tmp_path / "runs" / ".config" / "runtime.json").read_text())
    assert "model_call_limit" not in payload["roles"]["worker"]
    assert "max_calls" not in payload["tool_defaults"]


def test_apps_api_is_the_only_http_boundary(tmp_path: Path) -> None:
    reset_containers()
    app.state.container = get_container(tmp_path / "runs")
    client = TestClient(app)
    created = client.post(
        "/api/v2/tasks",
        json={
            "id": "browser-id",
            "name": "browser",
            "mode": "vulnerability_research",
            "goal": "Inspect target",
            "input": {"text": "Use evidence", "fileIds": []},
            "executionPolicy": {
                "network": {"access": "disabled"},
                "local_compute": {"mode": "disabled", "timeout_seconds": 120},
            },
        },
    )
    assert created.status_code == 201
    assert client.get(f"/api/v2/tasks/{created.json()['task_id']}").status_code == 200
    assert (
        client.get(f"/api/v2/tasks/{created.json()['task_id']}/session").status_code
        == 404
    )
    assert not (Path(__file__).parents[1] / "tga2" / "api.py").exists()


def test_built_spa_supports_direct_history_route_access() -> None:
    client = TestClient(app)

    solver_page = client.get("/settings/solvers")
    runtime_page = client.get("/tasks/task_example/runtime?tab=evidence")

    assert solver_page.status_code == 200
    assert runtime_page.status_code == 200
    assert '<div id="root"></div>' in solver_page.text
    assert '<div id="root"></div>' in runtime_page.text
    assert client.get("/api/v2/does-not-exist").status_code == 404
    assert client.get("/assets/does-not-exist.js").status_code == 404


def test_code_audit_mode_is_not_exposed_or_accepted(tmp_path: Path) -> None:
    reset_containers()
    app.state.container = get_container(tmp_path / "runs")
    client = TestClient(app)
    teams = client.get("/api/v2/catalog/teams").json()["items"]
    assert "code_audit" not in {item["mode"] for item in teams}
    rejected = client.post(
        "/api/v2/tasks",
        json={
            "name": "removed-mode",
            "mode": "code_audit",
            "goal": "must be rejected",
            "input": {"text": "", "fileIds": []},
        },
    )
    assert rejected.status_code == 422


def test_resource_report_and_policy_catalogs_match_frontend_contracts(
    tmp_path: Path,
) -> None:
    reset_containers()
    app.state.container = get_container(tmp_path / "runs")
    client = TestClient(app)
    source = tmp_path / "target.txt"
    source.write_text("catalog evidence", encoding="utf-8")
    made = app.state.container.runtime.create_task(
        CreateTaskRequest(
            name="catalog task",
            objective="Inspect catalog evidence",
            mode="vulnerability_research",
            input_paths=[str(source)],
        )
    )
    app.state.container.runtime.run_task(made["task_id"])

    resources = client.get("/api/v2/catalog/resources").json()["items"]
    assert {item["kind"] for item in resources} == {
        "artifacts",
        "evidence",
        "findings",
    }
    assert all(
        {"id", "task_id", "task_name", "title", "status", "raw"} <= item.keys()
        for item in resources
    )

    reports = client.get("/api/v2/catalog/reports").json()["items"]
    assert reports == [
        {
            "id": f"report-{made['task_id']}",
            "task_id": made["task_id"],
            "task_name": "catalog task",
            "title": "catalog task 报告",
            "mode": "vulnerability_research",
            "status": "final",
            "findings": 1,
            "updated_at": reports[0]["updated_at"],
        }
    ]

    policies = client.get("/api/v2/catalog/policies").json()["items"]
    assert len({item["id"] for item in policies}) == len(policies)


def test_prompt_skill_and_solver_settings_reach_runtime(tmp_path: Path) -> None:
    reset_containers()
    app.state.container = get_container(tmp_path / "runs")
    client = TestClient(app)
    prompt = client.put(
        "/api/v2/settings/agent-prompts",
        json={
            "schema_version": 1,
            "common_system_prompt": "competition prompt",
            "modes": [
                {
                    "id": "vulnerability_research",
                    "label": "Vulnerability research",
                    "methodology": ["Trace data flow"],
                    "completion_focus": "Require evidence",
                    "observer_focus": "Reject unsupported claims",
                }
            ],
        },
    )
    assert prompt.status_code == 200
    assert (
        app.state.container.configuration.runtime.common_prompt == "competition prompt"
    )
    assert app.state.container.configuration.scene("vulnerability_research")["prompts"][
        "methodology"
    ] == ["Trace data flow"]
    created = client.post(
        "/api/v2/settings/skills",
        json={
            "name": "evidence",
            "description": "Evidence handling",
            "tags": ["evidence"],
            "version": "1",
            "instructions": "# Evidence\n\nRead the locator guide when needed.",
        },
    )
    assert created.status_code == 201
    added = client.put(
        "/api/v2/settings/skills/evidence/documents",
        json={"path": "references/locators.md", "content": "# Locators\nUse lines."},
    )
    assert added.status_code == 200
    package = app.state.container.runtime.skills.get_required("evidence")
    assert [item.path for item in package.documents] == [
        "SKILL.md",
        "references/locators.md",
    ]
    read = client.get(
        "/api/v2/settings/skills/evidence/documents/references/locators.md"
    )
    assert read.json()["document"]["content"] == "# Locators\nUse lines."
    solver = client.put(
        "/api/v2/solvers/worker/capabilities",
        json={
            "host_capability_overrides": {"add": [], "remove": ["save_note"]},
            "kali": None,
        },
    )
    assert solver.status_code == 200
    assert (
        "save_note"
        not in app.state.container.configuration.runtime.roles["worker"].tools
    )


def test_kali_profile_uses_the_released_image_and_never_reverts_to_kali_rolling(
    tmp_path: Path,
) -> None:
    reset_containers()
    run_root = tmp_path / "runs"
    app.state.container = get_container(run_root)
    client = TestClient(app)

    profile = client.get("/api/v2/kali/profiles").json()["items"][0]
    assert profile["image"] == DEFAULT_KALI_IMAGE
    assert profile["image_digest"] == DEFAULT_KALI_IMAGE_DIGEST
    assert profile["enabled"] is True

    changed = client.put(
        "/api/v2/kali/profiles/tga2-kali",
        json={
            "enabled": True,
            "image": "ghcr.io/example/tga-kali:test",
            "expected_digest": None,
        },
    )
    assert changed.status_code == 200
    assert changed.json()["image"] == "ghcr.io/example/tga-kali:test"

    assigned = client.put(
        "/api/v2/solvers/worker/capabilities",
        json={
            "host_capability_overrides": {"add": [], "remove": []},
            "kali": {"profile_id": "tga2-kali", "capabilities": ["kali.exec"]},
        },
    )
    assert assigned.status_code == 200
    assert assigned.json()["kali"]["image_name"] == "ghcr.io/example/tga-kali"
    assert app.state.container.configuration.runtime.sandbox_image == (
        "ghcr.io/example/tga-kali:test"
    )

    reset_containers()
    assert get_container(run_root).configuration.runtime.sandbox_image == (
        "ghcr.io/example/tga-kali:test"
    )


def test_kali_health_verifies_the_configured_local_image_digest(
    tmp_path: Path, monkeypatch
) -> None:
    import apps.api.routes.catalog as catalog_routes

    reset_containers()
    app.state.container = get_container(tmp_path / "runs")
    client = TestClient(app)
    monkeypatch.setattr(catalog_routes.shutil, "which", lambda _name: "/usr/bin/docker")
    monkeypatch.setattr(
        catalog_routes.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                [
                    {
                        "Id": DEFAULT_KALI_IMAGE_DIGEST,
                        "RepoDigests": [],
                    }
                ]
            ),
            stderr="",
        ),
    )

    health = client.post("/api/v2/solvers/worker/kali-health/check")
    assert health.status_code == 200
    assert health.json()["status"] == "healthy"
    assert health.json()["image"] == DEFAULT_KALI_IMAGE
    assert health.json()["image_store"]["expected_digest"] == (
        DEFAULT_KALI_IMAGE_DIGEST
    )
    assert health.json()["image_store"]["actual_digest"] == (DEFAULT_KALI_IMAGE_DIGEST)


def test_mcp_config_translation() -> None:
    config = MCPConfig(
        servers={
            "demo": MCPServer(transport="stdio", command="python", args=["server.py"])
        }
    )
    assert config.adapter_connections() == {
        "demo": {"transport": "stdio", "command": "python", "args": ["server.py"]}
    }


def test_skill_zip_installs_one_directory_package_and_rejects_unsafe_paths(
    tmp_path: Path,
) -> None:
    reset_containers()
    app.state.container = get_container(tmp_path / "runs")
    client = TestClient(app)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "ctf-crypto/SKILL.md",
            "---\nname: ctf-crypto\ndescription: Crypto methods\n"
            "tags: [ctf, crypto]\nversion: 2\n---\n\n# Crypto\nRoute by topic.",
        )
        archive.writestr("ctf-crypto/rsa.md", "# RSA\nCheck key material.")
    imported = client.post(
        "/api/v2/settings/skills/import",
        content=buffer.getvalue(),
        headers={"content-type": "application/zip"},
    )
    assert imported.status_code == 201
    assert imported.json()["skill"]["file_count"] == 2
    assert (
        tmp_path / "runs" / ".config" / "skills" / "ctf-crypto" / "rsa.md"
    ).is_file()

    unsafe = io.BytesIO()
    with zipfile.ZipFile(unsafe, "w") as archive:
        archive.writestr("../SKILL.md", "# unsafe")
    rejected = client.post("/api/v2/settings/skills/import", content=unsafe.getvalue())
    assert rejected.status_code == 422


def test_provider_registry_verifies_selected_provider_and_persists(
    tmp_path: Path, monkeypatch
) -> None:
    import apps.api.routes.settings as settings_routes
    import tga2.agent.service as service_module

    monkeypatch.setattr(
        settings_routes, "build_chat_model", lambda _settings: _VerificationModel()
    )
    monkeypatch.setattr(
        service_module, "build_chat_model", lambda _settings: _VerificationModel()
    )
    run_root = tmp_path / "runs"
    reset_containers()
    app.state.container = get_container(run_root)
    client = TestClient(app)

    created = client.post(
        "/api/v2/settings/llm/providers",
        json={
            "preset_id": "deepseek",
            "name": "My DeepSeek Gateway",
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-chat",
            "api_key": "deepseek-secret",
        },
    )
    assert created.status_code == 201
    provider = created.json()["provider"]
    assert provider["name"] == "My DeepSeek Gateway"
    assert provider["id"].startswith("provider_")
    model_id = provider["models"][0]["id"]

    verified = client.post(
        f"/api/v2/settings/llm/providers/{provider['id']}/models/{model_id}/verify"
    )
    assert verified.status_code == 200
    assert verified.json()["provider_name"] == "My DeepSeek Gateway"
    assert verified.json()["model"] == "deepseek-chat"
    assert isinstance(app.state.container.runtime.agents, RoutedAgentSuite)
    assert all(
        isinstance(suite, OfflineAgentSuite)
        for suite in app.state.container.runtime.agents.roles.values()
    )
    persisted = json.loads((run_root / ".config" / "models.json").read_text())
    assert persisted["providers"][0]["api_keys"][0]["api_key"] == "deepseek-secret"

    reset_containers()
    reloaded = get_container(run_root)
    stored = reloaded.configuration.model_registry.provider(provider["id"])
    assert stored.name == "My DeepSeek Gateway"
    assert stored.model(model_id).verification_status == "verified"


def test_agent_model_assignments_are_validated_and_routed(
    tmp_path: Path, monkeypatch
) -> None:
    import apps.api.routes.settings as settings_routes
    import tga2.agent.service as service_module

    monkeypatch.setattr(
        settings_routes, "build_chat_model", lambda _settings: _VerificationModel()
    )
    monkeypatch.setattr(
        service_module, "build_chat_model", lambda _settings: _VerificationModel()
    )
    reset_containers()
    app.state.container = get_container(tmp_path / "runs")
    client = TestClient(app)
    provider = client.post(
        "/api/v2/settings/llm/providers",
        json={
            "preset_id": "custom",
            "name": "Competition Gateway",
            "base_url": "https://models.example/v1",
            "model": "competition-model",
            "api_key": "competition-secret",
        },
    ).json()["provider"]
    model_id = provider["models"][0]["id"]
    assert (
        client.post(
            f"/api/v2/settings/llm/providers/{provider['id']}/models/{model_id}/verify"
        ).status_code
        == 200
    )
    for role in ("supervisor", "reviewer"):
        changed = client.put(
            f"/api/v2/solvers/{role}/capabilities",
            json={
                "host_capability_overrides": {"add": [], "remove": []},
                "kali": None,
                "model": {"provider_id": provider["id"], "model_id": model_id},
            },
        )
        assert changed.status_code == 200
    request = CreateTaskRequest(
        name="routed",
        objective="route models",
        mode="vulnerability_research",
    )
    made = app.state.container.runtime.create_task(request)
    with app.state.container.runtime._runtime(made["task_id"]) as (_store, graph):
        routed = graph.dependencies.agents
        assert isinstance(routed, RoutedAgentSuite)
        assert isinstance(routed.roles["supervisor"], LangChainAgentSuite)
        assert isinstance(routed.roles["worker"], OfflineAgentSuite)
    runtime_payload = json.loads(
        (app.state.container.run_root / ".config" / "runtime.json").read_text()
    )
    assert runtime_payload["roles"]["supervisor"]["model"] == {
        "provider_id": provider["id"],
        "model_id": model_id,
    }


def test_cancelled_task_is_not_restarted_or_completed(tmp_path: Path) -> None:
    service = TaskRuntimeService(run_root=tmp_path / "runs")
    made = service.create_task(
        CreateTaskRequest(
            name="cancelled",
            objective="must not execute",
            mode="vulnerability_research",
        )
    )
    service.cancel_task(made["task_id"])
    result = service.run_task(made["task_id"])
    assert result["status"] == "cancelled"
    assert service.snapshot(made["task_id"])["session"]["status"] == "cancelled"


def test_tga2_has_no_legacy_or_fastapi_imports() -> None:
    root = Path(__file__).parents[1] / "tga2"
    violations = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [item.name for item in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                if name in {"tga", "fastapi"} or name.startswith(("tga.", "fastapi.")):
                    violations.append(f"{path}: {name}")
    assert violations == []
