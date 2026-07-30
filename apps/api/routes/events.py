"""Events HTTP boundaries."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from tga.application.projections.models import EventPage

from apps.api.routes.support import _require_current_task, _runtime_queries

router = APIRouter(tags=["events"])


@router.get("/tasks/{task_id}/events", response_model=EventPage)
def list_events(
    task_id: str,
    after_seq: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=200),
):
    _require_current_task(task_id)
    return _runtime_queries().events(
        task_id, after_seq=after_seq, limit=limit
    )


@router.get("/tasks/{task_id}/timeline", response_model=EventPage)
def get_timeline(
    task_id: str,
    after_seq: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=200),
):
    """Return the paginated task timeline using the canonical event envelope."""
    _require_current_task(task_id)
    return _runtime_queries().events(
        task_id, after_seq=after_seq, limit=limit
    )


@router.get("/tasks/{task_id}/events/stream")
async def stream_events(task_id: str, request: Request, after_seq: int = 0) -> StreamingResponse:
    # Resolve before opening the stream so a typo produces a normal 404.
    _require_current_task(task_id)
    return StreamingResponse(
        _event_stream(task_id, request, after_seq),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _event_stream(task_id: str, request: Request, cursor: int) -> AsyncIterator[str]:
    """Catch up from SQLite, then wait on the process bus between DB reads."""
    queries = _runtime_queries()
    while not await request.is_disconnected():
        page = queries.events(task_id, after_seq=max(0, cursor), limit=200)
        for event in page.events:
            cursor = event.seq
            yield _sse("event", event.model_dump(mode="json"))
        if page.has_more:
            continue
        if await request.is_disconnected():
            return
        signalled = await queries.wait_for_events(
            task_id, after_seq=cursor, timeout=15.0
        )
        if not signalled:
            # Periodic authoritative fallback repairs missed process-local
            # notifications and doubles as the heartbeat cursor.
            fallback = queries.events(task_id, after_seq=cursor, limit=1)
            if fallback.events:
                continue
            yield _sse("heartbeat", {"latest_seq": fallback.latest_seq})


def _sse(event_type: str, payload: dict[str, Any]) -> str:
    return f"event: {event_type}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
