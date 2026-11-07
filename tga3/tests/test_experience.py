import json
import os
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from tga3.app import create_app
from tga3.config import TGA3Config
from tga3.docker_runtime import FakeContainerRuntime
from tga3.domain import AgentRun, RunState
from tga3.experience import CARD_FIELDS, REPORT_INSTRUCTIONS, ExperienceStore, atomic_json
from tga3.host_agents import DeterministicHostModel
from tga3.storage import InMemoryStorage
from tga3.workers.action_journal import ActionJournal


@pytest.fixture
def config(tmp_path):
    value = TGA3Config(Path(__file__).parents[1] / "config")
    value.runtime.task_root = str(tmp_path / "tasks")
    value.runtime.cadence.finalization_grace_seconds = 0
    return value


def draft(config, question, *, signal="png"):
    config.ensure_task_directories(question)
    workspace = config.task_path(question, "workspaces", "worker-openai")
    workspace.mkdir(parents=True, exist_ok=True)
    journal = ActionJournal(config.task_path(question, "logs", "worker-openai"),
        identity={"task_id": str(question), "agent_id": "worker-openai"}, output_directory=workspace)
    action = journal.record("call", "exec_command", inputs={"command": "python extract.py"},
                            result={"aggregated_output": "archive verified", "exit_code": 0}, status="completed")
    reporter = config.task_path(question, "workspaces", "reporter")
    (reporter / "experience-assets").mkdir(parents=True, exist_ok=True)
    (reporter / "experience-assets/extract.py").write_bytes(b"print('reusable')\n")
    value = {"cards": [{"title": "图片尾部归档", "applies": {"signals": [signal, "trailing_data"],
        "requires": ["ZIP parser accepts archive"]}, "steps": ["parse and extract"],
        "verify": ["archive validated"], "limits": ["encrypted archive not tested"],
        "resources": ["experience-assets/extract.py"],
        "sources": [{"action_ids": [action["action_id"]], "validation": "reproduced"}]}]}
    atomic_json(reporter / "experience-cards.json", value)
    return value


def test_success_cards_are_idempotent_task_scoped_and_have_durable_resources(config):
    parent, source, target, foreign = uuid4(), uuid4(), uuid4(), uuid4()
    config.register_question_path(parent, source)
    config.register_question_path(parent, target)
    draft(config, source)
    store = ExperienceStore(config)
    assert store.import_report(source, successful=False)["status"] == "skipped"
    assert store.cards(parent) == []
    result = store.import_report(source, successful=True)
    assert result["status"] == "completed", result
    assert store.import_report(source, successful=True) == result
    card = store.cards(target)[0]
    assert set(card) == CARD_FIELDS
    assert card["sources"][0]["task_id"] == str(parent)
    assert card["sources"][0]["question_id"] == str(source)
    assert card["sources"][0]["validation"] == "reproduced"
    assert store.cards(foreign) == []
    with pytest.raises(KeyError):
        store.resource(foreign, card["card_id"], card["resources"][0])
    with pytest.raises(ValueError):
        store.resource(parent, card["card_id"], "../private.txt")
    (config.task_path(source, "workspaces", "reporter") / "experience-assets/extract.py").unlink()
    assert store.resource(target, card["card_id"], card["resources"][0]).read_text() == "print('reusable')\n"
    restarted = ExperienceStore(config)
    assert len(restarted.cards(parent)) == 1
    assert restarted.prepare_view(foreign).joinpath("index.json").read_text().strip() == "[]"
    assert restarted.prepare_view(parent).joinpath("cards", card["card_id"] + ".json").is_file()


def test_catalog_ack_replay_new_cards_and_on_demand_reading(config):
    parent, source, target = uuid4(), uuid4(), uuid4()
    for question in (source, target):
        config.register_question_path(parent, question)
    store = ExperienceStore(config)
    store.prepare_view(parent)
    initial = store.deliver(target, "worker-claude", "session-one")
    assert "0 available" in initial["context"]
    assert store.deliver(target, "worker-claude", "session-one") == initial
    assert store.deliver(target, "worker-claude", "session-one", initial["delivery_id"]) == {}
    draft(config, source)
    store.import_report(source, successful=True)
    advice = store.deliver(target, "worker-claude", "session-one")
    assert "图片尾部归档" in advice["context"]
    assert "parse and extract" not in advice["context"]  # No full method at claim/update.
    assert "archive validated" not in advice["context"]
    card = store.cards(target)[0]
    # Worker can use a native file read only when the concrete subproblem is encountered.
    assert json.loads((store.prepare_view(target) / "cards" / (card["card_id"] + ".json")).read_text())[
        "steps"] == ["parse and extract"]
    assert store.deliver(target, "worker-claude", "session-one", advice["delivery_id"]) == {}
    assert store.deliver(target, "worker-claude", "session-one") == {}
    assert "图片尾部归档" in store.deliver(target, "worker-claude", "session-two")["context"]


