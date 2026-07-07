"""Week 1 orchestration loop skeleton."""

from __future__ import annotations

from tga.contracts import TGATask
from tga.evidence.store import EvidenceStore
from tga.orchestrator.planner import plan_initial_intents
from tga.orchestrator.scheduler import Scheduler
from tga.workers.base import Worker


def run_task(*, task: TGATask, store: EvidenceStore, worker: Worker, run_root: str) -> None:
    store.create_task(task)
    scheduler = Scheduler(store=store, worker=worker, run_root=run_root)
    for intent in plan_initial_intents(task):
        scheduler.run_intent(task=task, intent=intent)

