
import pytest
from pydantic import ValidationError

from tga3.config import AgentConfig
from tga3.domain import SYSTEM_ACTOR, Actor, EventKind
from tga3.events import TaskEvents
from tga3.storage import InMemoryStorage


def test_removed_agent_and_chat_contracts_are_rejected():
    with pytest.raises(ValidationError):
        Actor(agent_id="supervisor", display_name="Supervisor", role="supervisor")
    with pytest.raises(ValidationError):
        AgentConfig(display_name="Supervisor", role="supervisor", runtime="openai_agents", system_prompt="old")
    for removed in ("question", "user_message"):
        with pytest.raises(ValueError):
            EventKind(removed)


async def test_task_events_persist_with_incremental_sequence():
    storage = InMemoryStorage()
    task = await storage.create_task("activity", "pwn")
    events = TaskEvents(storage)
    for text in ("first", "second"):
        await events.emit(task.id, actor=SYSTEM_ACTOR, kind=EventKind.SYSTEM, text=text)
    history = await events.history(task.id, after_seq=1, limit=1)
    assert [(item.seq, item.text) for item in history] == [(2, "second")]
    assert (await storage.get_task(task.id)).event_seq == 2