def test_invalid_evidence_and_resource_paths_never_publish_cards(config):
    question = uuid4()
    value = draft(config, question)
    reporter = config.task_path(question, "workspaces", "reporter")
    value["cards"][0]["sources"][0]["action_ids"] = ["invented-action"]
    atomic_json(reporter / "experience-cards.json", value)
    store = ExperienceStore(config)
    assert store.import_report(question, successful=True)["status"] == "error"
    assert store.cards(question) == []
    value = draft(config, question)
    value["cards"][0]["resources"] = ["../secret.txt"]
    atomic_json(reporter / "experience-cards.json", value)
    assert store.import_report(question, successful=True)["status"] == "error"
    assert store.cards(question) == []


def test_catalog_context_token_survives_turns_and_resumes_but_resets_for_fresh_cli(tmp_path, monkeypatch):
    from tga3.workers.cli_adapter import CodexAdapter
    for key, value in {
        "TGA3_WORKSPACE": str(tmp_path / "workspace"), "TGA3_LOGS": str(tmp_path / "logs"),
        "TGA3_TASK_ID": str(uuid4()), "TGA3_AGENT_ID": "worker-openai", "TGA3_AGENT_ROLE": "worker",
        "TGA3_MODEL_NAME": "fixture", "TGA3_API_KEY": "fixture", "TGA3_PROVIDER_PROTOCOL": "openai_responses",
        "TGA3_FORCE_NEW_SESSION": "0",
        "TGA3_EXPERIENCE_SESSION_TOKEN": "",
    }.items():
        monkeypatch.setenv(key, value)
    adapter = CodexAdapter()
    token = os.environ["TGA3_EXPERIENCE_SESSION_TOKEN"]
    adapter.remember_session("same-cli-context")
    monkeypatch.setenv("TGA3_CLI_GENERATION", "different-command-generation")
    CodexAdapter()
    assert os.environ["TGA3_EXPERIENCE_SESSION_TOKEN"] == token
    monkeypatch.setenv("TGA3_FORCE_NEW_SESSION", "1")
    CodexAdapter()
    assert os.environ["TGA3_EXPERIENCE_SESSION_TOKEN"] != token


def test_catalog_is_not_filtered_by_statement_and_task_deletion_cleans_only_its_cards(config):
    parent, q1, q2, foreign = uuid4(), uuid4(), uuid4(), uuid4()
    for question in (q1, q2):
        config.register_question_path(parent, question)
    draft(config, q1)
    draft(config, q2, signal="jvm")
    draft(config, foreign, signal="php")
    store = ExperienceStore(config)
    for question in (q1, q2, foreign):
        assert store.import_report(question, successful=True)["status"] == "completed"
    catalog = store.deliver(q2, "worker-openai", "session")["context"]
    assert "2 available" in catalog
    assert all(card["card_id"] in catalog for card in store.cards(parent))
    assert '"signals"' not in catalog and "jvm" not in catalog and "png" not in catalog
    assert "php" not in catalog
    store.delete_task(parent)
    assert store.cards(parent) == []
    assert len(store.cards(foreign)) == 1


def test_complete_title_catalog_exceeds_old_limit_and_migrates_prior_ack(config, monkeypatch):
    from tga3.workers.experience_catalog import initial_catalog, record_catalog

    parent, source, target = uuid4(), uuid4(), uuid4()
    for question in (source, target):
        config.register_question_path(parent, question)
    store = ExperienceStore(config)
    for batch in range(30):
        question = uuid4()
        config.register_question_path(parent, question)
        value = draft(config, question)
        template = value["cards"][0]
        value["cards"] = [{**template, "title": f"Problem {batch * 3 + i:03d}: " + "specific remedy " * 10}
                          for i in range(3)]
        atomic_json(config.task_path(question, "workspaces", "reporter") / "experience-cards.json", value)
        assert store.import_report(question, successful=True)["status"] == "completed"
    view = store.prepare_view(target)
    path = config.task_path(target, "workspaces", "worker-openai") / ".tga3/experience-delivery.json"
    atomic_json(path, {"generation": "same", "seen": ["old-hidden-card"], "catalog_sent": True,
                       "query_key": str((view / "index.json").stat().st_mtime_ns)})
    workspace = config.task_path(target, "workspaces", "worker-openai")
    for key, value in {"TGA3_WORKSPACE": str(workspace), "TGA3_EXPERIENCE_ROOT": str(view),
                       "TGA3_AGENT_ROLE": "worker", "TGA3_EXPERIENCE_SESSION_TOKEN": "same"}.items():
        monkeypatch.setenv(key, value)
    context = initial_catalog()
    assert len(context) > 6000
    for card in store.cards(parent):
        assert card["card_id"] in context and card["title"] in context
    assert "parse and extract" not in context
    fingerprints = json.loads((workspace / ".tga3/experience-initial-catalog.json").read_text())["fingerprints"]
    record_catalog()
    assert initial_catalog() == ""  # Resume must not inject the same titles a second time.
    assert store.deliver(target, "worker-openai", "same", initial=fingerprints) == {}
    assert len(json.loads(path.read_text())["seen"]) == 90
    # Backlogged updates must drain even when index mtime does not change, acknowledging only each page.
    all_titles = []
    delivery = store.deliver(target, "worker-claude", "other-context")
    while delivery:
        assert store.deliver(target, "worker-claude", "other-context") == delivery
        all_titles.append(delivery["context"])
        delivery = store.deliver(target, "worker-claude", "other-context", delivery["delivery_id"])
    assert len(all_titles) > 1
    assert all(card["title"] in "\n".join(all_titles) for card in store.cards(parent))
    assert all(set(item) == {"card_id", "title"} for item in json.loads((view / "index.json").read_text()))
    from concurrent.futures import ThreadPoolExecutor

    known_file = workspace / ".tga3/experience-known-catalog.json"
    known_file.unlink()
    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(record_catalog, [fingerprints[i:i + 5] for i in range(0, len(fingerprints), 5)]))
    assert set(json.loads(known_file.read_text())["fingerprints"]) == set(fingerprints)


