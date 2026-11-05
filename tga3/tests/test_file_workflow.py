import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

from test_action_findings import setup as finding_setup
from test_aoe_runtime import prepare, setup as aoe_setup
from tga3.aoe_models import active_attempts, AOEQuestionInput
from tga3.app import create_app
from tga3.blackboard import Blackboard
from tga3.docker_runtime import FakeContainerRuntime
from tga3.file_publications import FilePublications
from tga3.workers.hook_service import HookService


def test_lightweight_hook_import_does_not_initialize_control_plane_schemas():
    result = subprocess.run([sys.executable, '-c',
        'import tga3.workers.hook_client,sys; '
        'assert "pydantic" not in sys.modules; '
        'assert "tga3.config" not in sys.modules; '
        'assert "httpx" not in sys.modules'], capture_output=True, text=True,
        env={**os.environ, 'PYTHONPATH':str(Path(__file__).parents[1]/'src')})
    assert result.returncode == 0, result.stderr


def test_resident_hook_service_reuses_remote_connection_across_native_hook_requests(monkeypatch, tmp_path):
    connections = []
    class Remote(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            connections.append(self.connection)
            self.send_response(200)
            self.send_header('Content-Length', '2')
            self.end_headers()
            self.wfile.write(b'{}')
        def log_message(self, *_args):
            pass
    remote = ThreadingHTTPServer(('127.0.0.1', 0), Remote)
    threading.Thread(target=remote.serve_forever, daemon=True).start()
    for key, value in {'TGA3_AGENT_ROLE':'worker', 'TGA3_TASK_ID':'fixture', 'TGA3_AGENT_ID':'worker-openai',
        'TGA3_WORKSPACE':str(tmp_path), 'TGA3_BLACKBOARD_MCP_URL':f'http://127.0.0.1:{remote.server_port}/mcp'}.items():
        monkeypatch.setenv(key, value)
    service = HookService()
    try:
        with httpx.Client(trust_env=False) as client:
            for generation in ['cycle-one', 'cycle-two']:
                result = client.post(f'http://127.0.0.1:{service.port}/', json={
                    'hook_event_name':'UserPromptSubmit', '_tga_generation':generation})
                assert result.status_code == 200
        assert len(connections) == 2 and connections[0] is connections[1]
    finally:
        service.close()
        remote.shutdown()
        remote.server_close()


async def test_first_action_starts_budget_once_and_claimed_idle_time_does_not_count(tmp_path):
    cfg, storage, containers, c, parent, clock, questions = await prepare(tmp_path)
    await c.aoe.tick(parent.id)
    first = containers.launched[0]
    clock[0] = 10_000
    await c.aoe.tick(parent.id)
    active = active_attempts(await storage.get_task(first.task_id))[0]
    assert active['started_at'] is None and active['deadline'] is None
    app = create_app(cfg, storage, FakeContainerRuntime())
    app.state.coordinator.aoe.clock = lambda: clock[0]
    url = f'/internal/blackboard/{first.task_id}/{first.agent.agent_id}/checkpoint'
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        # A denied first tool is not an actual action and cannot start the clock.
        response = (await client.post(url, json={'action_start':first.attempt_id, 'force_start':True, 'final':True})).json()
        assert active_attempts(await storage.get_task(first.task_id))[0]['deadline'] is None
        await client.post(url, json={'ack':response['delivery_id'], 'action_start':first.attempt_id})
        active = active_attempts(await storage.get_task(first.task_id))[0]
        assert active['started_at'] == 10_000 and active['deadline'] == 11_200
        clock[0] += 100
        await client.post(url, json={'action_start':first.attempt_id})
        assert active_attempts(await storage.get_task(first.task_id))[0]['deadline'] == 11_200


async def test_registration_receipts_only_include_this_call_and_question_lives_at_root(tmp_path):
    cfg, storage, _, c, parent, _ = await aoe_setup(tmp_path)
    await c.aoe.register_questions(parent.id, [AOEQuestionInput(
        source_key=str(i), title=str(i), description='original', difficulty='easy') for i in range(2)])
    result = await c.aoe.register_questions(parent.id, [AOEQuestionInput(
        source_key='new', title='New', description='new description', difficulty='easy')])
    assert len(result['questions']) == 1 and result['questions'][0]['source_key'] == 'new'
    assert 'description' not in json.dumps(result)
    question = next(q for q in await storage.list_questions(parent.id) if q.title == 'New')
    assert (cfg.task_path(question.id)/'question.json').is_file()
    assert not (cfg.task_path(question.id, 'inputs')/'question.json').exists()
    overview = json.loads((cfg.task_path(parent.id, 'inputs')/'questions.json').read_text())
    assert len(overview['questions']) == 3


async def test_finding_files_are_bound_isolated_idempotent_and_immutable_after_import(tmp_path):
    cfg, storage, task, _, _, request = await finding_setup(tmp_path)
    importer = FilePublications(cfg, Blackboard(storage))
    root = cfg.task_path(task.id, 'findings', 'worker-openai')
    root.mkdir(parents=True)
    path = root/'worker-openai-001.json'
    path.write_text(request.model_dump_json())
    first = await importer.sync_findings(task.id, 'worker-openai')
    assert first[0]['status'] == 'imported'
    assert await importer.sync_findings(task.id, 'worker-openai') == []
    restarted = FilePublications(cfg, Blackboard(storage))
    assert (await restarted.sync_findings(task.id, 'worker-openai'))[0]['entry_id'] == first[0]['entry_id']
    assert await restarted.sync_findings(task.id, 'worker-claude') == []
    path.write_text(json.dumps({**request.model_dump(), 'claim':'modified imported fact'}))
    assert (await restarted.sync_findings(task.id, 'worker-openai'))[0]['status'] == 'error'
    assert len(await storage.list_entries(task.id)) == 1


async def test_final_file_waits_for_unread_updates_then_imports_after_ack(tmp_path):
    cfg, storage, task, workspace, _, request = await finding_setup(tmp_path)
    app = create_app(cfg, storage, FakeContainerRuntime())
    root = cfg.task_path(task.id, 'findings', 'worker-openai')
    root.mkdir(parents=True)
    (root/'fact.json').write_text(request.model_dump_json())
    url = f'/internal/blackboard/{task.id}/worker-openai/checkpoint'
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        initial = (await client.post(url, json={'file_sync':True})).json()
        ident = initial['publication_receipts'][0]['entry_id']
        (workspace/'result/final-candidate.json').write_text(json.dumps({
            'conclusion':'verified', 'rationale':'observed', 'finding_ids':[ident]}))
        pending = (await client.post(url, json={'file_sync':True})).json()
        assert pending['publication_receipts'][0]['status'] == 'waiting_for_blackboard'
        assert not any(e.kind == 'final_candidate' for e in await storage.list_entries(task.id))
        imported = (await client.post(url, json={'file_sync':True, 'ack':initial['delivery_id'], 'ack_only':True})).json()
        assert imported['publication_receipts'][0]['status'] == 'imported'
        await app.state.automation.cancel(task.id)
