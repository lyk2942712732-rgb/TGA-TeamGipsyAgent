"""The complete task topology is expressed directly as a LangGraph StateGraph."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, TypedDict

from langchain_core.tools import BaseTool
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from tga2.agent.roles import AgentSuite
from tga2.config import Configuration
from tga2.core.store import TaskStore
from tga2.core.workspace import TaskWorkspace


class TGAState(TypedDict, total=False):
    task_id: str
    objective: str
    intent_ids: list[str]
    intent_index: int
    current_intent_id: str
    attempt: int
    model_calls: int
    review_feedback: str
    worker_draft: dict[str, Any]
    claim_ids: list[str]
    review_result: dict[str, Any]
    supervisor_decision: dict[str, Any]
    plan_version: int
    user_question: str
    user_response: str
    completed_with_limitations: bool
    report_path: str
    status: str
    error: str


@dataclass(slots=True)
class RuntimeDeps:
    store: TaskStore
    workspace: TaskWorkspace
    agents: AgentSuite
    sandbox_image: str | None = None
    configuration: Configuration | None = None
    external_tools: Sequence[BaseTool] = ()


class TaskGraph:
    def __init__(self, dependencies: RuntimeDeps) -> None:
        from tga2.agent.nodes import GraphNodes

        self.dependencies = dependencies
        self._checkpoint_connection = sqlite3.connect(
            dependencies.workspace.checkpoint_path,
            check_same_thread=False,
        )
        checkpointer = SqliteSaver(self._checkpoint_connection)
        nodes = GraphNodes(dependencies)
        graph = StateGraph(TGAState)
        graph.add_node("load_task", nodes.load_task)
        graph.add_node("preflight", nodes.preflight)
        graph.add_node("initial_plan", nodes.initial_plan)
        graph.add_node("worker", nodes.worker)
        graph.add_node("reviewer", nodes.reviewer)
        graph.add_node("supervisor_checkpoint", nodes.supervisor_checkpoint)
        graph.add_node("advance", nodes.advance)
        graph.add_node("retry", nodes.retry)
        graph.add_node("revise_plan", nodes.revise_plan)
        graph.add_node("request_user_input", nodes.request_user_input)
        graph.add_node("wait_for_user", nodes.wait_for_user)
        graph.add_node("block_intent", nodes.block_intent)
        graph.add_node("reporter", nodes.reporter)
        graph.add_node("complete", nodes.complete)
        graph.add_edge(START, "load_task")
        graph.add_edge("load_task", "preflight")
        graph.add_edge("preflight", "initial_plan")
        graph.add_edge("initial_plan", "worker")
        graph.add_edge("worker", "reviewer")
        graph.add_edge("reviewer", "supervisor_checkpoint")
        graph.add_conditional_edges(
            "supervisor_checkpoint",
            self._route_supervisor,
            {
                "retry": "retry",
                "next_intent": "advance",
                "revise_plan": "revise_plan",
                "ask_user": "request_user_input",
                "report": "reporter",
                "fail": "block_intent",
            },
        )
        graph.add_edge("retry", "worker")
        graph.add_edge("advance", "worker")
        graph.add_edge("revise_plan", "advance")
        graph.add_edge("request_user_input", "wait_for_user")
        graph.add_edge("wait_for_user", "supervisor_checkpoint")
        graph.add_edge("block_intent", "reporter")
        graph.add_edge("reporter", "complete")
        graph.add_edge("complete", END)
        self.compiled = graph.compile(checkpointer=checkpointer, name="tga2_task")

    def invoke(self, task_id: str) -> dict[str, Any]:
        result = self.compiled.invoke(
            {"task_id": task_id},
            config=self._config(task_id),
            version="v2",
        )
        return {
            "state": result.value,
            "interrupts": [item.value for item in result.interrupts],
        }

    def resume(self, task_id: str, decision: bool | dict[str, Any]) -> dict[str, Any]:
        result = self.compiled.invoke(
            Command(resume=decision),
            config=self._config(task_id),
            version="v2",
        )
        return {
            "state": result.value,
            "interrupts": [item.value for item in result.interrupts],
        }

    def stream(self, task_id: str):
        return self.compiled.stream(
            {"task_id": task_id},
            config=self._config(task_id),
            stream_mode="updates",
        )

    def state(self, task_id: str):
        return self.compiled.get_state(self._config(task_id))

    def close(self) -> None:
        self._checkpoint_connection.close()

    @staticmethod
    def _config(task_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": task_id}}

    @staticmethod
    def _route_supervisor(state: TGAState) -> str:
        action = (state.get("supervisor_decision") or {}).get("action", "fail")
        if action == "finish":
            return "report"
        return str(action)


__all__ = ["RuntimeDeps", "TGAState", "TaskGraph"]
