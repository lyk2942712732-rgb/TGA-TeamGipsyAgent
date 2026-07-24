"""Finding evidence gate."""

from __future__ import annotations

from tga.contracts import Finding, TGATask
from tga.network_policy import authorize_url


def finding_ok(
    finding: Finding,
    *,
    task: TGATask,
    artifact_text: str | None,
) -> bool:
    try:
        authorize_url(finding.target, task.execution_policy.network, resolve_dns=False)
    except (PermissionError, ValueError):
        return False
    if not finding.evidence_artifact_id:
        return False
    if not artifact_text:
        return False
    if finding.evidence_excerpt and finding.evidence_excerpt not in artifact_text:
        return False
    return True

