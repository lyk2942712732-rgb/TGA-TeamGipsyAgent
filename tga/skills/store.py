from __future__ import annotations

import os
from pathlib import Path

from tga.modes import TaskMode

from .loader import Skill, load_skill, load_skill_text


MAX_SKILL_BYTES = 512 * 1024


def custom_skill_root() -> Path:
    configured = os.environ.get("TGA_CUSTOM_SKILLS_ROOT")
    if configured:
        return Path(configured).resolve()
    return (Path(os.environ.get("TGA_RUN_ROOT", "runs")) / "_skills").resolve()


class SkillStore:
    """Persist operator-authored skills separately from packaged built-ins."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or custom_skill_root()).resolve()

    def list(self) -> list[Skill]:
        if not self.root.is_dir():
            return []
        skills: list[Skill] = []
        for path in sorted(self.root.glob("*.md")):
            try:
                skills.append(load_skill(path))
            except (OSError, ValueError):
                # A manually corrupted file must not hide healthy skills.
                continue
        return skills

    def disabled_names(self) -> set[str]:
        disabled = self.root / ".disabled"
        if not disabled.is_dir():
            return set()
        return {path.name for path in disabled.iterdir() if path.is_file() and not path.is_symlink()}

    def get(self, name: str) -> Skill | None:
        path = self._path(name)
        if not path.is_file() or path.is_symlink():
            return None
        return load_skill(path)

    def import_markdown(self, raw: bytes, *, overwrite: bool = False) -> Skill:
        if not raw or len(raw) > MAX_SKILL_BYTES:
            raise ValueError(f"skill file must be between 1 and {MAX_SKILL_BYTES} bytes")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("skill file must be UTF-8 Markdown") from exc
        self.root.mkdir(parents=True, exist_ok=True)
        skill = load_skill_text(text, source="upload")
        path = self._path(skill.name)
        if path.exists() and not overwrite:
            raise FileExistsError(skill.name)
        self._write_atomic(path, render_skill(skill))
        self.enable(skill.name)
        return load_skill(path)

    def update(
        self,
        name: str,
        *,
        modes: list[TaskMode],
        capabilities: list[str],
        tags: list[str],
        version: str,
        body: str,
    ) -> Skill:
        path = self._path(name)
        skill = Skill(
            name=name,
            modes=modes,
            capabilities=capabilities,
            tags=tags,
            version=version,
            source=str(path),
            body=body,
        )
        self._write_atomic(path, render_skill(skill))
        self.enable(name)
        return load_skill(path)

    def delete(self, name: str) -> bool:
        path = self._path(name)
        if not path.is_file() or path.is_symlink():
            return False
        path.unlink()
        return True

    def disable(self, name: str) -> None:
        path = self._path(name)
        path.unlink(missing_ok=True)
        disabled = self.root / ".disabled"
        disabled.mkdir(parents=True, exist_ok=True)
        marker = (disabled / name).resolve()
        marker.relative_to(disabled.resolve())
        marker.write_text("disabled\n", encoding="utf-8")

    def enable(self, name: str) -> None:
        self._path(name)
        marker = self.root / ".disabled" / name
        marker.unlink(missing_ok=True)

    def _path(self, name: str) -> Path:
        # Let Skill perform the identifier validation without accepting a body
        # or user-controlled path.
        if not name or len(name) > 64 or not all(char.islower() or char.isdigit() or char in "_-" for char in name):
            raise ValueError("invalid skill name")
        path = (self.root / f"{name}.md").resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("invalid skill path") from exc
        return path

    @staticmethod
    def _write_atomic(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(content, encoding="utf-8", newline="\n")
        temporary.replace(path)


def render_skill(skill: Skill) -> str:
    def values(items: list[str]) -> str:
        return ", ".join(items)

    return (
        "---\n"
        f"name: {skill.name}\n"
        f'version: "{skill.version}"\n'
        f"modes: [{values(skill.modes)}]\n"
        f"capabilities: [{values(skill.capabilities)}]\n"
        f"tags: [{values(skill.tags)}]\n"
        "---\n"
        f"{skill.body.strip()}\n"
    )
