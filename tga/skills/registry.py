from __future__ import annotations

from pathlib import Path

from .loader import Skill, load_skill
from .store import SkillStore
from tga.modes import TaskMode


class SkillRegistry:
    def __init__(self, root: Path | None = None, custom_root: Path | None = None) -> None:
        self.root = root or Path(__file__).parents[2] / "resources" / "skills"
        self.custom_store = SkillStore(custom_root)
        self._skills: dict[str, Skill] = {}
        self._origins: dict[str, str] = {}
        self.reload()

    def reload(self) -> None:
        builtins = [load_skill(path) for path in self.root.rglob("*.md")]
        self._builtin_names = {skill.name for skill in builtins}
        disabled = self.custom_store.disabled_names()
        self._skills = {skill.name: skill for skill in builtins if skill.name not in disabled}
        self._origins = {skill.name: "builtin" for skill in builtins if skill.name not in disabled}
        for skill in self.custom_store.list():
            # User-owned files are overlays. This makes built-ins editable
            # without mutating package files and keeps the original recoverable.
            self._skills[skill.name] = skill
            self._origins[skill.name] = "custom"

    def compatible(self, mode: TaskMode) -> list[tuple[Skill, str]]:
        """Return mode-compatible documents with their operator-facing origin."""
        self.reload()
        return [
            (skill, self._origins[skill.name])
            for skill in sorted(self._skills.values(), key=lambda item: item.name)
            if mode in skill.modes
        ]

    def get(self, name: str) -> Skill | None:
        self.reload()
        return self._skills.get(name)

    def snapshot(self) -> dict:
        self.reload()
        return {"skills": [{
            "name": skill.name,
            "modes": skill.modes,
            "capabilities": skill.capabilities,
            "tags": skill.tags,
            "version": skill.version,
            "source": self._origins[skill.name],
            "summary": skill.summary,
            "editable": True,
        } for skill in sorted(self._skills.values(), key=lambda item: item.name)]}

    def detail(self, name: str) -> dict | None:
        skill = self.get(name)
        if skill is None:
            return None
        return {
            "name": skill.name,
            "modes": skill.modes,
            "capabilities": skill.capabilities,
            "tags": skill.tags,
            "version": skill.version,
            "source": self._origins[skill.name],
            "summary": skill.summary,
            "body": skill.body,
            "editable": True,
        }

    def is_builtin(self, name: str) -> bool:
        self.reload()
        return name in self._builtin_names
