"""Filesystem-backed SkillDocument catalog over existing Markdown skills."""

from __future__ import annotations

from pathlib import Path

from tga.domain.skills.models import SkillDocument
from tga.modes import TaskMode
from tga.skills.loader import load_skill


class FileSkillCatalog:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    @classmethod
    def builtin(cls) -> "FileSkillCatalog":
        return cls(Path(__file__).parents[3] / "resources" / "skills")

    def all(self) -> tuple[SkillDocument, ...]:
        documents: dict[str, SkillDocument] = {}
        for path in sorted(self.root.rglob("*.md")):
            skill = load_skill(path)
            if skill.name in documents:
                raise ValueError(f"duplicate Skill document: {skill.name}")
            origin = "builtin" if "builtin" in path.parts else "resource"
            documents[skill.name] = SkillDocument(
                name=skill.name,
                version=skill.version,
                modes=tuple(skill.modes),
                capability_requirements=tuple(skill.capabilities),
                tags=tuple(skill.tags),
                body=skill.body,
                origin=origin,
                source_ref=str(path),
            )
        return tuple(documents[name] for name in sorted(documents))

    def get(self, name: str) -> SkillDocument | None:
        return next((skill for skill in self.all() if skill.name == name), None)

    def compatible(self, mode: TaskMode) -> tuple[SkillDocument, ...]:
        return tuple(skill for skill in self.all() if mode in skill.modes)


__all__ = ["FileSkillCatalog"]
