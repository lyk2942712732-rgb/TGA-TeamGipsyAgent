"""Week 1 orchestration loop skeleton."""

from __future__ import annotations

from tga.contracts import TGATask
from tga.evidence.store import EvidenceStore
from tga.orchestrator.planner import explain_plan, plan_initial_intents
from tga.orchestrator.scheduler import Scheduler
from tga.workers.base import Worker


def run_task(*, task: TGATask, store: EvidenceStore, worker: Worker, run_root: str) -> None:
    store.create_task(task)
    scheduler = Scheduler(store=store, worker=worker, run_root=run_root)
    intents = plan_initial_intents(task)
    plan = explain_plan(task, intents)
    store.add_event(
        task.id,
        "PLAN_CREATED",
        {
            "summary": "Initial autonomous execution plan created",
            "rationale": plan["strategy"],
            "plan": plan,
        },
    )
    for intent in intents:
        scheduler.run_intent(task=task, intent=intent)
