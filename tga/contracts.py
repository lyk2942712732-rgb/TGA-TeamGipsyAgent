"""Backward-compatible exports for the pre-domain-module contract surface.

Canonical definitions now live under :mod:`tga.domain`.  Keep importing from
this module while callers migrate gradually; every name below is the same
Python object exported by its canonical module.
"""

from tga.domain.evidence.legacy_models import (
    AgentEvent,
    ArtifactIndex,
    ArtifactKind,
    ArtifactRecord,
    ArtifactSegment,
    DecisionPhase,
    DecisionTrace,
    ExtractionStatus,
    Finding,
    FindingStatus,
    Intent,
    IntentKind,
    IntentStatus,
    Severity,
    WorkerResult,
    WorkerStatus,
)
from tga.domain.governance.models import (
    ActionEffect,
    ActionKind,
    ActionResult,
    ActionSpec,
    ActionStatus,
    ExecutionPolicy,
    HighImpactExecutionPolicy,
    LocalComputeExecutionPolicy,
    NetworkExecutionPolicy,
    RiskLevel,
    TGAError,
)
from tga.domain.solver.legacy_models import (
    ChallengeContract,
    ChallengeStatus,
    ContextMetric,
    MemoryEntry,
    MemoryKind,
    SessionRecord,
    SessionStatus,
    SolverRecord,
    SolverRole,
    SolverStatus,
    StrategyCard,
    StrategySource,
    StrategyStatus,
    StrategyStep,
)
from tga.domain.task.models import (
    CtfModeConfig,
    CtfVerifier,
    IncidentResponseModeConfig,
    MCPCapabilitySnapshot,
    MCPCapabilityTool,
    MediaKind,
    ModeConfig,
    ModelSnapshot,
    PenetrationTestModeConfig,
    ResourceKind,
    ResourceProvenance,
    ResourceRef,
    ResourceRole,
    ReverseAnalysisModeConfig,
    SessionFile,
    SessionFileKind,
    SessionInput,
    TGATask,
    VulnerabilityResearchModeConfig,
    default_mode_config,
)

__all__ = [name for name in globals() if not name.startswith("_")]
