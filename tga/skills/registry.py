from __future__ import annotations

from pathlib import Path

from .loader import Skill, load_skill


class SkillRegistry:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or Path(__file__).with_name("builtin")
        self._skills = {skill.name: skill for skill in (load_skill(path) for path in self.root.rglob("*.md"))}

    def query(self, *, mode: str, tags: list[str] | None = None, limit: int = 3) -> list[Skill]:
        """Select at most three mode-compatible skills, preferring tag matches.

        A sparse attack-class vocabulary must not result in an empty context:
        compatible skills are retained as a deterministic fallback after the
        directly matched playbooks.
        """
        requested = set(tags or [])
        matches = [skill for skill in self._skills.values() if mode in skill.modes]
        return sorted(
            matches,
            key=lambda skill: (-len(requested.intersection(skill.tags)), skill.name),
        )[: max(0, min(limit, 3))]

    def for_turn(self, *, mode: str, attack_class: str) -> list[Skill]:
        return self.query(mode=mode, tags=_attack_tags(attack_class), limit=3)

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def snapshot(self) -> dict:
        return {"skills": [{"name": skill.name, "modes": skill.modes, "capabilities": skill.capabilities, "tags": skill.tags, "version": skill.version, "source": skill.source, "summary": skill.summary} for skill in self._skills.values()]}


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
