"""Typed, extensible locators into immutable artifacts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


LocatorKind = Literal[
    "whole_artifact", "text_range", "line_range", "json_path", "page", "binary_offset",
    "legacy_whole_artifact",
]


class EvidenceLocator(BaseModel):
    model_config = {"extra": "forbid"}

    kind: LocatorKind
    char_start: int | None = Field(default=None, ge=0)
    char_end: int | None = Field(default=None, ge=0)
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)
    json_path: str | None = Field(default=None, max_length=2_000)
    page: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)
    binary_offset: int | None = Field(default=None, ge=0)
    binary_length: int | None = Field(default=None, ge=1)
    text_quote: str | None = Field(default=None, max_length=8_000)
    legacy_reason: str | None = Field(default=None, max_length=1_000)

    @model_validator(mode="after")
    def validate_coordinates(self) -> "EvidenceLocator":
        coordinate_fields = {
            "char_start": self.char_start,
            "char_end": self.char_end,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "json_path": self.json_path,
            "page": self.page,
            "page_end": self.page_end,
            "binary_offset": self.binary_offset,
            "binary_length": self.binary_length,
        }
        allowed: dict[str, set[str]] = {
            "whole_artifact": set(),
            "text_range": {"char_start", "char_end"},
            "line_range": {"line_start", "line_end"},
            "json_path": {"json_path"},
            "page": {"page", "page_end"},
            "binary_offset": {"binary_offset", "binary_length"},
            "legacy_whole_artifact": set(),
        }
        unexpected = [
            name for name, value in coordinate_fields.items()
            if value is not None and name not in allowed[self.kind]
        ]
        if unexpected:
            raise ValueError(f"{self.kind} locator has incompatible coordinates: {unexpected}")
        if self.kind == "text_range" and (
            self.char_start is None or self.char_end is None or self.char_end <= self.char_start
        ):
            raise ValueError("text_range requires char_end greater than char_start")
        if self.kind == "line_range" and (
            self.line_start is None or self.line_end is None or self.line_end < self.line_start
        ):
            raise ValueError("line_range requires line_end at or after line_start")
        if self.kind == "json_path" and not (self.json_path or "").strip():
            raise ValueError("json_path locator requires json_path")
        if self.kind == "page" and self.page is None:
            raise ValueError("page locator requires page")
        if self.kind == "page" and self.page_end is not None and self.page_end < self.page:  # type: ignore[operator]
            raise ValueError("page_end must be at or after page")
        if self.kind == "binary_offset" and (
            self.binary_offset is None or self.binary_length is None
        ):
            raise ValueError("binary_offset locator requires offset and length")
        if self.kind == "legacy_whole_artifact" and not (self.legacy_reason or "").strip():
            raise ValueError("legacy whole-artifact locator requires an explicit reason")
        return self


__all__ = ["EvidenceLocator", "LocatorKind"]
