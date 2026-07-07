"""API schemas.

Keep these thin: API request/response shapes should map directly to
`tga.contracts` instead of defining a separate product model.
"""

from __future__ import annotations

from pydantic import BaseModel

from tga.contracts import TGATask


class CreateTaskRequest(BaseModel):
    task: TGATask


class CreateTaskResponse(BaseModel):
    task_id: str
    status: str = "accepted"


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str = "tga-api"

