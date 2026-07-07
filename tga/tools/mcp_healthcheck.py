from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from pydantic import BaseModel

from tga.tools.mcp_catalog import MCPServerSpec, discover_mcp_security_hub


class HealthcheckRecord(BaseModel):
    tool: str
    status: str
    detail: str


def check_mcp_security_hub(hub_root: str | Path) -> list[HealthcheckRecord]:
    try:
        catalog = discover_mcp_security_hub(hub_root)
    except Exception as exc:
        return [HealthcheckRecord(tool="mcp-security-hub", status="failed", detail=str(exc))]

    docker_available = shutil.which("docker") is not None
    docker_daemon = _docker_daemon_available() if docker_available else False
    records: list[HealthcheckRecord] = []
    for server in catalog.servers:
        if server.dockerfile is None:
            records.append(HealthcheckRecord(tool=server.id, status="failed", detail="Dockerfile missing"))
            continue
        if not docker_available:
            records.append(HealthcheckRecord(tool=server.id, status="missing", detail="docker command not found"))
            continue
        if not docker_daemon:
            records.append(HealthcheckRecord(tool=server.id, status="failed", detail="docker daemon unavailable"))
            continue
        image_status = _docker_image_status(server)
        records.append(image_status)
    return records


def records_to_json(records: list[HealthcheckRecord]) -> str:
    return json.dumps([record.model_dump() for record in records], ensure_ascii=False, indent=2)


def _docker_image_status(server: MCPServerSpec) -> HealthcheckRecord:
    completed = subprocess.run(
        ["docker", "image", "inspect", server.image],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode == 0:
        return HealthcheckRecord(tool=server.id, status="available", detail=f"image present: {server.image}")
    return HealthcheckRecord(
        tool=server.id,
        status="missing",
        detail=f"image not built: {server.image}",
    )


def _docker_daemon_available() -> bool:
    completed = subprocess.run(
        ["docker", "info", "--format", "{{.ServerVersion}}"],
        text=True,
        capture_output=True,
        check=False,
    )
    return completed.returncode == 0
