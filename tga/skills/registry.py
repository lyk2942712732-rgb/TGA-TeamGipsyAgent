from __future__ import annotations

from pathlib import Path

from .loader import Skill, load_skill
from .store import SkillStore
from tga.modes import TaskMode


class SkillRegistry:
    def __init__(self, root: Path | None = None, custom_root: Path | None = None) -> None:
        self.root = root or Path(__file__).with_name("builtin")
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

    def query(self, *, mode: TaskMode, tags: list[str] | None = None, limit: int = 3) -> list[Skill]:
        """Select at most three mode-compatible skills, preferring tag matches.

        A sparse attack-class vocabulary must not result in an empty context:
        compatible skills are retained as a deterministic fallback after the
        directly matched playbooks.
        """
        self.reload()
        requested = set(tags or [])
        matches = [skill for skill in self._skills.values() if mode in skill.modes]
        direct = [skill for skill in matches if requested.intersection(skill.tags)]
        if direct:
            matches = direct
        return sorted(
            matches,
            key=lambda skill: (-len(requested.intersection(skill.tags)), skill.name),
        )[: max(0, min(limit, 3))]

    def for_turn(self, *, mode: TaskMode, attack_class: str) -> list[Skill]:
        return self.query(mode=mode, tags=_attack_tags(attack_class), limit=3)

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


def _attack_tags(attack_class: str) -> list[str]:
    normalized = attack_class.strip().lower()
    aliases = {
        "recon": ["recon", "links", "forms", "js"],
        "web": ["sqli", "idor", "upload", "auth"],
        "sqli": ["sqli", "web"],
        "idor": ["idor", "auth", "web"],
        "upload": ["upload", "web"],
        "rev": ["binary", "metadata"],
        "pwn": ["binary"],
        "binary": ["binary", "metadata"],
        "crypto": ["crypto", "encoding", "decoding"],
    }
    return aliases.get(normalized, [normalized])
