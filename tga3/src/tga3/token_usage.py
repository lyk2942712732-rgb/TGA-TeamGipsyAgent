"""Read reported usage from native CLI journals, without exposing conversation content."""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from pathlib import Path
from uuid import UUID

from .config import TGA3Config
from .domain import utc_now
from .storage import Storage

COUNTERS = ("input_tokens", "output_tokens", "cached_input_tokens", "cache_write_input_tokens", "total_tokens")


def normalize_usage(usage: dict, *, claude: bool = False) -> dict[str, int]:
    def number(key):
        value = usage.get(key, 0)
        return value if type(value) is int and value >= 0 else 0

    cached = number("cache_read_input_tokens") if claude else number("cached_input_tokens")
    written = number("cache_creation_input_tokens") if claude else number("cache_write_input_tokens")
    inputs = number("input_tokens") + (cached + written if claude else 0)
    outputs = number("output_tokens")
    return {"input_tokens": inputs, "output_tokens": outputs, "cached_input_tokens": cached,
            "cache_write_input_tokens": written, "total_tokens": inputs + outputs}


def merge_usage_sources(metadata: dict, updates: dict[str, dict]) -> dict:
    sources = dict(metadata.get("token_usage_sources", {}))
    for source, usage in updates.items():
        previous = sources.get(source, {})
        sources[source] = {key: max(previous.get(key, 0), usage.get(key, 0)) for key in COUNTERS}
    if sources == metadata.get("token_usage_sources"):
        return metadata
    total = {key: sum(item.get(key, 0) for item in sources.values()) for key in COUNTERS}
    return {**metadata, "token_usage_sources": sources,
            "token_usage": {**total, "reported": True, "updated_at": utc_now().isoformat()}}


class TokenUsageTracker:
    def __init__(self, config: TGA3Config, storage: Storage):
        self.config, self.storage = config, storage
        self.positions: dict[Path, tuple[int, int]] = {}
        self.locks = defaultdict(asyncio.Lock)

    def read_updates(self, workspace: Path) -> tuple[dict, dict]:
        updates, positions = {}, {}
        paths = list((workspace / ".codex" / "sessions").rglob("*.jsonl"))
        paths += list((workspace / ".claude" / "projects").rglob("*.jsonl"))
        for path in paths:
            if path.is_symlink() or not path.resolve().is_relative_to(workspace.resolve()):
                continue
            try:
                stat = path.stat()
                prior_inode, offset = self.positions.get(path, (stat.st_ino, 0))
                if prior_inode != stat.st_ino or stat.st_size < offset:
                    offset = 0
                with path.open("rb") as journal:
                    journal.seek(offset)
                    while line := journal.readline():
                        if not line.endswith(b"\n"):
                            break
                        offset = journal.tell()
                        try:
                            record = json.loads(line)
                        except (ValueError, UnicodeDecodeError):
                            continue
                        if not isinstance(record, dict):
                            continue
                        payload = record.get("payload") or {}
                        if not isinstance(payload, dict):
                            continue
                        usage, source = None, ""
                        if record.get("type") == "token_usage_record":
                            usage = payload.get("thread_token_usage")
                            source = f"codex:{path.name}"
                        elif record.get("type") == "event_msg" and payload.get("type") == "token_count":
                            usage = (payload.get("info") or {}).get("total_token_usage")
                            source = f"codex:{path.name}"
                        elif record.get("type") == "assistant":
                            message = record.get("message") or {}
                            usage = message.get("usage")
                            if message.get("id"):
                                source = f"claude:{message['id']}"
                        if source and isinstance(usage, dict):
                            normalized = normalize_usage(usage, claude=source.startswith("claude:"))
                            previous = updates.get(source, {})
                            updates[source] = {key: max(previous.get(key, 0), normalized[key]) for key in COUNTERS}
                positions[path] = (stat.st_ino, offset)
            except (FileNotFoundError, PermissionError):
                continue
        return updates, positions

    async def refresh_task(self, task_id: UUID) -> None:
        async with self.locks[task_id]:
            for agent in await self.storage.list_agents(task_id):
                workspace = self.config.resolve_path(self.config.runtime.workspace_root) / str(task_id) / agent.agent_id
                updates, positions = await asyncio.to_thread(self.read_updates, workspace)
                if updates:
                    await self.storage.mutate_agent_metadata(
                        task_id, agent.agent_id, lambda metadata, batch=updates: merge_usage_sources(metadata, batch),
                    )
                self.positions.update(positions)