async def test_http_card_and_resource_isolation(config):
    storage = InMemoryStorage()
    parent = await storage.create_task("owner", "aoe")
    foreign = await storage.create_task("foreign", "aoe")
    source = await storage.create_task("source", "aoe", parent_task_id=parent.id)
    config.register_question_path(parent.id, source.id)
    draft(config, source.id)
    app = create_app(config, storage, FakeContainerRuntime(), host_model=DeterministicHostModel())
    store = app.state.experience
    assert store.import_report(source.id, successful=True)["status"] == "completed"
    card = store.cards(parent.id)[0]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert len((await client.get(f"/api/v3/tasks/{parent.id}/experience")).json()["cards"]) == 1
        assert (await client.get(f"/api/v3/tasks/{foreign.id}/experience")).json()["cards"] == []
        relative = card["resources"][0]
        url = f"/api/v3/tasks/{parent.id}/experience/{card['card_id']}/resources/{relative}"
        assert (await client.get(url)).content == b"print('reusable')\n"
        assert (await client.get(url.replace(str(parent.id), str(foreign.id)))).status_code == 422
        assert "experience" not in json.dumps(storage.tasks[parent.id].metadata)


async def test_reporter_extracts_in_same_call_only_for_solved_questions(config):
    from tga3.blackboard import Blackboard
    from tga3.domain import Actor, Artifact, ArtifactRef, PublishRequest
    from tga3.events import TaskEvents
    from tga3.host_agents import Automation

    storage = InMemoryStorage()
    parent = await storage.create_task("batch", "aoe")
    successful = await storage.create_task("solved", "aoe", parent_task_id=parent.id, metadata={"status": "solved"})
    abandoned = await storage.create_task("abandoned", "aoe", parent_task_id=parent.id,
                                          metadata={"status": "abandoned"})
    board = Blackboard(storage)
    calls = []
    for task in (successful, abandoned):
        config.register_question_path(parent.id, task.id)
        binding = config.agents.agents["reporter"]
        await storage.upsert_agent(AgentRun(task_id=task.id, agent_id="reporter", sdk=binding.runtime,
            provider_id=binding.provider_id, model_id=binding.model_id, protocol=binding.protocol))
    actor = Actor(agent_id="worker-openai", display_name="worker", role="worker")
    artifact = await storage.register_artifact(Artifact(task_id=successful.id, created_by=actor,
        name="result", storage_path="result.txt", media_type="text/plain", sha256="a" * 64, size_bytes=1))
    finding = await board.publish(successful.id, actor, PublishRequest(kind="finding", body={"claim": "verified"},
        artifact_refs=[ArtifactRef(artifact_id=artifact.id, locator="line 1")], idempotency_key="finding"))
    await board.publish(successful.id, actor, PublishRequest(kind="final_candidate", body={
        "conclusion": "ok", "rationale": "verified", "finding_ids": [finding.id]}, idempotency_key="final"))

    class Reporter:
        async def report(self, snapshot, binding):
            calls.append(binding.system_prompt)
            if REPORT_INSTRUCTIONS in binding.system_prompt:
                draft(config, successful.id)
            return "# Report\n\nverified"

    automation = Automation(config, storage, board, TaskEvents(storage), Reporter())
    await automation._report(successful.id)
    await automation._report(abandoned.id)
    assert len(calls) == 2 and REPORT_INSTRUCTIONS in calls[0] and REPORT_INSTRUCTIONS not in calls[1]
    assert len(automation.experience.cards(parent.id)) == 1
    assert (await storage.get_task(successful.id)).state == RunState.COMPLETED
    assert (await storage.get_task(abandoned.id)).metadata["status"] == "abandoned"
