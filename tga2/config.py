"""One persisted configuration source shared by API, CLI and Runtime."""

from __future__ import annotations

import json
import os
from pathlib import Path
from threading import RLock
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from tga2.integrations.model import ModelSettings

DEFAULT_PROMPTS = {
    "common": "Evidence first. Never expand task authorization.",
    "supervisor": "",
    "worker": "",
    "reviewer": "",
    "reporter": "",
}


class RuntimeSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompts: dict[str, str] = Field(default_factory=lambda: dict(DEFAULT_PROMPTS))
    mode_prompts: list[dict[str, Any]] = Field(default_factory=list)
    solver_tools: dict[str, list[str]] = Field(
        default_factory=lambda: {
            "supervisor": [],
            "worker": [
                "list_inputs",
                "read_input",
                "glob_search",
                "grep_search",
                "save_note",
                "run_command",
            ],
            "reviewer": [],
            "reporter": [],
        }
    )
    sandbox_image: str | None = None


class Configuration:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "runtime.json"
        self.model_path = self.root / "model.json"
        self._lock = RLock()
        self.runtime = self._load()
        self.model = self._load_model()

    def save(self) -> None:
        with self._lock:
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(
                self.runtime.model_dump_json(indent=2), encoding="utf-8"
            )
            temporary.replace(self.path)

    def save_model(self, settings: ModelSettings) -> None:
        with self._lock:
            secret = settings.api_key.get_secret_value() if settings.api_key else None
            payload = settings.model_dump(mode="json", exclude={"api_key"})
            payload["api_key"] = secret
            temporary = self.model_path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            temporary.replace(self.model_path)
            try:
                os.chmod(self.model_path, 0o600)
            except OSError:
                pass
            self.model = settings

    def update_prompts(self, payload: dict[str, Any]) -> dict[str, Any]:
        prompts = dict(self.runtime.prompts)
        common = payload.get("common_system_prompt")
        if common is not None:
            prompts["common"] = str(common)
        for role in ("supervisor", "worker", "reviewer", "reporter"):
            if role in payload:
                prompts[role] = str(payload[role])
        modes = payload.get("modes", self.runtime.mode_prompts)
        if not isinstance(modes, list):
            raise TypeError("modes must be a list")
        self.runtime = self.runtime.model_copy(
            update={"prompts": prompts, "mode_prompts": modes}
        )
        self.save()
        return self.prompt_payload()

    def prompt_payload(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "common_system_prompt": self.runtime.prompts.get("common", ""),
            "modes": self.runtime.mode_prompts,
            **{
                role: self.runtime.prompts.get(role, "")
                for role in ("supervisor", "worker", "reviewer", "reporter")
            },
        }

    def agent_prompts(self) -> dict[str, Any]:
        return {**self.runtime.prompts, "__modes__": self.runtime.mode_prompts}

    def update_solver_tools(self, solver_id: str, tools: list[str]) -> None:
        values = dict(self.runtime.solver_tools)
        values[solver_id] = list(dict.fromkeys(tools))
        self.runtime = self.runtime.model_copy(update={"solver_tools": values})
        self.save()

    def _load(self) -> RuntimeSettings:
        if not self.path.is_file():
            return RuntimeSettings()
        return RuntimeSettings.model_validate_json(
            self.path.read_text(encoding="utf-8")
        )

    def _load_model(self) -> ModelSettings:
        if not self.model_path.is_file():
            return ModelSettings.from_env()
        payload = json.loads(self.model_path.read_text(encoding="utf-8"))
        return ModelSettings.model_validate(payload)


__all__ = ["Configuration", "RuntimeSettings"]
