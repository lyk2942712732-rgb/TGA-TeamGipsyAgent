"""Task domain models."""

from tga.domain.task import models as _legacy_models
from tga.domain.task.hints import HintStatus, TaskHint, TaskScope
from tga.domain.task.interventions import InterventionKind, UserIntervention
from tga.domain.task.models import *
from tga.domain.task.spec import DirectiveKind, TaskDirective, TaskSpec
from tga.domain.task.status import TaskStatus

__all__ = [
    *_legacy_models.__all__,
    "DirectiveKind", "HintStatus", "InterventionKind", "TaskDirective",
    "TaskHint", "TaskScope", "TaskSpec", "TaskStatus", "UserIntervention",
]
