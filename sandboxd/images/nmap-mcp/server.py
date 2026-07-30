"""Minimal line-delimited MCP server for constrained Nmap operations."""

from __future__ import annotations

import argparse
import ipaddress
import json
import subprocess
import sys
import time
from typing import Any


COMMON_TARGET = {
    "type": "string",
    "description": "An authorized IPv4/IPv6 address or canonical CIDR.",
}
PORTS = {
    "type": "array",
    "items": {"type": "integer", "minimum": 1, "maximum": 65535},
    "minItems": 1,
    "maxItems": 128,
    "uniqueItems": True,
}
CONNECT_TOOLS = [
    {
        "name": "nmap_connect",
        "description": "Run a DNS-free Nmap TCP connect scan against authorized targets and ports.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {"target": COMMON_TARGET, "ports": PORTS},
            "required": ["target", "ports"],
        },
    }
]
RAW_TOOLS = [
    {
        "name": "nmap_syn",
        "description": "Run a rate-limited Nmap SYN scan against authorized targets and ports.",
        "inputSchema": CONNECT_TOOLS[0]["inputSchema"],
    },
    {
        "name": "ping",
        "description": "Send up to five ICMP echo requests to one authorized IP address.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "target": COMMON_TARGET,
                "count": {"type": "integer", "minimum": 1, "maximum": 5},
            },
            "required": ["target"],
        },
    },
    {
        "name": "capture",
        "description": "Capture a bounded number of packets for one authorized IP address.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "target": COMMON_TARGET,
                "seconds": {"type": "integer", "minimum": 1, "maximum": 30},
                "packets": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            "required": ["target"],
        },
    },
]


def target(value: Any, *, address_only: bool = False) -> str:
    text = str(value or "").strip()
    try:
        parsed = ipaddress.ip_address(text)
    except ValueError as exc:
        if address_only:
            raise ValueError("target must be one IP address") from exc
        try:
            parsed = ipaddress.ip_network(text, strict=False)
        except ValueError as network_exc:
            raise ValueError("target must be an IP address or canonical CIDR") from network_exc
    canonical = str(parsed)
    if text != canonical:
        raise ValueError("target must use canonical notation")
    return canonical


def ports(value: Any) -> list[int]:
    if not isinstance(value, list) or not 1 <= len(value) <= 128:
        raise ValueError("ports must contain between 1 and 128 values")
    result = sorted(set(value))
    if len(result) != len(value) or any(not isinstance(port, int) or port < 1 or port > 65535 for port in result):
        raise ValueError("ports must be unique integers between 1 and 65535")
    return result


def command(mode: str, name: str, arguments: dict[str, Any]) -> list[str]:
    if mode == "connect" and name == "nmap_connect":
        return [
            "nmap", "-n", "-Pn", "-sT", "--max-rate", "1000",
            "-p", ",".join(map(str, ports(arguments.get("ports")))),
            target(arguments.get("target")),
        ]
    if mode == "raw" and name == "nmap_syn":
        return [
            "nmap", "-n", "-Pn", "-sS", "--max-rate", "1000",
            "-p", ",".join(map(str, ports(arguments.get("ports")))),
            target(arguments.get("target")),
        ]
    if mode == "raw" and name == "ping":
        count = arguments.get("count", 3)
        if not isinstance(count, int) or not 1 <= count <= 5:
            raise ValueError("count must be between 1 and 5")
        return ["ping", "-n", "-c", str(count), target(arguments.get("target"), address_only=True)]
    if mode == "raw" and name == "capture":
        seconds = arguments.get("seconds", 10)
        packets = arguments.get("packets", 50)
        if not isinstance(seconds, int) or not 1 <= seconds <= 30:
            raise ValueError("seconds must be between 1 and 30")
        if not isinstance(packets, int) or not 1 <= packets <= 100:
            raise ValueError("packets must be between 1 and 100")
        return [
            "timeout", "--signal=TERM", str(seconds),
            "tcpdump", "-n", "-c", str(packets), "host",
            target(arguments.get("target"), address_only=True),
        ]
    raise ValueError("tool is unavailable in this execution mode")


def call(mode: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    try:
        if mode == "raw" and name == "capture":
            destination = target(arguments.get("target"), address_only=True)
            seconds = arguments.get("seconds", 10)
            packets = arguments.get("packets", 50)
            if not isinstance(seconds, int) or not 1 <= seconds <= 30:
                raise ValueError("seconds must be between 1 and 30")
            if not isinstance(packets, int) or not 1 <= packets <= 100:
                raise ValueError("packets must be between 1 and 100")
            capture = subprocess.Popen(
                ["tcpdump", "-n", "-c", str(packets), "host", destination],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            time.sleep(0.2)
            subprocess.run(
                ["ping", "-n", "-c", "3", destination],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=min(seconds, 10),
                check=False,
            )
            try:
                captured, _ = capture.communicate(timeout=seconds)
            except subprocess.TimeoutExpired:
                capture.terminate()
                captured, _ = capture.communicate(timeout=2)
            return {
                "content": [{"type": "text", "text": captured[:262_144]}],
                "isError": capture.returncode not in (0, -15),
            }
        argv = command(mode, name, arguments)
        completed = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=45,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        output = completed.stdout[:262_144]
        return {
            "content": [{"type": "text", "text": output}],
            "isError": completed.returncode not in (0, 1),
        }
    except (ValueError, subprocess.TimeoutExpired) as exc:
        return {
            "content": [{"type": "text", "text": str(exc)}],
            "isError": True,
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("connect", "raw"), required=True)
    mode = parser.parse_args().mode
    tools = CONNECT_TOOLS if mode == "connect" else RAW_TOOLS
    for line in sys.stdin:
        message: dict[str, Any] = {}
        try:
            message = json.loads(line)
            method = message.get("method")
            if method == "initialize":
                result = {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": f"tga-nmap-{mode}", "version": "1.0.0"},
                }
            elif method == "tools/list":
                result = {"tools": tools}
            elif method == "tools/call":
                params = message.get("params") or {}
                result = call(mode, str(params.get("name") or ""), params.get("arguments") or {})
            else:
                continue
            response = {"jsonrpc": "2.0", "id": message["id"], "result": result}
        except Exception as exc:
            response = {
                "jsonrpc": "2.0",
                "id": message.get("id") if isinstance(message, dict) else None,
                "error": {"code": -32602, "message": str(exc)},
            }
        print(json.dumps(response, separators=(",", ":")), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
