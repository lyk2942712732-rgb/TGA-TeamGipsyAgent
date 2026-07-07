"""Finding evidence gate."""

from __future__ import annotations

from tga.contracts import Finding, TGATask
from tga.core.scope import is_in_scope


def finding_ok(
    finding: Finding,
    *,
    task: TGATask,
    artifact_text: str | None,
) -> bool:
    if not is_in_scope(finding.target, task.scope):
        return False
    if not finding.evidence_artifact_id:
        return False
    if not artifact_text:
        return False
    if finding.evidence_excerpt and finding.evidence_excerpt not in artifact_text:
        return False
    return True

