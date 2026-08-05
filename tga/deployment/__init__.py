"""Deployment layer: process layout, readiness, lifecycle state and services.

This package owns everything the `tga up` entrypoint needs in order to bring a
TGA installation from "nothing runs" to "readiness is true", on both a Windows
workstation and a Linux server.  Runtime and application code must not reach
around it to discover paths or service state.
"""

from tga.deployment.errors import DeploymentError, ErrorCode
from tga.deployment.paths import run_root, web_dist

__all__ = ["DeploymentError", "ErrorCode", "run_root", "web_dist"]
