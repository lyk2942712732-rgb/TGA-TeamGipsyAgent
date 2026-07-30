"""Non-authoritative metadata supplied by the model with a tool call."""

from pydantic import BaseModel, ConfigDict, Field

from tga.domain.governance.models import ActionEffect


class ModelToolIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    rationale: str = Field(default="", max_length=500)
    expected_outcome: str = Field(default="", max_length=500)
    retry_reason: str | None = Field(default=None, max_length=500)
    alternative_analysis: str | None = Field(default=None, max_length=500)
    proposed_effect: ActionEffect | None = None


__all__ = ["ModelToolIntent"]
