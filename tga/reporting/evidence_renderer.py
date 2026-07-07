"""Evidence rendering helpers."""

from __future__ import annotations


def quote_excerpt(text: str, limit: int = 240) -> str:
    clean = " ".join((text or "").split())
    return clean[:limit]

