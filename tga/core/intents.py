"""Intent helpers."""

from __future__ import annotations

from uuid import uuid4

from tga.contracts import Intent, TGATask


def new_intent_id() -> str:
    return f"intent_{uuid4().hex[:10]}"


def make_intent(
    *,
    task: TGATask,
    kind: str,
    goal: str,
    required_tools: list[str] | None = None,
    risk: str = "passive",
) -> Intent:
    return Intent(
        id=new_intent_id(),
        task_id=task.id,
        kind=kind,  # type: ignore[arg-type]
        target=task.target,
        goal=goal,
        required_tools=required_tools or [],
        risk=risk,  # type: ignore[arg-type]
    )

