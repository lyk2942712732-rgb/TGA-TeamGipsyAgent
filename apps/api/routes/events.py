"""Events HTTP boundaries."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from tga.evidence.store import EvidenceStore

from apps.api.routes.support import _normalize_event, _require_current_task, _task_root

router = APIRouter(tags=["events"])


@router.get("/tasks/{task_id}/events")
def list_events(task_id: str, after_seq: int = 0, limit: int = 200) -> dict[str, Any]:
    _require_current_task(task_id)
    store = EvidenceStore(_task_root(task_id) / "evidence.db")
    try:
        bounded_limit = max(1, min(limit, 200))
        events = [
            _normalize_event(event.model_dump(mode="json"), after_seq + index + 1)
            for index, event in enumerate(store.list_events(task_id, after_seq=after_seq, limit=bounded_limit))
        ]
        latest_seq = store.latest_agent_event_seq(task_id)
        return {"events": events, "latest_seq": latest_seq}
    finally:
        store.close()


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
    """Poll the repository so the transport works before the manager owns a bus."""
    heartbeat_at = 0.0
    while not await request.is_disconnected():
        now = asyncio.get_running_loop().time()
        heartbeat_due = now - heartbeat_at >= 15
        store = EvidenceStore(_task_root(task_id) / "evidence.db")
        try:
            events = [
                _normalize_event(event.model_dump(mode="json"), cursor + index + 1)
                for index, event in enumerate(store.list_events(task_id, after_seq=cursor, limit=200))
            ]
            latest_seq = store.latest_agent_event_seq(task_id) if heartbeat_due else None
        finally:
            store.close()
        for event in events:
            cursor = event["seq"]
            yield _sse("event", event)
        if heartbeat_due:
            heartbeat_at = now
            yield _sse("heartbeat", {"latest_seq": latest_seq})
        await asyncio.sleep(1)


def _sse(event_type: str, payload: dict[str, Any]) -> str:
    return f"event: {event_type}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
