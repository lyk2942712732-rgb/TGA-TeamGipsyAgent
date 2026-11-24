"""Runtime-agnostic CLI adapter factory and shared container role dispatcher."""

from __future__ import annotations

import asyncio
import os

from .cli_adapter import ClaudeAdapter, CliAdapter, CodexAdapter
from .extra_cli import OpenCodeAdapter, QwenAdapter
from .runtime import WorkerSession

_ADAPTERS: dict[str, type[CliAdapter]] = {
    "codex_cli": CodexAdapter,
    "claude_cli": ClaudeAdapter,
    "opencode_cli": OpenCodeAdapter,
    "qwen_cli": QwenAdapter,
}


def create_adapter(runtime: str | None = None) -> CliAdapter:
    selected = (runtime or os.environ.get("TGA3_AGENT_RUNTIME") or "codex_cli").strip()
    adapter_cls = _ADAPTERS.get(selected)
    if adapter_cls is None:
        raise ValueError(f"Unsupported CLI runtime: {selected}")
    return adapter_cls()


def run_role_entrypoint(default_runtime: str) -> None:
    runtime = os.environ.get("TGA3_AGENT_RUNTIME") or default_runtime
    role = os.environ.get("TGA3_AGENT_ROLE")
    if role == "reporter":
        from .openai_worker import report

        asyncio.run(report(create_adapter(runtime)))
    elif role == "scout":
        from .openai_worker import scout

        asyncio.run(scout(create_adapter(runtime)))
    else:
        asyncio.run(WorkerSession(create_adapter(runtime)).run())
