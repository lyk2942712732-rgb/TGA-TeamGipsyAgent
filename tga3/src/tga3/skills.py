"""Name-addressed, role-neutral skill catalogue."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .errors import NotFoundError


@dataclass(frozen=True)
class SkillInfo:
    name: str
    description: str


class SkillCatalog:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def _skill_path(self, name: str) -> Path:
        if not name or Path(name).name != name:
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


__all__ = ["SkillCatalog", "SkillInfo"]
