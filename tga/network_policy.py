"""Deterministic task-input URL extraction and network authorization helpers."""

from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urlsplit, urlunsplit

from tga.contracts import NetworkExecutionPolicy


URL_PATTERN = re.compile(r"https?://[^\s<>\]\[()\"']+", re.IGNORECASE)
def extract_input_urls(text: str) -> list[str]:
    values: list[str] = []
    for candidate in URL_PATTERN.findall(text):
        clean = candidate.rstrip(".,;:!?)]}，。；：！？）】}")
        try:
            parsed = urlsplit(clean)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                continue
            if parsed.username or parsed.password:
                continue
            normalized = urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path or "/", parsed.query, ""))
        except ValueError:
            continue
        if normalized not in values:
            values.append(normalized)
    return values


def normalize_origin(value: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("origin must be an absolute HTTP(S) URL without credentials")
    host = parsed.hostname.lower()
    port = parsed.port
    default_port = 80 if parsed.scheme == "http" else 443
    authority = host if port in {None, default_port} else f"{host}:{port}"
    return f"{parsed.scheme.lower()}://{authority}"


def input_network_seeds(text: str) -> tuple[list[str], str | None]:
    urls = extract_input_urls(text)
    origins = list(dict.fromkeys(normalize_origin(item) for item in urls))
    return origins, urls[0] if urls else None


def authorize_url(url: str, policy: NetworkExecutionPolicy, *, resolve_dns: bool = True) -> list[str]:
    origin = normalize_origin(url)
    if policy.access == "disabled":
        raise PermissionError("NETWORK_ACCESS_DISABLED")
    if policy.access == "task_sources" and origin not in policy.seed_origins:
        raise PermissionError("NETWORK_ORIGIN_NOT_IN_TASK_SOURCES")
    host = urlsplit(url).hostname or ""
    addresses = _resolved_addresses(host) if resolve_dns else _literal_addresses(host)
    if policy.access == "custom" and not _custom_target_allowed(
        origin=origin,
        host=host,
        addresses=addresses,
        policy=policy,
    ):
        raise PermissionError("NETWORK_TARGET_NOT_IN_CUSTOM_ALLOWLIST")
    return [str(address) for address in addresses]


def _custom_target_allowed(
    *,
    origin: str,
    host: str,
    addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address],
    policy: NetworkExecutionPolicy,
) -> bool:
    if origin in policy.custom_origins:
        return True
    normalized_host = host.casefold().rstrip(".")
    for rule in policy.custom_domains:
        candidate = rule.strip().casefold().rstrip(".")
        if not candidate:
            continue
        if candidate.startswith("*."):
            suffix = candidate[1:]
            if normalized_host.endswith(suffix) and normalized_host != suffix[1:]:
                return True
        elif normalized_host == candidate:
            return True
    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for value in policy.custom_cidrs:
        try:
            networks.append(ipaddress.ip_network(value.strip(), strict=False))
        except ValueError as exc:
            raise ValueError(f"invalid custom CIDR: {value}") from exc
    return bool(addresses) and all(
        any(address.version == network.version and address in network for network in networks)
        for address in addresses
    )


def _literal_addresses(host: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        return [ipaddress.ip_address(host)]
    except ValueError:
        return []


def _resolved_addresses(host: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    literal = _literal_addresses(host)
    if literal:
        return literal
    try:
        values = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise PermissionError("NETWORK_DNS_RESOLUTION_FAILED") from exc
    result: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for item in values:
        address = ipaddress.ip_address(item[4][0])
        if address not in result:
            result.append(address)
    return result

