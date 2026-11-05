"""Publish an immediately reusable observation with server-resolved provenance."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from collections import defaultdict
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from .action_logs import read_action_log
from .blackboard import Blackboard
from .config import TGA3Config
from .domain import Actor, EntryKind, PublishRequest
from .errors import ContractError
from .evidence import register_evidence


class ActionFindingInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    action_id: str = Field(min_length=1, max_length=200)
    claim: str = Field(min_length=1, max_length=2000)
    scope: str = Field(min_length=1, max_length=2000)
    preconditions: str = Field(default="", max_length=1000)


class ActionFindingPublisher:
    def __init__(self, config: TGA3Config, board: Blackboard):
        self.config, self.board = config, board
        self.locks = defaultdict(asyncio.Lock)

    async def publish(self, task_id: UUID, agent_id: str, request: ActionFindingInput) -> dict:
        configured = self.config.agents.agents.get(agent_id)
        if configured is None or configured.role != "worker":
            raise ContractError("Finding import requires a bound Worker identity")
        run = await self.board.storage.get_agent(task_id, agent_id)
        actor = Actor(agent_id=agent_id, display_name=configured.display_name, role="worker", sdk=run.sdk,
                      model=run.model_id)
        await self.board.check_write(task_id, actor, EntryKind.FINDING)
        key = "action-finding:" + hashlib.sha256(
            json.dumps(request.model_dump(), ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()
        root = self.config.task_path(task_id, "workspaces", agent_id)
        if root.resolve() != root.absolute():
            raise ContractError("Evidence directory cannot be a symlink to another directory")
        draft_path = root / ".tga3/finding-outbox" / (key.split(":", 1)[1] + ".json")
        if not draft_path.resolve().is_relative_to(root.resolve()):
            raise ContractError("Finding draft must stay inside the Worker workspace")

        def save(value):
            draft_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = draft_path.with_suffix(f".{uuid4().hex}.tmp")
            try:
                temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
                os.replace(temporary, draft_path)
            finally:
                temporary.unlink(missing_ok=True)

        async with self.locks[task_id, agent_id]:
            draft = json.loads(draft_path.read_text(encoding="utf-8")) if draft_path.is_file() else None
            if draft and draft.get("entry"):
                # Return the original publication even if an attachment was later changed/deleted.
                entries = await self.board.storage.list_entries(task_id, after_seq=draft["entry"]["seq"] - 1, limit=1)
                if entries and entries[0].actor.agent_id == agent_id and entries[0].idempotency_key == key:
                    return {"saved": True, "entry": entries[0].model_dump(mode="json")}
            actions = read_action_log(self.config.task_path(task_id, "logs", agent_id),
                                      task_id=task_id, agent_id=agent_id)["actions"]
            action = next((row for row in actions if row["action_id"] == request.action_id), None)
            if action is None:
                raise ContractError("Unknown action_id for this Worker; use the ID supplied with the tool result")
            if action["status"] not in {"completed", "failed"}:
                raise ContractError("Action has not returned a result; wait for completion before publishing")
            method = action["action"]
            if "blackboard" in str(method.get("tool", "")).lower():
                raise ContractError("A publication/sync action is not execution evidence; "
                                    "reference the original action")
            artifact = action["artifact"]
            if not artifact.get("output_path"):
                raise ContractError("Action is missing its saved result body")
            paths = list(dict.fromkeys([artifact["output_path"], *artifact.get("attachments", [])]))
            draft = {"status": "pending", "task_id": str(task_id), "agent_id": agent_id,
                     "request": request.model_dump(), "action": action, "idempotency_key": key}
            save(draft)
            try:
                references = await register_evidence(self.board, task_id, actor, root, paths)
                command = json.dumps(method.get("input", {}), ensure_ascii=False, indent=2)
                if not method.get("input") and method.get("command"):
                    command = str(method["command"])
                if len(command) > 4000:
                    command = (command[:4000] + f"\n[参数过长，完整内容见 /logs/{agent_id}-actions.json "
                               f"的 {request.action_id}]")
                detail = f"适用条件与边界：{request.scope}\n"
                if request.preconditions:
                    detail += f"补充前置条件：{request.preconditions}\n"
                detail += (f"复现方法（{method['tool']}，工作目录 {method.get('cwd', '/workspace')}）：\n{command}\n"
                           f"执行状态：{action['status']}；退出码：{action.get('exit_code')}\n"
                           f"执行者：{agent_id}；action_id：{request.action_id}\n"
                           f"结果正文：{artifact['output_path']}")
                if artifact.get("attachments"):
                    listing = json.dumps(artifact["attachments"], ensure_ascii=False)
                    detail += "\n附件：" + (listing if len(listing) <= 500 else
                                         f"共 {len(artifact['attachments'])} 个，"
                                         "完整清单见该 action 的 artifact.attachments")
                entry = await self.board.publish(task_id, actor, PublishRequest(
                    kind="finding", body={"claim": request.claim, "detail": detail},
                    artifact_refs=references, source_action_ids=[request.action_id], idempotency_key=key,
                ))
            except Exception as exc:
                draft.update(status="failed", error_type=type(exc).__name__)
                save(draft)
                raise
            draft.update(status="saved", entry=entry.model_dump(mode="json"))
            save(draft)
            return {"saved": True, "entry": draft["entry"]}
