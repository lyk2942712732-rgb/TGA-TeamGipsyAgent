"""Capability execution layer for approved CTF actions."""

from tga.capabilities.executor import CapabilityExecutor
from tga.capabilities.models import ActionResult, ActionSpec
from tga.capabilities.registry import CapabilityRegistry

__all__ = ["ActionSpec", "ActionResult", "CapabilityExecutor", "CapabilityRegistry"]
