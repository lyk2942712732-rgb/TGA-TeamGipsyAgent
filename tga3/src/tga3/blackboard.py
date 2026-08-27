"""The only shared-memory interface exposed to agents."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from uuid import UUID

from .domain import (
    Actor,
    Artifact,
    ArtifactRef,
    BlackboardEntry,
    BlackboardSyncResult,
    EntryKind,
    PublishRequest,
)
from .errors import ContractError
from .storage import Storage

ChangeHook = Callable[[UUID, int], Awaitable[None]]

_WRITE_POLICY: dict[str, frozenset[EntryKind]] = {
    "user": frozenset({EntryKind.USER_PROMPT, EntryKind.USER_FILE}),
    "supervisor": frozenset({EntryKind.SUPERVISOR_ADVICE}),
    "worker": frozenset({EntryKind.INTEL, EntryKind.FINDING, EntryKind.FINAL_CANDIDATE}),
    "reporter": frozenset(),
    "system": frozenset({EntryKind.USER_PROMPT, EntryKind.USER_FILE}),
}


class Blackboard:
    def __init__(self, storage: Storage, on_change: ChangeHook | None = None) -> None:
        self.storage = storage
        self.on_change = on_change

    async def publish(self, task_id: UUID, actor: Actor, request: PublishRequest) -> BlackboardEntry:
        if request.kind not in _WRITE_POLICY[actor.role]:
            raise ContractError(f"{actor.role} cannot publish {request.kind.value}")
        entry = await self.storage.publish(task_id, actor, request)
        if self.on_change:
            await self.on_change(task_id, entry.seq)
        return entry

    async def sync(
        self,
        task_id: UUID,
        *,
        after_seq: int = 0,
        up_to_seq: int | None = None,
        limit: int = 500,
    ) -> BlackboardSyncResult:
        task = await self.storage.get_task(task_id)
        entries = await self.storage.list_entries(task_id, after_seq=after_seq, up_to_seq=up_to_seq, limit=limit)
        return BlackboardSyncResult(latest_seq=task.blackboard_seq, entries=tuple(entries))

    async def publish_and_sync(
        self,
        task_id: UUID,
        actor: Actor,
        request: PublishRequest,
        *,
        after_seq: int,
        limit: int = 500,
    ) -> BlackboardSyncResult:
        published = await self.publish(task_id, actor, request)
        entries = await self.storage.list_entries(task_id, after_seq=after_seq, limit=limit)
        return BlackboardSyncResult(published=published, latest_seq=published.seq, entries=tuple(entries))

    async def register_artifact(self, artifact: Artifact) -> Artifact:
        return await self.storage.register_artifact(artifact)

    async def inspect_finding(self, task_id: UUID, finding_id: UUID) -> Sequence[ArtifactRef]:
        """Privileged/on-demand lookup; links never appear in normal blackboard sync."""
        return await self.storage.finding_links(task_id, finding_id)


__all__ = ["Blackboard"]
