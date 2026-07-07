"""Evidence rendering helpers."""

from __future__ import annotations


def quote_excerpt(text: str, limit: int = 240) -> str:
    clean = " ".join((text or "").split())
    if len(clean) <= limit:
        return clean
    return clean[: max(0, limit - 3)] + "..."


def format_list(values: list[str] | None) -> str:
    if not values:
        return "none"
    return ", ".join(str(value) for value in values)

