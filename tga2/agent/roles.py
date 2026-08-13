"""Role agents built with LangChain create_agent; no custom model/tool loop."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from typing import Any, Protocol

from langchain.agents import create_agent
from langchain.agents.middleware import (
    ModelCallLimitMiddleware,
)
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import BaseTool
from pydantic import BaseModel

from tga2.agent.schemas import (
    ClaimDraft,
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
    ) -> None:
        self.model = model
        self.skill_catalog = skill_catalog or (lambda _task: "")
        self.prompts = prompts or {}
        self.structured_parse_retries = structured_parse_retries
        self._last_model_calls = 0
        self._model_middleware = [
            ModelCallLimitMiddleware(run_limit=model_call_limit, exit_behavior="error"),
        ]

    def plan(self, task: Task) -> PlanDraft:
        return self._direct_structured(
            task,
            "supervisor",
            PlanDraft,
            _task_prompt(task),
            "Return the initial task plan as JSON with a summary and one or more bounded intents.",
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
        agent = create_agent(
            self.model,
            tools=tools,
            system_prompt=self._prompt("worker", task),
            response_format=WorkerDraft,
            middleware=[*self._middleware(task), *middleware],
            name="tga2_worker",
        )
        content = json.dumps(
            {
                "task": task.model_dump(mode="json"),
                "intent": intent,
                "review_feedback": feedback or None,
            },
            ensure_ascii=False,
        )
        result = agent.invoke({"messages": [{"role": "user", "content": content}]})
        self._last_model_calls = sum(
            isinstance(message, AIMessage) for message in result.get("messages", [])
        )
        return self._structured(result, WorkerDraft)

    def review(self, task: Task, packet: ReviewPacket) -> ReviewDraft:
        return self._direct_structured(
            task,
            "reviewer",
            ReviewDraft,
            packet.model_dump_json(),
            "Review only the supplied evidence packet and return the verdict as JSON.",
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
