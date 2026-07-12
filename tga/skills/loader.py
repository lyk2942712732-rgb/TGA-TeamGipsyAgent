from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, Field


class Skill(BaseModel):
    name: str
    modes: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    version: str = "1"
    source: str
    body: str

    @property
    def summary(self) -> str:
        return " ".join(self.body.split())[:900]


def load_skill(path: Path) -> Skill:
    text = path.read_text(encoding="utf-8")
    metadata, body = _split_frontmatter(text)
    return Skill(source=str(path), body=body, **metadata)


def _split_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        raise ValueError("skill is missing YAML frontmatter")
    _, raw, body = text.split("---", 2)
    values: dict[str, object] = {}
    for line in raw.strip().splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            values[key.strip()] = [item.strip() for item in value[1:-1].split(",") if item.strip()]
        else:
            values[key.strip()] = value.strip('"')
    return values, body.lstrip()
