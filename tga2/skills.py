"""Small filesystem-backed Skill catalog used by LangChain middleware."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Skill(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    description: str = Field(default="", max_length=1000)
    tags: tuple[str, ...] = ()
    content: str = Field(min_length=1, max_length=50_000)
    enabled: bool = True
    source: Literal["builtin", "custom"] = "custom"


BUILTIN_SKILLS = (
    Skill(
        name="code-audit",
        description="Evidence-led review of authorized source code.",
        tags=("code", "audit", "source", "vulnerability"),
        content=(
            "Locate entry points and trace data flow before reporting issues. "
            "Treat scanner hits as leads; confirm source-to-sink evidence and cite artifacts."
        ),
        source="builtin",
    ),
    Skill(
        name="binary-triage",
        description="Static triage for authorized binaries and forensic samples.",
        tags=("binary", "reverse", "strings", "metadata"),
        content=(
            "Start with metadata, strings, and file structure. Preserve generated output as "
            "artifacts and state exactly what was checked before changing hypotheses."
        ),
        source="builtin",
    ),
    Skill(
        name="crypto-encoding",
        description="Bounded testing of encoding and cryptographic hypotheses.",
        tags=("crypto", "encoding", "decoding", "ctf"),
        content=(
            "Preserve the original value, test small reversible transformations, and retain "
            "parameters and output. A readable string is only a lead until independently verified."
        ),
        source="builtin",
    ),
    Skill(
        name="incident-response",
        description="Read-only, evidence-led incident investigation.",
        tags=("incident", "forensics", "timeline", "ioc"),
        content=(
            "Preserve source evidence and hashes. Distinguish observations from hypotheses, build "
            "a bounded timeline, and document unavailable evidence and recovery limitations."
        ),
        source="builtin",
    ),
    Skill(
        name="web-recon",
        description="Authorized passive mapping of a web target.",
        tags=("web", "recon", "links", "forms", "api"),
        content=(
            "Record observed routes, forms, scripts, and API hints before active testing. Stop when "
            "reachable entry points are covered and turn observations into bounded hypotheses."
        ),
        source="builtin",
    ),
    Skill(
        name="web-vulnerability-triage",
        description="Minimal evidence-backed tests of web security hypotheses.",
        tags=("web", "sqli", "idor", "upload", "auth"),
        content=(
            "Test only observed inputs with the smallest policy-approved request. Preserve baseline "
            "and test output; after repeated equivalent failures, record the boundary and stop."
        ),
        source="builtin",
    ),
)


class SkillRepository:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, skill: Skill) -> None:
        if any(item.name == skill.name for item in BUILTIN_SKILLS):
            raise ValueError("builtin skill names are reserved")
        path = self.root / f"{skill.name}.json"
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(skill.model_dump_json(indent=2), encoding="utf-8")
        temporary.replace(path)

    def get(self, name: str) -> Skill | None:
        builtin = next((item for item in BUILTIN_SKILLS if item.name == name), None)
        if builtin is not None:
            return builtin
        path = self.root / f"{name}.json"
        return (
            Skill.model_validate_json(path.read_text(encoding="utf-8"))
            if path.is_file()
            else None
        )

    def list(self) -> list[Skill]:
        custom = [
            Skill.model_validate_json(path.read_text(encoding="utf-8"))
            for path in sorted(self.root.glob("*.json"))
        ]
        return [*BUILTIN_SKILLS, *custom]

    def select(
        self, objective: str, *, selected_names: list[str] | None = None, limit: int = 3
    ) -> list[Skill]:
        enabled = [item for item in self.list() if item.enabled]
        if selected_names is not None:
            wanted = set(selected_names)
            return [item for item in enabled if item.name in wanted][:limit]
        words = {word.casefold().strip(".,:;()[]{}") for word in objective.split()}
        scored = []
        for skill in enabled:
            haystack = {skill.name.casefold(), *(tag.casefold() for tag in skill.tags)}
            if score := len(words.intersection(haystack)):
                scored.append((score, skill))
        return [
            item
            for _, item in sorted(scored, key=lambda pair: (-pair[0], pair[1].name))[
                :limit
            ]
        ]

    def delete(self, name: str) -> bool:
        if any(item.name == name for item in BUILTIN_SKILLS):
            return False
        path = self.root / f"{name}.json"
        if not path.is_file():
            return False
        path.unlink()
        return True


__all__ = ["BUILTIN_SKILLS", "Skill", "SkillRepository"]
