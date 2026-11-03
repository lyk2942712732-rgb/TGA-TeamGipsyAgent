"""Bind final publications to the runtime connection and derive stable retry identity."""

import asyncio
import hashlib
import json
from collections import defaultdict
from uuid import UUID

from .blackboard import Blackboard
from .config import TGA3Config
from .domain import Actor, EntryKind, FinalCandidateBody, PublishRequest
from .errors import ContractError


class FinalCandidatePublisher:
    def __init__(self, config: TGA3Config, board: Blackboard):
        self.config, self.board = config, board
        self.locks = defaultdict(asyncio.Lock)

    async def publish(self, task_id: UUID, agent_id: str, body: FinalCandidateBody, topic: str) -> dict:
        configured = self.config.agents.agents.get(agent_id)
        if configured is None or configured.role != "worker":
            raise ContractError("Final candidates require a bound Worker identity")
        run = await self.board.storage.get_agent(task_id, agent_id)
        actor = Actor(agent_id=agent_id, display_name=configured.display_name, role="worker",
                      sdk=run.sdk, model=run.model_id)
        payload = {"body": body.model_dump(mode="json"), "topic": topic}
        key = "final-candidate:" + hashlib.sha256(json.dumps(payload, ensure_ascii=False,
                                                             sort_keys=True).encode()).hexdigest()
        async with self.locks[task_id, agent_id]:
            entry = await self.board.storage.publication_by_key(task_id, agent_id, key)
            if entry is not None:
                return entry.model_dump(mode="json")
            await self.board.check_write(task_id, actor, EntryKind.FINAL_CANDIDATE)
            task = await self.board.storage.get_task(task_id)
            run = await self.board.storage.get_agent(task_id, agent_id)
            if run.metadata.get("blackboard_cursor", -1) < task.blackboard_seq:
                raise ContractError("Unread blackboard updates: consume the native checkpoint before resubmitting")
            entry = await self.board.publish(task_id, actor, PublishRequest(
                kind="final_candidate", topic=topic, body=body.model_dump(mode="json"), idempotency_key=key,
            ))
            return entry.model_dump(mode="json")
