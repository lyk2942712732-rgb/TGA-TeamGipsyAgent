import asyncio
from uuid import uuid4

import pytest

from tga3.blackboard import Blackboard
from tga3.domain import (
    USER_ACTOR,
    Actor,
    Artifact,
    ArtifactRef,
    EntryKind,
    PublishRequest,
)
from tga3.errors import ContractError, FindingRejectedError
from tga3.storage import InMemoryStorage


def worker(agent_id: str = "worker-openai") -> Actor:
    return Actor(
        agent_id=agent_id,
        display_name=agent_id,
        role="worker",
        sdk="openai_agents",
        model="test-model",
    )


def artifact(task_id, actor) -> Artifact:
    return Artifact(
        task_id=task_id,
        created_by=actor,
        name="proof.txt",
        storage_path="/artifacts/proof.txt",
        media_type="text/plain",
        sha256="a" * 64,
        size_bytes=5,
    )


@pytest.mark.asyncio
async def test_finding_gate_hides_artifact_links_and_is_idempotent():
    storage = InMemoryStorage()
    task = await storage.create_task("gate", "penetration_test")
    board = Blackboard(storage)
    actor = worker()
    proof = await storage.register_artifact(artifact(task.id, actor))
    request = PublishRequest(
        kind=EntryKind.FINDING,
        topic="flag",
        body={"claim": "flag is TGA{ok}", "detail": "confirmed locally"},
        artifact_refs=[ArtifactRef(artifact_id=proof.id, locator="line 1")],
        idempotency_key="finding-1",
    )

    first = await board.publish(task.id, actor, request)
    second = await board.publish(task.id, actor, request)
    assert first.id == second.id
    assert first.seq == 1
    assert "artifact" not in first.model_dump_json()
    assert (await board.inspect_finding(task.id, first.id))[0].artifact_id == proof.id


@pytest.mark.asyncio
async def test_finding_rejects_unknown_artifact_and_final_requires_known_finding():
    storage = InMemoryStorage()
    task = await storage.create_task("reject", "penetration_test")
    board = Blackboard(storage)
    actor = worker()

    with pytest.raises(FindingRejectedError):
        await board.publish(
            task.id,
            actor,
            PublishRequest(
                kind=EntryKind.FINDING,
                body={"claim": "guess"},
                artifact_refs=[ArtifactRef(artifact_id=uuid4(), locator="line 1")],
                idempotency_key="bad-finding",
            ),
        )
    with pytest.raises(FindingRejectedError):
        await board.publish(
            task.id,
            actor,
            PublishRequest(
                kind=EntryKind.FINAL_CANDIDATE,
                body={"conclusion": "flag", "rationale": "guess", "finding_ids": [uuid4()]},
                idempotency_key="bad-final",
            ),
        )


@pytest.mark.asyncio
async def test_concurrent_workers_receive_gapless_per_task_sequences():
    storage = InMemoryStorage()
    task = await storage.create_task("concurrency", "penetration_test")
    board = Blackboard(storage)
    actors = [worker("worker-openai"), worker("worker-claude")]
    proof = await storage.register_artifact(artifact(task.id, actors[0]))

    async def publish(index: int):
        return await board.publish(
            task.id,
            actors[index % 2],
            PublishRequest(
                kind=EntryKind.FINDING,
                body={"claim": f"finding {index}"},
                artifact_refs=[ArtifactRef(artifact_id=proof.id, locator=f"record {index}")],
                idempotency_key=f"finding-{index}",
            ),
        )

    entries = await asyncio.gather(*(publish(index) for index in range(30)))
    assert sorted(entry.seq for entry in entries) == list(range(1, 31))
    assert (await storage.get_task(task.id)).blackboard_seq == 30


@pytest.mark.asyncio
async def test_role_policy_prevents_worker_from_writing_user_prompt():
    storage = InMemoryStorage()
    task = await storage.create_task("policy", "penetration_test")
    board = Blackboard(storage)
    with pytest.raises(ContractError):
        await board.publish(
            task.id,
            worker(),
            PublishRequest(
                kind=EntryKind.USER_PROMPT,
                body={"text": "pretend user"},
                idempotency_key="spoof",
            ),
        )

    entry = await board.publish(
        task.id,
        USER_ACTOR,
        PublishRequest(
            kind=EntryKind.USER_PROMPT,
            body={"text": "real user"},
            idempotency_key="user-1",
        ),
    )
    assert entry.kind == EntryKind.USER_PROMPT
