"""Traceable, safety-labelled document chunks."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tga.domain.retrieval.corpus import OwnerScope, RetrievalChannel, TrustLevel


ChunkLocatorKind = Literal[
    "text_range", "line_range", "symbol", "heading", "time_window",
    "json_path", "http_part", "page", "binary_extraction",
]


class ChunkLocator(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: ChunkLocatorKind
    char_start: int | None = Field(default=None, ge=0)
    char_end: int | None = Field(default=None, ge=0)
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)
    symbol: str | None = Field(default=None, max_length=1_000)
    heading_path: tuple[str, ...] = ()
    json_path: str | None = Field(default=None, max_length=2_000)
    http_part: str | None = Field(default=None, max_length=255)
    page: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)
    time_start: str | None = None
    time_end: str | None = None
    source_ref: str | None = Field(default=None, max_length=4_096)

    @model_validator(mode="after")
    def validate_locator(self) -> "ChunkLocator":
        if self.kind == "text_range" and (
            self.char_start is None or self.char_end is None
            or self.char_end <= self.char_start
        ):
            raise ValueError("text_range requires increasing character coordinates")
        if self.kind == "line_range" and (
            self.line_start is None or self.line_end is None
            or self.line_end < self.line_start
        ):
            raise ValueError("line_range requires valid line coordinates")
        if self.kind == "symbol" and not self.symbol:
            raise ValueError("symbol locator requires symbol")
        if self.kind == "heading" and not self.heading_path:
            raise ValueError("heading locator requires heading_path")
        if self.kind == "json_path" and not self.json_path:
            raise ValueError("json_path locator requires json_path")
        if self.kind == "http_part" and not self.http_part:
            raise ValueError("http_part locator requires http_part")
        if self.kind == "page" and self.page is None:
            raise ValueError("page locator requires page")
        return self


class DocumentChunk(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=255)
    knowledge_base_id: str
    source_id: str
    document_id: str
    revision_id: str
    channel: RetrievalChannel
    owner: OwnerScope
    trust_level: TrustLevel
    content: str = Field(min_length=1, max_length=200_000)
    content_sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    token_count: int = Field(ge=1)
    locator: ChunkLocator
    safety_flags: tuple[str, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str


__all__ = ["ChunkLocator", "ChunkLocatorKind", "DocumentChunk"]
