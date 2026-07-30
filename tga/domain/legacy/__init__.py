"""Conservative, pure conversions from schema-v5 domain contracts."""

from tga.domain.legacy.converters import (
    artifact_record_to_artifact,
    legacy_finding_to_evidence,
    memory_entry_to_knowledge,
    memory_entry_to_task_hint,
    strategy_card_to_plans,
)

__all__ = [
    "artifact_record_to_artifact", "legacy_finding_to_evidence",
    "memory_entry_to_knowledge", "memory_entry_to_task_hint",
    "strategy_card_to_plans",
]

