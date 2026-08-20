import pytest

from tga3.dialogue import SolverDialogue
from tga3.domain import Actor, EntryKind
from tga3.storage import InMemoryStorage


@pytest.mark.asyncio
async def test_only_answered_question_becomes_single_complete_qa_entry():
    storage = InMemoryStorage()
    task = await storage.create_task("q&a")
    dialogue = SolverDialogue(storage)
    supervisor = Actor(agent_id="supervisor", display_name="Supervisor", role="supervisor")
    worker = Actor(agent_id="worker-claude", display_name="Claude Worker", role="worker")

    pending = await dialogue.ask(
        task.id,
        supervisor=supervisor,
        origin=worker,
        question="目标服务的端口是什么？",
    )
    assert await storage.list_entries(task.id) == []
    question_message = (await dialogue.history(task.id))[0]
    assert "Claude Worker" in question_message.text

    entry = await dialogue.answer(
        pending.id,
        answer="端口是 31337",
        idempotency_key="answer-1",
    )
    assert entry.kind == EntryKind.QA
    assert entry.body["question"] == "目标服务的端口是什么？"
    assert entry.body["answer"] == "端口是 31337"
    assert entry.body["origin_agent_id"] == "worker-claude"
    assert len(await storage.list_entries(task.id)) == 1

    repeated = await dialogue.answer(
        pending.id,
        answer="端口是 31337",
        idempotency_key="answer-1",
    )
    assert repeated.id == entry.id
    assert len(await storage.list_entries(task.id)) == 1
