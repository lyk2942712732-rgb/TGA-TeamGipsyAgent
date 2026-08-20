"""Name-addressed, role-neutral skill catalogue."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from .errors import NotFoundError


@dataclass(frozen=True)
class SkillInfo:
    name: str
    description: str


class SkillCatalog:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def set_root(self, root: Path) -> None:
        self.root = root.resolve()

    def _skill_path(self, name: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", name) or name in {".", ".."}:
            raise NotFoundError("invalid skill name")
        path = (self.root / name / "SKILL.md").resolve()
        if self.root not in path.parents:
            raise NotFoundError("invalid skill path")
        return path

    def list(self) -> list[SkillInfo]:
        result: list[SkillInfo] = []
        if not self.root.exists():
            return result
        for path in sorted(self.root.glob("*/SKILL.md")):
            text = path.read_text(encoding="utf-8")
            description = next(
                (line.strip().lstrip("# ").strip() for line in text.splitlines() if line.strip()),
                path.parent.name,
            )
            result.append(SkillInfo(name=path.parent.name, description=description))
        return result

    def read(self, name: str) -> str:
        path = self._skill_path(name)
        if not path.is_file():
            raise NotFoundError(f"skill not found: {name}")
        return path.read_text(encoding="utf-8")

    def write(self, name: str, content: str) -> SkillInfo:
        if not content.strip():
            raise ValueError("skill content cannot be empty")
        path = self._skill_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.parent / f".SKILL.{uuid4().hex}.tmp"
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, path)
        description = next(
            (line.strip().lstrip("# ").strip() for line in content.splitlines() if line.strip()),
            name,
        )
        return SkillInfo(name=name, description=description)

    def delete(self, name: str) -> None:
        path = self._skill_path(name)
        if not path.is_file():
            raise NotFoundError(f"skill not found: {name}")
        path.unlink()
        try:
            path.parent.rmdir()
        except OSError:
            pass


__all__ = ["SkillCatalog", "SkillInfo"]
