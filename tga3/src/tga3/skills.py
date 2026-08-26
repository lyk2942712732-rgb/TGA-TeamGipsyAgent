"""Directory-addressed, role-neutral skill package catalogue."""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from uuid import uuid4

from .errors import ConflictError, NotFoundError


@dataclass(frozen=True)
class SkillInfo:
    name: str
    description: str
    file_count: int


@dataclass(frozen=True)
class SkillFile:
    path: str
    content: str


@dataclass(frozen=True)
class SkillPackage:
    name: str
    description: str
    files: tuple[SkillFile, ...]


class SkillCatalog:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def set_root(self, root: Path) -> None:
        self.root = root.resolve()

    def _skill_dir(self, name: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", name) or name in {".", ".."}:
            raise NotFoundError("invalid skill name")
        path = (self.root / name).resolve()
        if path.parent != self.root:
            raise NotFoundError("invalid skill path")
        return path

    @staticmethod
    def _relative_markdown_path(raw: str) -> PurePosixPath:
        path = PurePosixPath(raw.replace("\\", "/"))
        if (
            path.is_absolute()
            or not path.parts
            or any(part in {"", ".", ".."} for part in path.parts)
            or path.suffix.lower() != ".md"
        ):
            raise ValueError(f"invalid Markdown file path: {raw}")
        return path

    def _files(self, folder: Path) -> tuple[SkillFile, ...]:
        if not folder.is_dir():
            raise NotFoundError(f"skill not found: {folder.name}")
        paths = sorted(
            (
                path
                for path in folder.rglob("*")
                if path.is_file()
                and not path.is_symlink()
                and path.suffix.lower() == ".md"
                and folder in path.resolve().parents
            ),
            key=lambda path: (path.name.upper() != "SKILL.MD", path.relative_to(folder).as_posix().lower()),
        )
        if not paths:
            raise NotFoundError(f"skill package has no Markdown files: {folder.name}")
        return tuple(
            SkillFile(path=path.relative_to(folder).as_posix(), content=path.read_text(encoding="utf-8"))
            for path in paths
        )

    @staticmethod
    def _description(name: str, files: tuple[SkillFile, ...]) -> str:
        primary = next((item for item in files if item.path.lower() == "skill.md"), files[0])
        return next(
            (line.strip().lstrip("# ").strip() for line in primary.content.splitlines() if line.strip()),
            name,
        )

    def list(self) -> list[SkillInfo]:
        result: list[SkillInfo] = []
        if not self.root.exists():
            return result
        folders = (path for path in self.root.iterdir() if path.is_dir())
        for folder in sorted(folders, key=lambda path: path.name.lower()):
            try:
                files = self._files(folder)
            except NotFoundError:
                continue
            result.append(SkillInfo(folder.name, self._description(folder.name, files), len(files)))
        return result

    def package(self, name: str) -> SkillPackage:
        files = self._files(self._skill_dir(name))
        return SkillPackage(name, self._description(name, files), files)

    def read(self, name: str, document: str = "SKILL.md") -> str:
        """Read one Markdown document; Agent discovery always starts at SKILL.md."""
        folder = self._skill_dir(name)
        if not folder.is_dir():
            raise NotFoundError(f"skill not found: {name}")
        relative = self._relative_markdown_path(document)
        path = folder.joinpath(*relative.parts).resolve()
        if folder not in path.parents or not path.is_file():
            raise NotFoundError(f"skill document not found: {name}/{relative.as_posix()}")
        return path.read_text(encoding="utf-8")

    def write_package(self, name: str, documents: dict[str, str]) -> SkillPackage:
        if not documents:
            raise ValueError("skill package must contain Markdown files")
        normalized: dict[PurePosixPath, str] = {}
        for raw_path, content in documents.items():
            path = self._relative_markdown_path(raw_path)
            if not content.strip():
                raise ValueError(f"Markdown file cannot be empty: {raw_path}")
            if path in normalized:
                raise ValueError(f"duplicate Markdown file path: {raw_path}")
            normalized[path] = content
        if not any(path.as_posix().lower() == "skill.md" for path in normalized):
            raise ValueError("skill package must contain SKILL.md")

        folder = self._skill_dir(name)
        folder.mkdir(parents=True, exist_ok=True)
        existing = {
            path.relative_to(folder).as_posix(): path
            for path in folder.rglob("*")
            if path.is_file() and path.suffix.lower() == ".md"
        }
        wanted = {path.as_posix() for path in normalized}
        for relative, path in existing.items():
            if relative not in wanted:
                path.unlink()
        for relative, content in normalized.items():
            target = folder.joinpath(*relative.parts)
            if folder not in target.resolve().parents:
                raise ValueError(f"Markdown file escapes skill package: {relative.as_posix()}")
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.parent / f".{target.name}.{uuid4().hex}.tmp"
            temporary.write_text(content, encoding="utf-8")
            os.replace(temporary, target)
        for directory in sorted((path for path in folder.rglob("*") if path.is_dir()), reverse=True):
            try:
                directory.rmdir()
            except OSError:
                pass
        return self.package(name)

    def create(self, name: str, documents: dict[str, str]) -> SkillPackage:
        folder = self._skill_dir(name)
        if folder.exists():
            raise ConflictError(f"skill already exists: {name}")
        try:
            return self.write_package(name, documents)
        except Exception:
            if folder.exists():
                shutil.rmtree(folder)
            raise

    def delete(self, name: str) -> None:
        folder = self._skill_dir(name)
        if not folder.is_dir():
            raise NotFoundError(f"skill not found: {name}")
        shutil.rmtree(folder)


__all__ = ["SkillCatalog", "SkillFile", "SkillInfo", "SkillPackage"]
