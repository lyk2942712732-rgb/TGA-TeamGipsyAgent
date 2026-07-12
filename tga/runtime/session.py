"""Durable, non-secret session checkpoints."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tga.contracts import SessionRecord
from tga.evidence.store import EvidenceStore


class AgentSession:
    def __init__(self, *, store: EvidenceStore, run_root: str | Path, task_id: str):
        self.store = store
        self.task_id = task_id
        self.root = Path(run_root) / task_id
        self.path = self.root / "session" / "checkpoint.json"
        self.board_path = self.root / "board" / "snapshot.json"

    def ensure(self, *, max_turns: int) -> SessionRecord:
        for directory in ("session", "board", "solvers", "artifacts", "reports"):
            (self.root / directory).mkdir(parents=True, exist_ok=True)
        session = self.store.get_session(self.task_id)
        if session is None:
            session = self.store.create_session(SessionRecord(task_id=self.task_id, max_turns=max_turns))
        elif session.max_turns > max_turns:
            # Environment limits are an upper bound; a resumed session must
            # never retain a higher frontend-era value.
            session = self.store.update_session(self.task_id, max_turns=max_turns)
        self.checkpoint()
        return session

    def checkpoint(self) -> None:
        session = self.store.get_session(self.task_id)
        if session is None:
            return
        board = {
            "hypotheses": [item.model_dump(mode="json") for item in self.store.list_hypotheses(self.task_id)],
            "memory": [item.model_dump(mode="json") for item in self.store.list_memory(self.task_id)],
        }
        snapshot: dict[str, Any] = {
            "task_id": self.task_id,
            "session": session.model_dump(mode="json"),
            "solver_ids": [item.id for item in self.store.list_solvers(self.task_id)],
            "last_seq": self.store.latest_agent_event_seq(self.task_id),
            # Deliberately contains summaries and artifact IDs only: raw HTTP
            # and tool payloads remain immutable ArtifactStore records.
            "context": board,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
        self.board_path.parent.mkdir(parents=True, exist_ok=True)
        self.board_path.write_text(json.dumps(board, ensure_ascii=False, indent=2), encoding="utf-8")
