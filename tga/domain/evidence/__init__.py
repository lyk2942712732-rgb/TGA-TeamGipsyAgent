"""Artifact, evidence-claim and finding domain."""

from tga.domain.evidence.artifacts import Artifact
from tga.domain.evidence.claims import EvidenceClaim, EvidenceClaimStatus
from tga.domain.evidence.findings import Finding, FindingStatus, Severity
from tga.domain.evidence.legacy_models import (
    ArtifactRecord as LegacyArtifactRecord,
    Finding as LegacyFinding,
)
from tga.domain.evidence.locators import EvidenceLocator, LocatorKind

__all__ = [
    "Artifact", "EvidenceClaim", "EvidenceClaimStatus", "EvidenceLocator",
    "Finding", "FindingStatus", "LegacyArtifactRecord", "LegacyFinding",
    "LocatorKind", "Severity",
]
