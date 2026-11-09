"""One full-text overview per AOE batch; question boards contain only question-local context."""

from __future__ import annotations

from pathlib import Path


class BatchContext:
    def __init__(self, config, storage):
        self.config, self.storage = config, storage
        self._prepared = {}

    async def prepare(self, parent_id) -> Path:
        if parent_id in self._prepared:
            return self._prepared[parent_id]
        root = self.config.task_path(parent_id) / "overview"
        entries = await self.storage.list_entries(parent_id, limit=10_000)
        scene = next(
            (e.body["text"] for e in entries if e.kind == "user_prompt" and e.topic == "scene"),
            self.config.scene("aoe").system_prompt,
        )
        task = "\n\n".join(
            e.body["text"] for e in entries if e.kind == "user_prompt" and e.actor.role == "user" and e.topic == "task"
        )
        root.mkdir(parents=True, exist_ok=True)
        for name, text in [("scene.md", scene), ("task.md", task)]:
            path = root / name
            if not path.is_file() or path.read_text(encoding="utf-8") != text:
                temporary = path.with_suffix(".tmp")
                temporary.write_text(text, encoding="utf-8")
                temporary.replace(path)
        self._prepared[parent_id] = root
        return root
