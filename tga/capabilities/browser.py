"""Browser execution is deliberately declared unavailable in the first runtime."""

from pydantic import BaseModel

from .base import CapabilitySpec


def browser_stub() -> CapabilitySpec:
    return CapabilitySpec(
        name="browser.navigate",
        description="Reserved browser capability; no browser sandbox is configured.",
        kind="browser",
        risk="active",
        modes=["ctf", "web_audit"],
        parameter_schema=BaseModel.model_json_schema(),
        availability="unavailable",
        budget_key="browser",
    )
