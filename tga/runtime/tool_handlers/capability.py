"""Capability execution remains behind the phase-5 legacy execution adapter.

Phase 5A replaces this marker with ToolGovernanceGateway; transport execution
is intentionally not rewritten in phase 5.
"""


class LegacyCapabilityHandlerAdapter:
    adapter_id = "phase5-legacy-capability-handler"


__all__ = ["LegacyCapabilityHandlerAdapter"]
