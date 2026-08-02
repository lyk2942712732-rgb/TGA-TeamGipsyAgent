"""Schema-v6 evidence record names used by execution and persistence.

`Artifact` is the single Artifact model.  `ArtifactRecord` and `ArtifactKind`
are re-exported names for the execution-side call sites; they are the same
objects, not a second, weaker model.
"""

from __future__ import annotations

from tga.domain.evidence.artifacts import Artifact, ArtifactKind

ArtifactRecord = Artifact


__all__ = ["ArtifactKind", "ArtifactRecord"]
