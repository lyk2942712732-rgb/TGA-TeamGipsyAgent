"""Worker wrapper for pre-planned LLM HTTP actions."""

from __future__ import annotations

from tga.agent.http_action_planner import HTTPAction
from tga.contracts import Intent, TGATask, WorkerResult
from tga.ctf.llm_http_agent import execute_http_actions
from tga.evidence.artifacts import ArtifactStore


class LLMHTTPWorker:
    def __init__(
        self,
        *,
        artifact_store: ArtifactStore,
        actions: list[HTTPAction],
        plan_meta: dict,
        timeout_s: int = 12,
    ):
        self.artifact_store = artifact_store
        self.actions = actions
        self.plan_meta = plan_meta
        self.timeout_s = timeout_s

    def run(self, *, task: TGATask, intent: Intent, workspace: str) -> WorkerResult:
        return execute_http_actions(
            task=task,
            intent=intent,
            artifact_store=self.artifact_store,
            actions=self.actions,
            plan_meta=self.plan_meta,
            timeout_s=self.timeout_s,
        )
