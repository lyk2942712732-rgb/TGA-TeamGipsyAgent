"""Finding helpers."""

from __future__ import annotations

from uuid import uuid4


def new_finding_id() -> str:
    return f"finding_{uuid4().hex[:10]}"

