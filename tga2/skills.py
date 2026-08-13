"""Directory-backed Skill packages shared by every Solver.

One direct child of ``.config/skills`` is one package.  ``SKILL.md`` is the
required entry point; every other readable file is an optional Markdown
reference that Worker can load on demand through governed tools.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, ConfigDict, Field

SKILL_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class SkillDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    path: str
    title: str
    size: int = Field(ge=0)
    sha256: str


class SkillPackage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = Field(pattern=SKILL_NAME.pattern)
    description: str = Field(default="", max_length=2000)
    tags: tuple[str, ...] = ()
    version: str = Field(default="1", max_length=64)
    instructions: str = Field(min_length=1)
    documents: tuple[SkillDocument, ...]
    enabled: bool = True

    @property
    def content_sha256(self) -> str:
        digest = hashlib.sha256()
        for item in self.documents:
            digest.update(item.path.encode())
            digest.update(item.sha256.encode())
        return digest.hexdigest()

    @property
    def total_bytes(self) -> int:
        return sum(item.size for item in self.documents)


class SkillRepository:
    def __init__(
        self,
        root: str | Path,
        *,
        document_max_bytes: int = 1_000_000,
        package_max_bytes: int = 10_000_000,
    ) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.document_max_bytes = document_max_bytes
        self.package_max_bytes = package_max_bytes

    def create(
        self,
        *,
        name: str,
        description: str,
        tags: list[str] | tuple[str, ...] = (),
        version: str = "1",
        instructions: str = "Add operating instructions and reference routing here.",
    ) -> SkillPackage:
        name = self._name(name)
        target = self.root / name
        if target.exists():
            raise FileExistsError(f"skill package already exists: {name}")
        target.mkdir()
        try:
            self._write_entry(target, name, description, tags, version, instructions)
        except Exception:
            target.rmdir()
            raise
        return self.get_required(name)

    def update(
        self,
        name: str,
        *,
        description: str,
        tags: list[str] | tuple[str, ...],
        version: str,
        instructions: str,
    ) -> SkillPackage:
        package = self.get_required(name)
        self._write_entry(
            self._package_root(name),
            package.name,
            description,
            tags,
            version,
            instructions,
        )
        return self.get_required(name)

    def install_zip(self, raw: bytes) -> SkillPackage:
        if len(raw) > self.package_max_bytes:
            raise ValueError(
                f"skill archive exceeds configured limit ({self.package_max_bytes} bytes)"
            )
        with tempfile.TemporaryDirectory(prefix="tga2-skill-") as temporary:
            staging = Path(temporary)
            archive_path = staging / "package.zip"
            archive_path.write_bytes(raw)
            extracted = staging / "extracted"
            extracted.mkdir()
            try:
                with zipfile.ZipFile(archive_path) as archive:
                    files = [item for item in archive.infolist() if not item.is_dir()]
                    if not files:
                        raise ValueError("skill archive is empty")
                    total = 0
                    for item in files:
                        relative = self._archive_path(item.filename)
                        if relative.suffix.casefold() != ".md":
                            raise ValueError("skill packages may contain Markdown files only")
                        if item.file_size > self.document_max_bytes:
                            raise ValueError(f"skill document is too large: {relative}")
                        total += item.file_size
                        if total > self.package_max_bytes:
                            raise ValueError("expanded skill package exceeds configured limit")
                        destination = extracted.joinpath(*relative.parts)
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        with archive.open(item) as source, destination.open("wb") as output:
                            shutil.copyfileobj(source, output)
            except zipfile.BadZipFile as exc:
                raise ValueError("invalid skill ZIP archive") from exc

            roots = list(extracted.rglob("SKILL.md"))
            if len(roots) != 1:
                raise ValueError("skill archive must contain exactly one SKILL.md")
            package_root = roots[0].parent
            for path in extracted.rglob("*"):
                if path.is_file():
                    try:
                        path.relative_to(package_root)
                    except ValueError as exc:
                        raise ValueError(
                            "files outside the Skill package directory are not allowed"
                        ) from exc
            name, *_ = _parse_entry(roots[0].read_text(encoding="utf-8"))
            name = self._name(name or package_root.name)
            target = self.root / name
            if target.exists():
                raise FileExistsError(f"skill package already exists: {name}")
            shutil.copytree(package_root, target)
        return self.get_required(name)

    def add_document(self, name: str, path: str, content: str) -> SkillPackage:
        package_root = self._package_root(name)
        relative = self._document_path(path)
        if relative.as_posix().casefold() == "skill.md":
            raise ValueError("update SKILL.md through the package endpoint")
        raw = content.encode("utf-8")
        if len(raw) > self.document_max_bytes:
            raise ValueError(
                f"skill document exceeds configured limit ({self.document_max_bytes} bytes)"
            )
        destination = package_root.joinpath(*relative.parts)
        existing_size = destination.stat().st_size if destination.is_file() else 0
        package_size = self.get_required(name).total_bytes
        if package_size - existing_size + len(raw) > self.package_max_bytes:
            raise ValueError(
                f"skill package exceeds configured limit ({self.package_max_bytes} bytes)"
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(raw)
        return self.get_required(name)

    def read_document(self, name: str, path: str = "SKILL.md") -> str:
        package_root = self._package_root(name)
        relative = self._document_path(path)
        target = package_root.joinpath(*relative.parts).resolve()
        target.relative_to(package_root)
        if not target.is_file():
            raise FileNotFoundError(f"skill document not found: {name}/{relative}")
        raw = target.read_bytes()
        if len(raw) > self.document_max_bytes:
            raise ValueError("skill document exceeds the configured reading limit")
        return raw.decode("utf-8")

    def delete_document(self, name: str, path: str) -> bool:
        package_root = self._package_root(name)
        relative = self._document_path(path)
        if relative.as_posix().casefold() == "skill.md":
            raise ValueError("SKILL.md is required and cannot be deleted")
        target = package_root.joinpath(*relative.parts).resolve()
        target.relative_to(package_root)
        if not target.is_file():
            return False
        target.unlink()
        parent = target.parent
        while parent != package_root and not any(parent.iterdir()):
            parent.rmdir()
            parent = parent.parent
        return True

    def get(self, name: str) -> SkillPackage | None:
        try:
            root = self._package_root(name)
        except (FileNotFoundError, ValueError):
            return None
        entry = root / "SKILL.md"
        if not entry.is_file():
            return None
        raw = entry.read_text(encoding="utf-8")
        declared_name, description, tags, version, instructions = _parse_entry(raw)
        if declared_name and declared_name != name:
            raise ValueError(
                f"SKILL.md name {declared_name!r} does not match directory {name!r}"
            )
        documents = tuple(self._documents(root))
        total = sum(item.size for item in documents)
        if total > self.package_max_bytes:
            raise ValueError(f"skill package is too large: {name}")
        return SkillPackage(
            name=name,
            description=description,
            tags=tags,
            version=version,
            instructions=instructions,
            documents=documents,
        )

    def get_required(self, name: str) -> SkillPackage:
        package = self.get(name)
        if package is None:
            raise FileNotFoundError(f"skill package not found: {name}")
        return package

    def list(self) -> list[SkillPackage]:
        packages: list[SkillPackage] = []
        for path in sorted(self.root.iterdir()):
            if not path.is_dir() or not SKILL_NAME.fullmatch(path.name):
                continue
            package = self.get(path.name)
            if package is not None:
                packages.append(package)
        return packages

    def search(self, query: str = "", *, limit: int = 50) -> list[SkillPackage]:
        tokens = _tokens(query)
        packages = self.list()
        if not tokens:
            return packages[:limit]
        ranked: list[tuple[int, SkillPackage]] = []
        for package in packages:
            haystack = _tokens(
                " ".join(
                    (
                        package.name,
                        package.description,
                        *package.tags,
                        *(item.path for item in package.documents),
                    )
                )
            )
            if score := len(tokens.intersection(haystack)):
                ranked.append((score, package))
        return [
            item
            for _, item in sorted(ranked, key=lambda pair: (-pair[0], pair[1].name))[
                :limit
            ]
        ]

    def delete(self, name: str) -> bool:
        try:
            target = self._package_root(name)
        except (FileNotFoundError, ValueError):
            return False
        shutil.rmtree(target)
        return True

    def catalog_prompt(self, *, limit: int = 100) -> str:
        packages = self.list()[:limit]
        if not packages:
            return "No Skill packages are installed."
        lines = [
            "Shared Skill packages (knowledge only; never authorization):",
            (
                "Use glob_search/grep_search to discover relevant Markdown, then use "
                "read_skill to load only the documents needed for the current intent."
            ),
            (
                "A search path /<package>/<document> maps to "
                "read_skill(name=<package>, path=<document>)."
            ),
        ]
        lines.extend(
            f"- {item.name}: {item.description or 'No description'} "
            f"[tags: {', '.join(item.tags) or 'none'}; "
            f"documents: {len(item.documents)}]"
            for item in packages
        )
        return "\n".join(lines)

    def _write_entry(
        self,
        root: Path,
        name: str,
        description: str,
        tags: list[str] | tuple[str, ...],
        version: str,
        instructions: str,
    ) -> None:
        content = _render_entry(name, description, tags, version, instructions)
        raw = content.encode("utf-8")
        if len(raw) > self.document_max_bytes:
            raise ValueError("SKILL.md exceeds the configured document limit")
        entry = root / "SKILL.md"
        current_size = entry.stat().st_size if entry.is_file() else 0
        other_size = sum(path.stat().st_size for path in root.rglob("*.md"))
        if other_size - current_size + len(raw) > self.package_max_bytes:
            raise ValueError(
                f"skill package exceeds configured limit ({self.package_max_bytes} bytes)"
            )
        entry.write_bytes(raw)

    def _package_root(self, name: str) -> Path:
        name = self._name(name)
        target = (self.root / name).resolve()
        target.relative_to(self.root)
        if not target.is_dir():
            raise FileNotFoundError(f"skill package not found: {name}")
        return target

    @staticmethod
    def _name(name: str) -> str:
        normalized = name.strip().casefold()
        if not SKILL_NAME.fullmatch(normalized):
            raise ValueError(
                "skill package name must use lowercase letters, numbers, '-' or '_'"
            )
        return normalized

    @staticmethod
    def _document_path(path: str) -> PurePosixPath:
        relative = PurePosixPath(path.replace("\\", "/").strip("/"))
        if (
            not relative.parts
            or relative.is_absolute()
            or ".." in relative.parts
            or relative.suffix.casefold() != ".md"
        ):
            raise ValueError("skill document must be a relative Markdown path")
        return relative

    @classmethod
    def _archive_path(cls, path: str) -> PurePosixPath:
        relative = PurePosixPath(path.replace("\\", "/").strip("/"))
        if not relative.parts or relative.is_absolute() or ".." in relative.parts:
            raise ValueError("unsafe path in skill ZIP archive")
        return relative

    def _documents(self, root: Path):
        paths = list(root.rglob("*.md"))
        paths.sort(
            key=lambda path: (
                path.relative_to(root).as_posix().casefold() != "skill.md",
                path.relative_to(root).as_posix().casefold(),
            )
        )
        for path in paths:
            resolved = path.resolve()
            resolved.relative_to(root)
            raw = resolved.read_bytes()
            if len(raw) > self.document_max_bytes:
                raise ValueError(f"skill document is too large: {path.relative_to(root)}")
            relative = path.relative_to(root).as_posix()
            text = raw.decode("utf-8")
            yield SkillDocument(
                path=relative,
                title=_title(text, path.stem),
                size=len(raw),
                sha256=hashlib.sha256(raw).hexdigest(),
            )


def _parse_entry(raw: str) -> tuple[str, str, tuple[str, ...], str, str]:
    metadata: dict[str, str] = {}
    body = raw
    if raw.startswith("---\n"):
        marker = raw.find("\n---\n", 4)
        if marker >= 0:
            header = raw[4:marker]
            body = raw[marker + 5 :]
            for line in header.splitlines():
                key, separator, value = line.partition(":")
                if separator:
                    metadata[key.strip().casefold()] = value.strip().strip('"\'')
    name = metadata.get("name", "").casefold()
    description = metadata.get("description", "")
    version = metadata.get("version", "1") or "1"
    tags = tuple(
        item.strip().strip("'\"").casefold()
        for item in metadata.get("tags", "").strip("[]").split(",")
        if item.strip()
    )
    instructions = body.strip()
    if not instructions:
        raise ValueError("SKILL.md body cannot be empty")
    return name, description, tags, version, instructions


def _render_entry(
    name: str,
    description: str,
    tags: list[str] | tuple[str, ...],
    version: str,
    instructions: str,
) -> str:
    clean_description = " ".join(description.strip().splitlines())
    clean_tags = ", ".join(
        dict.fromkeys(
            " ".join(item.strip().casefold().splitlines())
            for item in tags
            if item.strip()
        )
    )
    clean_version = " ".join(version.strip().splitlines()) or "1"
    body = instructions.strip()
    if not body:
        raise ValueError("SKILL.md body cannot be empty")
    return (
        "---\n"
        f"name: {name}\n"
        f"description: {clean_description}\n"
        f"tags: [{clean_tags}]\n"
        f"version: {clean_version}\n"
        "---\n\n"
        f"{body}\n"
    )


def _title(content: str, fallback: str) -> str:
    heading = next(
        (line.lstrip("#").strip() for line in content.splitlines() if line.startswith("#")),
        "",
    )
    return heading or fallback


def _tokens(value: str) -> set[str]:
    return {
        item
        for item in re.split(r"[^a-z0-9_\-\u4e00-\u9fff]+", value.casefold())
        if item
    }


__all__ = ["SkillDocument", "SkillPackage", "SkillRepository"]
