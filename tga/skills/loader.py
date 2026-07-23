from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, Field, field_validator
from tga.modes import TaskMode, normalize_modes


class Skill(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    modes: list[TaskMode] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list, max_length=32)
    tags: list[str] = Field(default_factory=list, max_length=32)
    version: str = Field(default="1", min_length=1, max_length=32)
    source: str
    body: str = Field(min_length=1, max_length=500_000)

    @field_validator("modes", mode="before")
    @classmethod
    def migrate_modes(cls, value):
        return normalize_modes(value)

    @field_validator("capabilities", "tags")
    @classmethod
    def normalize_tokens(cls, value):
        values = [str(item).strip() for item in (value or []) if str(item).strip()]
        if any(len(item) > 80 or not re.fullmatch(r"[A-Za-z0-9_.:-]+", item) for item in values):
            raise ValueError("skill tokens must be short identifiers")
        return list(dict.fromkeys(values))

    @field_validator("version")
    @classmethod
    def validate_version(cls, value: str) -> str:
        value = value.strip()
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,32}", value):
            raise ValueError("skill version must be a short identifier")
        return value

    @property
    def summary(self) -> str:
        return " ".join(self.body.split())[:900]


def load_skill(path: Path) -> Skill:
    text = path.read_text(encoding="utf-8")
    return load_skill_text(text, source=str(path))


def load_skill_text(text: str, *, source: str) -> Skill:
    metadata, body = _split_frontmatter(text)
    return Skill(source=source, body=body, **metadata)


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
