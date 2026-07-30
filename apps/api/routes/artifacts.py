"""Artifacts HTTP boundaries."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse

from tga.contracts import TGATask
from tga.evidence.indexing import build_artifact_index, retrieve_segments
from tga.inputs import task_artifact_root

from apps.api.routes.support import _runtime_queries, _snapshot, _task_root

router = APIRouter(tags=["artifacts"])


@router.get("/tasks/{task_id}/artifacts/{artifact_id}", response_model=None)
def artifact(
    task_id: str,
    artifact_id: str,
    download: bool = False,
    query: str | None = None,
    section: str | None = None,
    offset: int = 0,
    limit: int = 6000,
):
    """Return a bounded, redacted preview unless an explicit download is requested.

    The preview is the product-facing endpoint.  Raw artifact delivery remains
    explicit for already-authorized users and is never embedded in a runtime
    list or report.
    """
    snapshot = _snapshot(task_id)
    try:
        item = _runtime_queries().artifact(task_id, artifact_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="artifact not found")
    task = TGATask.model_validate(snapshot["task"])
    task_root = _task_root(task_id)
    roots = (
        task_artifact_root(task_root, task),
        (task_root / "workspace" / "shared" / "artifacts").resolve(),
    )
    candidates = []
    for root in roots:
        candidate = (root / str(item.get("path") or "")).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid artifact path") from exc
        candidates.append(candidate)
    path = next((candidate for candidate in candidates if candidate.is_file()), None)
    if path is None:
        raise HTTPException(status_code=404, detail="artifact file not found")
    if download:
        return FileResponse(path, filename=path.name)
    if query or section or offset:
        index = _runtime_queries().artifact_index(task_id, artifact_id)
        if index is None:
            index = build_artifact_index(
                task_id=task_id,
                artifact_id=artifact_id,
                raw=path.read_bytes(),
                document_type="html" if path.suffix.casefold() in {".html", ".htm"} else None,
            )
        retrieval = retrieve_segments(
            index,
            query=(query or "")[:256] or None,
            section=(section or "")[:256] or None,
            offset=max(0, offset),
            limit=max(1, min(limit, 12_000)),
        )
        for match in retrieval["matches"]:
            match["text"], _ = _redact_artifact_text(match["text"])
        return JSONResponse({"artifact": {"id": artifact_id, "kind": item.get("kind")}, "retrieval": retrieval})
    return JSONResponse(_artifact_preview(item, path))


def _artifact_preview(item: dict[str, Any], path: Path, *, byte_limit: int = 16_384) -> dict[str, Any]:
    raw = path.read_bytes()[: byte_limit + 1]
    truncated = path.stat().st_size > byte_limit
    binary = b"\x00" in raw
    if binary:
        excerpt = "[binary artifact omitted from inline preview]"
    else:
        excerpt = raw.decode("utf-8", errors="replace")
    redacted, redaction_count = _redact_artifact_text(excerpt)
    return {
        "artifact": {key: item.get(key) for key in ("id", "kind", "tool", "target", "created_at", "sha256")},
        "preview": redacted,
        "truncated": truncated,
        "binary": binary,
        "redactions": redaction_count,
        "byte_limit": byte_limit,
        "download_url": None,
    }


def _redact_artifact_text(value: str) -> tuple[str, int]:
    import re

    patterns = (
        r"(?im)^\s*((?:authorization|proxy-authorization|cookie|set-cookie|x-api-key)\s*:\s*)[^\r\n]+",
        r"(?i)\b((?:token|secret|api[_-]?key|password)\s*[=:]\s*)([^\s,;}&]+)",
        r"(?i)\b(Bearer\s+)[A-Za-z0-9._~+/=-]{8,}",
    )
    count = 0
    for pattern in patterns:
        def replace(match: re.Match[str]) -> str:
            nonlocal count
            count += 1
            return f"{match.group(1)}[REDACTED]"
        value = re.sub(pattern, replace, value)
    return value, count
