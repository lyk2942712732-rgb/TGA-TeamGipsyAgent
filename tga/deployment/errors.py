"""Actionable deployment error codes shared by every launcher surface.

The Go launcher, the Python internal CLI and the readiness endpoint all speak
these codes, so a failure reported inside WSL2 keeps its identity when it
surfaces in a Windows terminal.  Every code carries a remediation hint; a code
without one is not actionable and must not be added.
"""

from __future__ import annotations

from enum import StrEnum


class ErrorCode(StrEnum):
    """Stable, machine-readable deployment failure identities."""

    WSL_NOT_AVAILABLE = "WSL_NOT_AVAILABLE"
    WSL_DISTRO_MISSING = "WSL_DISTRO_MISSING"
    WSL_IMPORT_FAILED = "WSL_IMPORT_FAILED"
    SANDBOX_RUNTIME_DISABLED = "SANDBOX_RUNTIME_DISABLED"
    SANDBOXD_SOCKET_MISSING = "SANDBOXD_SOCKET_MISSING"
    SANDBOXD_UID_DENIED = "SANDBOXD_UID_DENIED"
    DOCKER_UNAVAILABLE = "DOCKER_UNAVAILABLE"
    RUNSC_NOT_REGISTERED = "RUNSC_NOT_REGISTERED"
    NFTABLES_UNAVAILABLE = "NFTABLES_UNAVAILABLE"
    CGROUP_V2_UNAVAILABLE = "CGROUP_V2_UNAVAILABLE"
    PROFILE_IMAGE_MISSING = "PROFILE_IMAGE_MISSING"
    PROFILE_DIGEST_INVALID = "PROFILE_DIGEST_INVALID"
    TOOLSET_DIGEST_MISMATCH = "TOOLSET_DIGEST_MISMATCH"
    API_START_FAILED = "API_START_FAILED"
    READINESS_TIMEOUT = "READINESS_TIMEOUT"
    WEB_BUNDLE_MISSING = "WEB_BUNDLE_MISSING"
    RUN_ROOT_UNWRITABLE = "RUN_ROOT_UNWRITABLE"
    STATE_LOCKED = "STATE_LOCKED"
    PORT_UNAVAILABLE = "PORT_UNAVAILABLE"


REMEDIATION: dict[ErrorCode, str] = {
    ErrorCode.WSL_NOT_AVAILABLE: (
        "WSL2 is not enabled. Run `wsl --install` from an elevated PowerShell, "
        "then reboot and retry `tga up`."
    ),
    ErrorCode.WSL_DISTRO_MISSING: (
        "The TGA-Runtime WSL distribution is not registered. Run `tga up` once "
        "with network access so it can be imported automatically."
    ),
    ErrorCode.WSL_IMPORT_FAILED: (
        "Importing the TGA-Runtime distribution failed. Check free disk space "
        "on the system drive, then run `tga reset --runtime` and retry."
    ),
    ErrorCode.SANDBOX_RUNTIME_DISABLED: (
        "Sandbox runtime is 'disabled' in sandbox.json. TGA runs without "
        "isolated tool execution; set runtime to 'enforced' once profile "
        "images are published."
    ),
    ErrorCode.SANDBOXD_SOCKET_MISSING: (
        "tga-sandboxd is not listening. Run `tga status` to confirm the "
        "service state; `tga up` normally starts it."
    ),
    ErrorCode.SANDBOXD_UID_DENIED: (
        "The API process UID is not in sandbox.json allowed_client_uids. "
        "Run `tga up` so the configuration is regenerated for this host."
    ),
    ErrorCode.DOCKER_UNAVAILABLE: (
        "Docker Engine is not reachable. On Linux ensure docker.service is "
        "running; on Windows ensure the TGA-Runtime distribution has started."
    ),
    ErrorCode.RUNSC_NOT_REGISTERED: (
        "The gVisor 'runsc' runtime is not registered with Docker. Reinstall "
        "the runtime layer with `tga up --repair`."
    ),
    ErrorCode.NFTABLES_UNAVAILABLE: (
        "nftables is unavailable, so sandbox network policy cannot be "
        "enforced. Ensure the host kernel exposes nf_tables."
    ),
    ErrorCode.CGROUP_V2_UNAVAILABLE: (
        "cgroup v2 is unavailable, so sandbox resource limits cannot be "
        "enforced. Enable unified cgroup hierarchy on the host."
    ),
    ErrorCode.PROFILE_IMAGE_MISSING: (
        "A sandbox profile image is not present locally and could not be "
        "pulled. Check registry connectivity or import the offline bundle."
    ),
    ErrorCode.PROFILE_DIGEST_INVALID: (
        "A sandbox profile image is not digest-pinned. Publish real images and "
        "regenerate sandbox.json; placeholder digests cannot be enforced."
    ),
    ErrorCode.TOOLSET_DIGEST_MISMATCH: (
        "A profile's toolset digest does not match its image. Refresh the tool "
        "inventory for that profile and retry."
    ),
    ErrorCode.API_START_FAILED: (
        "The TGA API process failed to start. Inspect `tga logs --component "
        "api` for the underlying exception."
    ),
    ErrorCode.READINESS_TIMEOUT: (
        "TGA did not reach readiness within the allotted time. Run "
        "`tga doctor` to see which component is still not ready."
    ),
    ErrorCode.WEB_BUNDLE_MISSING: (
        "No frontend bundle was found. Set TGA_WEB_DIST to a built bundle, or "
        "run `npm --prefix apps/web run build` in a development checkout."
    ),
    ErrorCode.RUN_ROOT_UNWRITABLE: (
        "TGA_RUN_ROOT is not writable by this process. Fix ownership on the "
        "run root, or point TGA_RUN_ROOT at a writable location."
    ),
    ErrorCode.STATE_LOCKED: (
        "Another `tga` command holds the deployment lock. Wait for it to "
        "finish, or run `tga status` to inspect the current state."
    ),
    ErrorCode.PORT_UNAVAILABLE: (
        "The requested port is already in use. Stop the conflicting service "
        "or pass `tga up --port <other>`."
    ),
}


class DeploymentError(RuntimeError):
    """A deployment failure that carries a stable code and a fix hint."""

    def __init__(self, code: ErrorCode, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        self.remediation = REMEDIATION.get(code, "")
        message = f"[{code}] {detail}" if detail else f"[{code}]"
        super().__init__(message)

    def to_dict(self) -> dict[str, str]:
        return {
            "code": str(self.code),
            "detail": self.detail,
            "remediation": self.remediation,
        }
