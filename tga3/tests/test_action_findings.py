import asyncio
import json
from pathlib import Path

import httpx
import pytest

from tga3.action_findings import ActionFindingInput, ActionFindingPublisher
from tga3.action_logs import read_action_log
from tga3.app import create_app
from tga3.blackboard import Blackboard
from tga3.config import TGA3Config
from tga3.docker_runtime import FakeContainerRuntime
from tga3.domain import AgentRun, RunState
from tga3.errors import ContractError
from tga3.storage import InMemoryStorage
from tga3.workers.action_journal import ActionJournal


async def setup(tmp_path, *, completed=True):
    config = TGA3Config(Path(__file__).parents[1] / "config")
    config.runtime.task_root = str(tmp_path / "tasks")
    storage = InMemoryStorage()
    task = await storage.create_task("single observation", "pwn")
    for agent_id in ("worker-openai", "worker-claude", "scout", "reporter"):
        agent = config.agents.agents[agent_id]
        await storage.upsert_agent(AgentRun(task_id=task.id, agent_id=agent_id, sdk=agent.runtime,
                                          provider_id=agent.provider_id, model_id="task-selected-model",
                                          protocol=agent.protocol))
    root = config.task_path(task.id, "workspaces", "worker-openai")
    root.mkdir(parents=True)
    journal = ActionJournal(config.task_path(task.id, "logs", "worker-openai"),
                            identity={"task_id": str(task.id), "agent_id": "worker-openai"},
                            output_directory=root)
    journal.record("native-0", "Bash", inputs={"command": "curl -sS /api/export > response.txt"})
    (root / "response.txt").write_text("403 Forbidden")
    if completed:
        journal.record("native-0", "Bash", result={"aggregated_output": "", "exit_code": 0}, status="completed")
    action = read_action_log(journal.directory)["actions"][0]
    request = ActionFindingInput(action_id=action["action_id"], claim="Anonymous GET returns 403",
                                 scope="Only this GET was tested; authenticated behavior is unknown",
                                 preconditions="No login cookie")
    return config, storage, task, root, action, request


async def test_one_result_publishes_scope_method_body_and_attachments_and_retries_once(tmp_path):
    config, storage, task, root, action, request = await setup(tmp_path)
    publisher = ActionFindingPublisher(config, Blackboard(storage))
    results = await asyncio.gather(*(publisher.publish(task.id, "worker-openai", request) for _ in range(5)))
    assert {result["saved"] for result in results} == {True}
    assert len({result["entry"]["id"] for result in results}) == 1
    assert all("topic" not in result["entry"] for result in results)
    entry = (await storage.list_entries(task.id))[0]
    assert entry.topic is None
    assert entry.source_action_ids == [action["action_id"]]
    assert entry.actor.model == "task-selected-model"
    detail = entry.body["detail"]
    for value in [request.scope, request.preconditions, action["action"]["command"], "response.txt",
                  action["artifact"]["output_path"], "completed"]:
        assert value in detail
    links = await storage.finding_links(task.id, entry.id)
    assert {link.locator for link in links} == {"response.txt", action["artifact"]["output_path"]}
    assert (root / action["artifact"]["output_path"]).read_text() == ""
    (root / "response.txt").unlink()
    assert (await publisher.publish(task.id, "worker-openai", request))["entry"]["id"] == str(entry.id)
    assert len(storage.artifacts) == 2


async def test_failed_publish_keeps_retryable_draft_and_reuses_key(tmp_path, monkeypatch):
    config, storage, task, root, _, request = await setup(tmp_path)
    board = Blackboard(storage)
    publisher = ActionFindingPublisher(config, board)
    original = board.publish

    async def unavailable(*args, **kwargs):
        raise TimeoutError("fixture")

    monkeypatch.setattr(board, "publish", unavailable)
    with pytest.raises(TimeoutError):
        await publisher.publish(task.id, "worker-openai", request)
    path = next((root / ".tga3/finding-outbox").glob("*.json"))
    failed = json.loads(path.read_text(encoding="utf-8"))
    assert failed["status"] == "failed" and failed["request"] == request.model_dump()
    monkeypatch.setattr(board, "publish", original)
    result = await publisher.publish(task.id, "worker-openai", request)
    assert result["saved"] is True
    assert result["entry"]["idempotency_key"] == failed["idempotency_key"]
    assert json.loads(path.read_text(encoding="utf-8"))["status"] == "saved"
    assert len(storage.artifacts) == 2


@pytest.mark.parametrize("case", ["foreign", "unfinished", "closed", "missing", "scout"])
async def test_invalid_source_or_state_creates_no_evidence_or_finding(tmp_path, case):
    config, storage, task, root, action, request = await setup(tmp_path, completed=case != "unfinished")
    agent_id = "worker-openai"
    if case == "foreign":
        agent_id = "worker-claude"
    if case == "scout":
        agent_id = "scout"
    if case == "closed":
        await storage.update_task(task.id, state=RunState.COMPLETED)
    if case == "missing":
        (root / action["artifact"]["output_path"]).unlink()
    with pytest.raises(ContractError):
        await ActionFindingPublisher(config, Blackboard(storage)).publish(task.id, agent_id, request)
    assert not storage.artifacts
    assert not await storage.list_entries(task.id)


async def test_files_import_identity_evidence_and_final_candidate_through_checkpoint(tmp_path):
    config, storage, task, root, _, request = await setup(tmp_path)
    app = create_app(config, storage, FakeContainerRuntime())
    findings = config.task_path(task.id, 'findings', 'worker-openai')
    findings.mkdir(parents=True)
    (findings/'worker-openai-001.json').write_text(request.model_dump_json())
    url = f'/internal/blackboard/{task.id}/worker-openai/checkpoint'
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        first = (await client.post(url, json={'file_sync': True})).json()
        receipt = first['publication_receipts'][0]
        assert receipt['status'] == 'imported'
        entry = (await storage.list_entries(task.id))[0]
        assert receipt['entry_id'] == str(entry.id) and entry.actor.agent_id == 'worker-openai'
        assert len(storage.artifacts) == 2
        assert len(await storage.finding_links(task.id, entry.id)) == 2
        await client.post(url, json={'file_sync': True, 'ack': first['delivery_id'], 'ack_only': True})
        assert len(await storage.list_entries(task.id)) == 1
        final = root/'result/final-candidate.json'
        final.write_text(json.dumps({'conclusion':'fixture result', 'rationale':'verified',
                                     'finding_ids':[receipt['entry_id']]}))
        saved = (await client.post(url, json={'file_sync': True})).json()
        assert saved['publication_receipts'][0]['status'] == 'imported'
        assert (await storage.list_entries(task.id))[-1].kind == 'final_candidate'
        assert len(await storage.list_entries(task.id)) == 2
        await app.state.automation.cancel(task.id)
