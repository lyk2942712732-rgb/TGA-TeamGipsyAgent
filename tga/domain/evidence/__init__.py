"""Artifact, evidence-claim and finding domain."""

from tga.domain.evidence.artifacts import Artifact
from tga.domain.evidence.claims import EvidenceClaim, EvidenceClaimStatus
from tga.domain.evidence.findings import Finding, FindingStatus, Severity
from tga.domain.evidence.locators import EvidenceLocator, LocatorKind
from tga.domain.evidence.indexes import ArtifactIndex, ArtifactSegment, ExtractionStatus
from tga.domain.evidence.records import ArtifactKind, ArtifactRecord, CandidateFindingRecord

__all__ = [
    "Artifact", "ArtifactIndex", "ArtifactKind", "ArtifactRecord", "ArtifactSegment",
    "CandidateFindingRecord", "EvidenceClaim", "EvidenceClaimStatus", "EvidenceLocator",
    "ExtractionStatus", "Finding", "FindingStatus", "LocatorKind", "Severity",
]
