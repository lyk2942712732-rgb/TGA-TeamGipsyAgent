"""Runtime context construction package with a phase-1 compatibility bridge."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


_MODULE_NAME = "tga.runtime._legacy_context"
_LEGACY_PATH = Path(__file__).resolve().parent.parent / "context.py"
_spec = importlib.util.spec_from_file_location(_MODULE_NAME, _LEGACY_PATH)
if _spec is None or _spec.loader is None:  # pragma: no cover - invalid installation
    raise ImportError(f"cannot load legacy runtime context from {_LEGACY_PATH}")
_legacy = importlib.util.module_from_spec(_spec)
sys.modules[_MODULE_NAME] = _legacy
_spec.loader.exec_module(_legacy)

SessionContextBuilder = _legacy.SessionContextBuilder
build_working_messages = _legacy.build_working_messages
from tga.runtime.context.context_builder import (
    BuiltContext,
    ContextBuilder,
    ContextEnvelope,
    ContextSection,
    KnowledgeContext,
)

__all__ = [
    "BuiltContext", "ContextBuilder", "ContextEnvelope", "ContextSection",
    "KnowledgeContext", "SessionContextBuilder", "build_working_messages",
]
