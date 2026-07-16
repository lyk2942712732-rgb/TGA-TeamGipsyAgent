from __future__ import annotations

from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

from tga.contracts import ActionResult, ActionSpec, TGATask


class ActionExecutor(Protocol):
    """The only executor interface consumed by the session runtime."""

    def execute(self, *, task: TGATask, action: ActionSpec, workspace: Path) -> ActionResult: ...


class CapabilitySpec(BaseModel):
    name: str
    description: str
    kind: str
    risk: str = "passive"
    modes: list[str]
    parameter_schema: dict
    availability: str = "healthy"
    budget_key: str
