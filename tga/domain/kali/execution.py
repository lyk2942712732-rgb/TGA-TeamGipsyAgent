"""Model-facing Kali execution and PTY session contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class NetworkTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    host: str = Field(min_length=1, max_length=253)
    ports: tuple[int, ...] = Field(default_factory=tuple, max_length=128)


class KaliExecArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    executable: str = Field(min_length=1, max_length=256)
    argv: tuple[str, ...] = Field(default_factory=tuple, max_length=256)
    cwd: str = Field(default="scratch", min_length=1, max_length=512)
    env: dict[str, str] = Field(default_factory=dict)
    stdin: str | None = Field(default=None, max_length=262_144)
    timeout_seconds: int | None = Field(default=None, ge=1, le=7_200)
    network_targets: tuple[NetworkTarget, ...] = Field(default_factory=tuple, max_length=64)


class KaliSessionArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation: Literal["open", "write", "read", "resize", "close"]
    session_id: str | None = None
    executable: str | None = None
    argv: tuple[str, ...] = Field(default_factory=tuple, max_length=256)
    cwd: str = Field(default="scratch", min_length=1, max_length=512)
    input: str | None = Field(default=None, max_length=262_144)
    cols: int | None = Field(default=None, ge=20, le=1_000)
    rows: int | None = Field(default=None, ge=5, le=1_000)
    wait_ms: int = Field(default=200, ge=0, le=30_000)
    max_output_chars: int = Field(default=32_768, ge=1, le=262_144)

    @model_validator(mode="after")
    def operation_fields(self) -> "KaliSessionArguments":
        if self.operation == "open" and not self.executable:
            raise ValueError("kali.session open requires executable")
        if self.operation != "open" and not self.session_id:
            raise ValueError(f"kali.session {self.operation} requires session_id")
        if self.operation == "write" and self.input is None:
            raise ValueError("kali.session write requires input")
        if self.operation == "resize" and (self.cols is None or self.rows is None):
            raise ValueError("kali.session resize requires cols and rows")
        if self.operation != "open" and self.executable is not None:
            raise ValueError(f"kali.session {self.operation} does not accept executable")
        if self.operation != "open" and self.argv:
            raise ValueError(f"kali.session {self.operation} does not accept argv")
        if self.operation != "open" and self.cwd != "scratch":
            raise ValueError(f"kali.session {self.operation} does not accept cwd")
        if self.operation != "write" and self.input is not None:
            raise ValueError(f"kali.session {self.operation} does not accept input")
        if self.operation != "resize" and (self.cols is not None or self.rows is not None):
            raise ValueError(f"kali.session {self.operation} does not accept terminal size")
        return self


__all__ = ["KaliExecArguments", "KaliSessionArguments", "NetworkTarget"]
