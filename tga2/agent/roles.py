"""Role agents built with LangChain create_agent; no custom model/tool loop."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from typing import Any, Protocol

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware
from langchain_core.exceptions import OutputParserException
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.tools import BaseTool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command, interrupt
from pydantic import BaseModel

from tga2.agent.schemas import (
    ClaimDraft,
    CriterionAssessmentDraft,
    CriterionReviewDraft,
    FindingDraft,
    PlanDraft,
    PlanIntentDraft,
    ReportDraft,
    ReviewDraft,
    ReviewPacket,
    SituationPacket,
    SupervisorDecision,
    WorkerDraft,
    structured_output_prompt,
)
from tga2.core.models import Task


class AgentSuite(Protocol):
    def plan(self, task: Task) -> PlanDraft: ...
    def decide(self, task: Task, packet: SituationPacket) -> SupervisorDecision: ...
    def work(
        self,
        task: Task,
        intent: dict[str, Any],
        tools: Sequence[BaseTool],
        feedback: str,
        middleware: Sequence[Any] = (),
    ) -> WorkerDraft: ...
    def review(self, task: Task, packet: ReviewPacket) -> ReviewDraft: ...
    def report(self, task: Task, snapshot: dict[str, Any]) -> ReportDraft: ...
    def take_model_calls(self, role: str) -> int: ...


class WorkerFinalizationError(RuntimeError):
    """The clean, tool-free Worker finalizer exhausted its reserved calls."""

    def __init__(self, message: str, *, model_calls: int) -> None:
        super().__init__(message)
        self.model_calls = model_calls


class LangChainAgentSuite:
    """Standard LangChain agents embedded as nodes in the outer LangGraph."""

    def __init__(
        self,
        model: BaseChatModel,
        skill_catalog: Callable[[Task], str] | None = None,
        prompts: dict[str, Any] | None = None,
        *,
        model_call_limit: int = 8,
        structured_parse_retries: int = 1,
        force_prompt_worker_output: bool = False,
    ) -> None:
        self.model = model
        self.skill_catalog = skill_catalog or (lambda _task: "")
        self.prompts = prompts or {}
        self.structured_parse_retries = structured_parse_retries
        self.force_prompt_worker_output = force_prompt_worker_output
        self.model_call_limit = model_call_limit
        # Keep two calls outside create_agent. The tool loop cannot reliably
        # turn into schema JSON on reasoning endpoints: DeepSeek may emit DSML
        # asking for another tool even after tools are hidden. A fresh finalizer
        # gets one normal attempt and one validation-correction attempt.
        self.worker_investigation_limit = max(1, model_call_limit - 2)
        self._last_model_calls = 0
        self._model_middleware = [
            ModelCallLimitMiddleware(
                run_limit=self.worker_investigation_limit,
                exit_behavior="end",
            ),
        ]
        # A Worker is a resumable LangGraph sub-agent.  The outer task graph
        # persists the business workflow; this cache preserves the nested
        # LangChain tool loop while an operator reviews a tool call.
        self._worker_runs: dict[str, dict[str, Any]] = {}

    def plan(self, task: Task) -> PlanDraft:
        return self._direct_structured(
            task,
            "supervisor",
            PlanDraft,
            _task_prompt(task),
            (
                "Return the initial task plan as JSON with a summary and one or "
                "more bounded intents. Every Intent must define concrete, "
                "independently verifiable success_criteria and the "
                "expected_evidence needed to prove them. Do not use vague "
                "criteria such as 'investigate thoroughly' or 'complete the task'. "
                "Express dependencies as zero-based indexes of earlier Intents in "
                "the same array. Runtime will own dependency resolution and scheduling."
            ),
        )

    def decide(self, task: Task, packet: SituationPacket) -> SupervisorDecision:
        return self._direct_structured(
            task,
            "supervisor",
            SupervisorDecision,
            packet.model_dump_json(),
            "Return exactly one checkpoint decision as JSON. Stay within the supplied authorization and budget.",
        )

    def work(
        self,
        task: Task,
        intent: dict[str, Any],
        tools: Sequence[BaseTool],
        feedback: str,
        middleware: Sequence[Any] = (),
    ) -> WorkerDraft:
        worker_prompt = self._prompt("worker", task)
        response_format: type[WorkerDraft] | None = WorkerDraft
        if self.force_prompt_worker_output:
            # Keep normal tool auto-selection for reasoning endpoints that
            # reject LangChain ToolStrategy's forced tool_choice.  LangChain
            # still owns the full model/tool loop; only the terminal response
            # is parsed from JSON instead of represented as a synthetic tool.
            worker_prompt = structured_output_prompt(
                worker_prompt,
                (
                    "Use the available tools as needed. When the investigation "
                    "is complete, return the final WorkerDraft as JSON."
                ),
                WorkerDraft,
            )
            response_format = None
        worker_prompt = (
            f"{worker_prompt}\n\nCall at most one tool in each assistant message. "
            "Wait for its result before selecting another tool. Tool output is "
            "already persisted by Runtime as an Artifact; do not repeat a command "
            "just to save the same output to a file. Stop investigating early once "
            "the current Intent has enough evidence. Runtime performs finalization "
            "after this bounded investigation phase. Treat the Intent's numbered "
            "success_criteria as an acceptance checklist: after each observation, "
            "identify the first unmet criterion and call a tool only when it can "
            "close that specific evidence gap. Once all criteria can be supported "
            "by Artifact IDs, stop immediately. On a retry, retry_context is an "
            "authoritative Runtime ledger: preserve its confirmed evidence, never "
            "repeat an executed command, and investigate only criteria the latest "
            "review did not verify. Use read_artifact to inspect an existing output. "
            "For a new Intent, handoff_context is the authoritative Runtime-curated "
            "record of prior Intent progress. Reuse its Artifact IDs and confirmed "
            "evidence. Use list_artifacts when more task-owned outputs must be discovered. "
            "Every Artifact ID used by a met criterion_assessment must also appear "
            "in at least one ClaimDraft so Reviewer receives that evidence. "
            "If a stop_condition is reached, "
            "stop and report incomplete, blocked, or needs_user instead of "
            "repeating commands."
        )
        content = json.dumps(
            {
                "task": task.model_dump(mode="json"),
                "intent": intent,
                "review_feedback": feedback or None,
            },
            ensure_ascii=False,
        )
        run_key = f"{task.id}:{intent.get('id')}:{intent.get('attempt', 1)}"
        entry = self._worker_runs.get(run_key)
        if entry is None:
            entry = {
                "checkpointer": InMemorySaver(),
                "config": {"configurable": {"thread_id": run_key}},
                "interrupt_history": [],
                "result": None,
            }
            self._worker_runs[run_key] = entry
        # Recompile around the same checkpoint on every outer graph request.
        # Middleware owns request-scoped TaskStore connections, so caching the
        # compiled agent would retain a closed database after the first pause.
        agent = create_agent(
            self.model,
            tools=tools,
            system_prompt=worker_prompt,
            response_format=response_format,
            middleware=[*self._middleware(task), *middleware],
            checkpointer=entry["checkpointer"],
            name="tga2_worker",
        )
        config = entry["config"]

        # LangGraph resumes a node from its beginning. Replay earlier outer
        # interrupt calls so a later approval keeps the same interrupt index,
        # then forward the current decision into the nested Worker graph.
        for prior in entry["interrupt_history"]:
            interrupt(prior)
        result = entry.get("result")
        if result is None:
            inner_state = agent.get_state(config)
            if inner_state.interrupts:
                request = self._worker_interrupt(inner_state.interrupts)
                decision = interrupt(request)
                entry["interrupt_history"].append(request)
                result = agent.invoke(Command(resume=decision), config=config)
            else:
                result = agent.invoke(
                    {"messages": [{"role": "user", "content": content}]},
                    config=config,
                )
            while result.get("__interrupt__"):
                request = self._worker_interrupt(result["__interrupt__"])
                decision = interrupt(request)
                entry["interrupt_history"].append(request)
                result = agent.invoke(Command(resume=decision), config=config)
            entry["result"] = result
        investigation_calls = self._result_model_calls(result)
        self._last_model_calls = investigation_calls

        # A Worker may finish voluntarily before the investigation limit. Keep
        # that valid answer and avoid spending a separate finalizer call.
        try:
            if self.force_prompt_worker_output:
                return self._parse_worker_message(result)
            return self._structured(result, WorkerDraft)
        except (TypeError, ValueError):
            pass

        return self._finalize_worker(
            task,
            intent,
            feedback,
            result,
            remaining_calls=max(0, self.model_call_limit - investigation_calls),
        )

    @staticmethod
    def _worker_interrupt(values: Sequence[Any]) -> dict[str, Any]:
        first = values[0] if values else None
        value = getattr(first, "value", first)
        request = dict(value) if isinstance(value, dict) else {}
        return {
            "kind": "tool_approval",
            "action_requests": list(request.get("action_requests") or []),
            "review_configs": list(request.get("review_configs") or []),
        }

    def review(self, task: Task, packet: ReviewPacket) -> ReviewDraft:
        return self._direct_structured(
            task,
            "reviewer",
            ReviewDraft,
            packet.model_dump_json(),
            (
                "Review only the supplied evidence packet. Return one "
                "criterion_result for every numbered intent_success_criterion. "
                "Mark a criterion verified only when the supplied, locator-valid "
                "claims semantically prove it. A pass verdict requires every "
                "Intent criterion to be verified. Return the verdict as JSON."
            ),
        )

    def report(self, task: Task, snapshot: dict[str, Any]) -> ReportDraft:
        return self._direct_structured(
            task,
            "reporter",
            ReportDraft,
            json.dumps(snapshot, ensure_ascii=False),
            "Return the final report draft as JSON using only confirmed findings and persisted evidence.",
        )

    def _direct_structured(
        self,
        task: Task,
        role: str,
        expected: type[BaseModel],
        payload: str,
        instruction: str,
    ):
        structured = self.model.with_structured_output(
            expected, method="json_mode", include_raw=True
        )
        messages = [
            SystemMessage(
                content=structured_output_prompt(
                    self._prompt(role, task), instruction, expected
                )
            ),
            HumanMessage(content=payload),
        ]
        last_error: Exception | None = None
        self._last_model_calls = 0
        for attempt in range(self.structured_parse_retries + 1):
            result = structured.invoke(messages)
            self._last_model_calls += 1
            parsed = result.get("parsed") if isinstance(result, dict) else result
            if isinstance(parsed, expected):
                return parsed
            error = result.get("parsing_error") if isinstance(result, dict) else None
            last_error = (
                error
                if isinstance(error, Exception)
                else ValueError(f"{role} returned no valid {expected.__name__}")
            )
            if attempt < self.structured_parse_retries:
                messages.append(
                    HumanMessage(
                        content=f"The previous JSON failed validation: {last_error}. Return corrected JSON only."
                    )
                )
        raise last_error

    def take_model_calls(self, role: str) -> int:
        value = self._last_model_calls
        self._last_model_calls = 0
        return value

    @staticmethod
    def _structured(result: dict[str, Any], expected: type[BaseModel]):
        value = result.get("structured_response")
        if isinstance(value, expected):
            return value
        return expected.model_validate(value)

    @staticmethod
    def _parse_worker_message(result: dict[str, Any]) -> WorkerDraft:
        message = next(
            (
                item
                for item in reversed(result.get("messages", []))
                if isinstance(item, AIMessage) and not item.tool_calls
            ),
            None,
        )
        if message is None:
            raise ValueError("worker returned no final model response")
        return PydanticOutputParser(pydantic_object=WorkerDraft).parse(message.text)

    @staticmethod
    def _result_model_calls(result: dict[str, Any]) -> int:
        tracked = result.get("run_model_call_count")
        if tracked is not None:
            return int(tracked)
        return sum(
            isinstance(message, AIMessage)
            and not message.text.startswith("Model call limits exceeded:")
            for message in result.get("messages", [])
        )

    def _finalize_worker(
        self,
        task: Task,
        intent: dict[str, Any],
        feedback: str,
        result: dict[str, Any],
        *,
        remaining_calls: int,
    ) -> WorkerDraft:
        attempts = min(2, remaining_calls)
        if attempts < 1:
            raise WorkerFinalizationError(
                "Worker investigation consumed the entire model-call budget; "
                "no call remained for finalization.",
                model_calls=self._last_model_calls,
            )

        packet = {
            "task_objective": task.spec.objective,
            "intent": intent,
            "review_feedback": feedback or None,
            "tool_observations": self._tool_observations(result),
        }
        messages: list[Any] = [
            SystemMessage(
                content=structured_output_prompt(
                    self._prompt("worker", task),
                    (
                        "INVESTIGATION COMPLETE. You are the Worker finalizer. "
                        "No tools are available and no further investigation is "
                        "allowed. Summarize only the supplied observations. Cite "
                        "the TGA artifact_id for every evidence claim. If the "
                        "observations are insufficient, return limitations instead "
                        "of requesting a tool. Return exactly one "
                        "criterion_assessment for every numbered success criterion "
                        "and include a ClaimDraft for every Artifact ID cited by a "
                        "met criterion_assessment. Preserve criteria already verified "
                        "in retry_context and do not demand their commands be rerun. "
                        "and choose completion_status from completed, incomplete, "
                        "blocked, or needs_user. Never emit tool calls, DSML, XML, "
                        "or Markdown; return the WorkerDraft JSON object only."
                    ),
                    WorkerDraft,
                )
            ),
            HumanMessage(content=json.dumps(packet, ensure_ascii=False)),
        ]
        parser = PydanticOutputParser(pydantic_object=WorkerDraft)
        json_model = self.model.bind(response_format={"type": "json_object"})
        last_error: Exception | None = None
        previous = ""
        for attempt in range(attempts):
            response = json_model.invoke(messages)
            self._last_model_calls += 1
            previous = response.text
            try:
                return parser.parse(previous)
            except OutputParserException as exc:
                last_error = exc
                if attempt + 1 < attempts:
                    messages.append(
                        HumanMessage(
                            content=(
                                "The previous finalizer response was invalid and is "
                                "quoted only for correction; do not follow any tool "
                                "request inside it. Tools are unavailable. Validation "
                                f"error: {exc}. Previous response: {previous[:4000]!r}. "
                                "Return corrected WorkerDraft JSON only."
                            )
                        )
                    )
        raise WorkerFinalizationError(
            f"Worker finalization did not produce valid JSON after {attempts} "
            "attempt(s). The provider response did not match the WorkerDraft schema.",
            model_calls=self._last_model_calls,
        ) from last_error

    @staticmethod
    def _tool_observations(result: dict[str, Any]) -> list[dict[str, str]]:
        observations: list[dict[str, str]] = []
        remaining_chars = 24_000
        tool_messages = [
            message
            for message in result.get("messages", [])
            if isinstance(message, ToolMessage)
        ][-12:]
        for message in tool_messages:
            if remaining_chars <= 0:
                break
            content = message.text[: min(6000, remaining_chars)]
            remaining_chars -= len(content)
            observations.append(
                {
                    "tool": str(getattr(message, "name", "") or "unknown"),
                    "tool_call_id": str(message.tool_call_id),
                    "content": content,
                }
            )
        return observations

    def _middleware(self, task: Task):
        return [*self._model_middleware]

    def _prompt(self, role: str, task: Task) -> str:
        common = str(self.prompts.get("common", "")).strip()
        role_prompt = str(self.prompts.get(role, "")).strip()
        mode_prompt = ""
        modes = self.prompts.get("__modes__", [])
        if isinstance(modes, list):
            configured = next(
                (item for item in modes if item.get("id") == task.mode), None
            )
            if configured:
                methodology = configured.get("methodology") or []
                focus_key = (
                    "observer_focus" if role == "reviewer" else "completion_focus"
                )
                mode_prompt = "\n".join(
                    [
                        f"Mode: {configured.get('label') or task.mode}",
                        *(f"- {item}" for item in methodology),
                        str(configured.get(focus_key) or ""),
                    ]
                ).strip()
        catalog = self.skill_catalog(task).strip() if role == "worker" else ""
        return "\n\n".join(
            item for item in (common, role_prompt, mode_prompt, catalog) if item
        )


class RoutedAgentSuite:
    """Route each role to the model selected in the persisted task spec."""

    def __init__(self, roles: dict[str, AgentSuite]) -> None:
        self.roles = roles

    def plan(self, task: Task) -> PlanDraft:
        return self.roles["supervisor"].plan(task)

    def decide(self, task: Task, packet: SituationPacket) -> SupervisorDecision:
        return self.roles["supervisor"].decide(task, packet)

    def work(
        self,
        task: Task,
        intent: dict[str, Any],
        tools: Sequence[BaseTool],
        feedback: str,
        middleware: Sequence[Any] = (),
    ) -> WorkerDraft:
        return self.roles["worker"].work(task, intent, tools, feedback, middleware)

    def review(self, task: Task, packet: ReviewPacket) -> ReviewDraft:
        return self.roles["reviewer"].review(task, packet)

    def report(self, task: Task, snapshot: dict[str, Any]) -> ReportDraft:
        return self.roles["reporter"].report(task, snapshot)

    def take_model_calls(self, role: str) -> int:
        return self.roles[role].take_model_calls(role)


class OfflineAgentSuite:
    """Deterministic decisions for demos/tests; the LangGraph runtime is unchanged."""

    def plan(self, task: Task) -> PlanDraft:
        return PlanDraft(
            summary="Execute one bounded evidence-gathering intent and review its output.",
            intents=[
                PlanIntentDraft(
                    title="Analyze supplied task resources",
                    objective=task.spec.objective,
                    success_criteria=[
                        "The available task resources are inspected and the result is summarized."
                    ],
                    expected_evidence=[
                        "An Artifact-backed observation for each inspectable task resource."
                    ],
                )
            ],
        )

    def decide(self, task: Task, packet: SituationPacket) -> SupervisorDecision:
        review = packet.review_result or {}
        verdict = review.get("verdict")
        attempts = int(packet.current_intent.get("attempt", 1))
        if verdict == "pass":
            pending = [
                item
                for item in packet.plan.get("intents", [])
                if item.get("status") == "pending"
            ]
            return SupervisorDecision(
                action="next_intent" if pending else "finish",
                reason="Evidence review passed.",
            )
        if verdict == "needs_user":
            return SupervisorDecision(
                action="ask_user",
                reason="Reviewer requires operator input.",
                user_question=review.get("feedback")
                or "Please provide the missing information.",
            )
        if attempts < int(packet.remaining_budget.get("intent_attempt_limit", 3)):
            return SupervisorDecision(
                action="retry",
                reason="The current intent needs another bounded attempt.",
                feedback=review.get("feedback") or "Collect stronger evidence.",
            )
        return SupervisorDecision(
            action="fail",
            reason="The current intent exhausted its attempt budget.",
        )

    def work(
        self,
        task: Task,
        intent: dict[str, Any],
        tools: Sequence[BaseTool],
        feedback: str,
        middleware: Sequence[Any] = (),
    ) -> WorkerDraft:
        by_name = {tool.name: tool for tool in tools}
        names = by_name["list_inputs"].invoke({}) if "list_inputs" in by_name else []
        if isinstance(names, str):
            try:
                names = json.loads(names)
            except json.JSONDecodeError:
                names = []
        claims: list[ClaimDraft] = []
        notes: list[str] = []
        for name in list(names)[:3]:
            result = by_name["read_input"].invoke({"path": str(name)})
            payload = json.loads(result) if isinstance(result, str) else result
            artifact_id = str(payload["artifact_id"])
            excerpt = str(payload.get("content") or "")[:500]
            notes.append(f"Inspected {name}: {excerpt}")
            claims.append(
                ClaimDraft(
                    artifact_id=artifact_id,
                    statement=f"The supplied resource {name} was inspected and contains the quoted content.",
                    quote=excerpt,
                )
            )
        if not notes:
            notes.append(
                "No input files were supplied; only the task objective could be reviewed."
            )
        return WorkerDraft(
            summary="\n".join(notes),
            completion_status="completed",
            criterion_assessments=[
                CriterionAssessmentDraft(
                    criterion_index=index,
                    status="met",
                    artifact_ids=[item.artifact_id for item in claims],
                    note=(
                        "Inspectable resources produced Artifact-backed observations."
                        if claims
                        else "No inspectable task resource was supplied."
                    ),
                )
                for index, _criterion in enumerate(intent.get("success_criteria") or [])
            ],
            claims=claims,
            limitations=[]
            if claims
            else ["No inspectable task resource was supplied."],
        )

    def review(self, task: Task, packet: ReviewPacket) -> ReviewDraft:
        ids = [
            str(item.claim.get("id"))
            for item in packet.evidence
            if item.locator_valid and item.claim.get("status") == "candidate"
        ]
        findings = []
        if ids:
            findings.append(
                FindingDraft(
                    title="Supplied resources inspected",
                    description="The worker produced traceable observations from the supplied resources.",
                    severity="info",
                    evidence_claim_ids=ids,
                )
            )
        return ReviewDraft(
            verdict="pass",
            feedback="Evidence references are structurally valid."
            if ids
            else "Completed with no evidence-backed finding.",
            confirmed_claim_ids=ids,
            findings=findings,
            criterion_results=[
                CriterionReviewDraft(
                    criterion_index=index,
                    status="verified",
                    evidence_claim_ids=ids,
                    reason=(
                        "The persisted evidence supports this acceptance criterion."
                        if ids
                        else "The deterministic offline run completed without inspectable evidence."
                    ),
                )
                for index, _criterion in enumerate(packet.intent_success_criteria)
            ],
        )

    def report(self, task: Task, snapshot: dict[str, Any]) -> ReportDraft:
        count = len(snapshot.get("findings", []))
        return ReportDraft(
            executive_summary=f"Task completed with {count} persisted finding(s).",
            methodology=[
                "Supervisor planning",
                "Governed resource inspection",
                "Independent evidence review",
            ],
            limitations=[] if count else ["No evidence-backed finding was produced."],
        )

    def take_model_calls(self, role: str) -> int:
        return 0


def _task_prompt(task: Task) -> str:
    return json.dumps(task.model_dump(mode="json"), ensure_ascii=False)


__all__ = [
    "AgentSuite",
    "ClaimDraft",
    "FindingDraft",
    "LangChainAgentSuite",
    "OfflineAgentSuite",
    "PlanDraft",
    "PlanIntentDraft",
    "ReportDraft",
    "ReviewDraft",
    "ReviewPacket",
    "RoutedAgentSuite",
    "SituationPacket",
    "SupervisorDecision",
    "WorkerDraft",
]
