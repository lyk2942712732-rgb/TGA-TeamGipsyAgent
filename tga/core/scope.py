"""Authorization scope checks for TGA.

This module intentionally stays conservative for Week 1. If a target cannot be
clearly matched to the allowlist, active tooling should not run.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse


def _host_port(value: str) -> tuple[str, int | None]:
    raw = value.strip()
    parsed = urlparse(raw if "://" in raw else f"//{raw}")
    host = (parsed.hostname or raw.split("/", 1)[0]).strip().lower()
    port = parsed.port
    if port is None and parsed.scheme == "http":
        port = 80
    if port is None and parsed.scheme == "https":
        port = 443
    return host, port


def _matches_scope_entry(target: str, scope_entry: str) -> bool:
    target_host, target_port = _host_port(target)
    scope_host, scope_port = _host_port(scope_entry)
    if not target_host or not scope_host:
        return False

    if scope_port is not None and target_port is not None and scope_port != target_port:
        return False

    try:
        network = ipaddress.ip_network(scope_host, strict=False)
        return ipaddress.ip_address(target_host) in network
    except ValueError:
        pass

    if scope_host.startswith("*."):
        suffix = scope_host[1:]
        return target_host.endswith(suffix)
    return target_host == scope_host


def is_in_scope(target: str, scope: list[str]) -> bool:
    return any(_matches_scope_entry(target, entry) for entry in scope)


def require_in_scope(target: str, scope: list[str]) -> None:
    if not is_in_scope(target, scope):
        raise ValueError("OUT_OF_SCOPE")

