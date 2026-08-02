"""Stable public protocol metadata for the Runtime HTTP API.

The domain entities live in :mod:`tga.contracts`; this module owns the
transport-level version and cursor contract shared by API, CLI, and Web.

``RUNTIME_API_VERSION`` is the transport contract version behind ``/api/v2``.
It is deliberately named apart from the schema-v6 domain model version and the
database schema version so the three cannot be conflated.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


RUNTIME_API_VERSION = 2


class EventCursor(BaseModel):
    api_version: int = RUNTIME_API_VERSION
    after_seq: int = Field(default=0, ge=0)
    limit: int = Field(default=200, ge=1, le=1000)


class RuntimeCommandResult(BaseModel):
    api_version: int = RUNTIME_API_VERSION
    task_id: str
    accepted: bool
    status: str
    reason: str = ""
