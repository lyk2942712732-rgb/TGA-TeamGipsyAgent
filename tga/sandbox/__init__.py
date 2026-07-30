"""Task-scoped sandbox control plane."""

from tga.sandbox.config import SandboxConfig, load_sandbox_config
from tga.sandbox.docker_provider import DockerSandboxProvider
from tga.sandbox.manager import SandboxManager
from tga.sandbox.lifecycle import SandboxLifecycleService
from tga.sandbox.models import (
    ExecResult,
    NetworkGrant,
    ProcessSpec,
    SandboxHandle,
    SandboxProfile,
    SandboxState,
)
from tga.sandbox.provider import SandboxError, SandboxProvider
from tga.sandbox.sandboxd_provider import SandboxdProvider

__all__ = [
    "DockerSandboxProvider",
    "ExecResult",
    "NetworkGrant",
    "ProcessSpec",
    "SandboxConfig",
    "SandboxError",
    "SandboxHandle",
    "SandboxManager",
    "SandboxLifecycleService",
    "SandboxProfile",
    "SandboxProvider",
    "SandboxState",
    "SandboxdProvider",
    "load_sandbox_config",
]
