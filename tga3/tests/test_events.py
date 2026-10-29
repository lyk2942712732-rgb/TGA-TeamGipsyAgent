from pathlib import Path

from tga3.config import AgentsConfig, RuntimeConfig, TGA3Config
from tga3.domain import SYSTEM_ACTOR, USER_ACTOR, Actor, EventKind
from tga3.events import TaskEvents
from tga3.storage import InMemoryStorage


def test_legacy_configuration_discards_supervisor_and_its_scheduling_settings():
    config = TGA3Config(Path(__file__).parents[1] / "config")
    agents = config.agents.model_dump(mode="json")
    agents["agents"]["supervisor"] = {"role": "supervisor", "runtime": "openai_agents"}
    assert set(AgentsConfig.model_validate(agents).agents) == {"worker-openai", "worker-claude", "reporter"}
    runtime = config.runtime.model_dump(mode="json")
    runtime["supervisor_enabled"] = True
    runtime["cadence"]["supervisor_silence_seconds"] = 10
    migrated = RuntimeConfig.model_validate(runtime).model_dump(mode="json")
    assert "supervisor_enabled" not in migrated
    assert "supervisor_silence_seconds" not in migrated["cadence"]


async def test_read_only_events_hide_legacy_chat_and_supervisor_messages():
    storage = InMemoryStorage()
    task = await storage.create_task("history", "pwn")
    events = TaskEvents(storage)
    for actor, kind, channel in (
        (USER_ACTOR, EventKind.USER_MESSAGE, "worker-openai"),
        (Actor(agent_id="supervisor", display_name="Supervisor", role="supervisor"), EventKind.SYSTEM, "supervisor"),
        (SYSTEM_ACTOR, EventKind.QUESTION, "worker-openai"),
        (SYSTEM_ACTOR, EventKind.AGENT_STATUS, "supervisor"),
        (SYSTEM_ACTOR, EventKind.AGENT_STATUS, "worker-openai"),
    ):
        await events.emit(task.id, actor=actor, kind=kind, text="history", channel_agent_id=channel)
    history = await events.history(task.id, limit=1)
    assert [(item.kind, item.channel_agent_id) for item in history] == [(EventKind.AGENT_STATUS, "worker-openai")]
