"""Compatibility exports for the canonical Host handler registry."""

from tga.runtime.host_handler_registry import (
    DECLARED_HOST_HANDLER_KEYS as RUNTIME_HOST_HANDLER_KEYS,
    HostHandlerRegistry,
    validate_runtime_host_handlers,
)

__all__ = [
    "HostHandlerRegistry", "RUNTIME_HOST_HANDLER_KEYS",
    "validate_runtime_host_handlers",
]
