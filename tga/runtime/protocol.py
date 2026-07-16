"""Stable public protocol metadata for Runtime v2.

The domain entities live in :mod:`tga.contracts`; this module owns the
transport-level version and cursor contract shared by API, CLI, and Web.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


RUNTIME_SCHEMA_VERSION = 2


class EventCursor(BaseModel):
    schema_version: int = RUNTIME_SCHEMA_VERSION
    after_seq: int = Field(default=0, ge=0)
    limit: int = Field(default=200, ge=1, le=1000)


class RuntimeCommandResult(BaseModel):
    schema_version: int = RUNTIME_SCHEMA_VERSION
    task_id: str
    accepted: bool
    status: str
    reason: str = ""

