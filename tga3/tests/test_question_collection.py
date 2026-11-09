import json

import pytest
from test_aoe_runtime import setup

from tga3.domain import PublishRequest, USER_ACTOR
from tga3.errors import ContractError
from tga3.question_collection import CollectionPlan, declared_requests
from tga3.workers.aoe_access import overview_policy


async def test_list_maps_original_fields_preserves_source_and_finishes_without_transcription(tmp_path):
    cfg, storage, _, c, parent, _ = await setup(tmp_path)
    text = ('BENCHMARK_BASE_URL: https://fixture.example\n\n题目列表：\n'
            'GET {BENCHMARK_BASE_URL}/questions\n\nPOST https://fixture.example/start')
    await c.blackboard.publish(parent.id, USER_ACTOR, PublishRequest(
        kind='user_prompt', topic='task', body={'text': text}, idempotency_key='task'))
    calls = []

    async def request(req):
        calls.append(req)
        return 200, [{'unique_code': str(i), 'description': '完整题面 '+str(i), 'difficulty': 'easy',
                      'total_score': 100, 'container_addr': '', 'arbitrary_source': {'value': i}}
                     for i in range(40)]
    c.aoe.targets._request = request
    plan = CollectionPlan(request={'method': 'GET', 'url': 'https://fixture.example/questions',
                                   'headers': {'BENCHMARK_TOKEN': 'fixture-secret'}},
                          target={'mode': 'managed', 'start': {'url': 'https://fixture.example/start',
                                  'params': {'unique_code': '{unique_code}'},
                                  'headers': {'BENCHMARK_TOKEN': 'fixture-secret'}},
                                  'stop': {'url': 'https://fixture.example/close',
                                           'params': {'unique_code': '{unique_code}'}}},
                          submission={'request': {'url': 'https://fixture.example/submit',
                                      'body': {'unique_code': '{unique_code}', 'flag': '{flag}'}}})
    result = await c.aoe.collection.collect(parent.id, plan)
    assert result['question_count'] == 40 and result['collection_complete']
    assert len(calls) == 1 and calls[0].headers['BENCHMARK_TOKEN'] == 'fixture-secret'
    q = next(q for q in await storage.list_questions(parent.id) if q.metadata['source_key'] == '7')
    original = json.loads((cfg.task_path(q.id) / 'source.json').read_text(encoding='utf8'))
    assert original['arbitrary_source'] == {'value': 7} and 'source' not in q.metadata
    saved = json.loads((cfg.task_path(q.id) / 'question.json').read_text(encoding='utf8'))
    assert saved['source_path'] == 'source.json'
    assert saved['submission']['request']['body'] == {'unique_code': '7', 'flag': '{flag}'}
    assert saved['target']['start']['params']['unique_code'] == '7'
    assert not saved['attachments']
    assert all(e.topic == 'question' for e in await storage.list_entries(q.id))
    assert text == (cfg.task_path(parent.id) / 'overview/task.md').read_text(encoding='utf8')
    await c.aoe.collection.collect(parent.id, plan)
    assert len(calls) == 1


async def test_collector_rejects_other_endpoints_before_network_and_follows_only_list_pagination(tmp_path):
    _, storage, _, c, parent, _ = await setup(tmp_path)
    await c.blackboard.publish(parent.id, USER_ACTOR, PublishRequest(
        kind='user_prompt', topic='task', body={'text': 'Question list: GET https://fixture.example/questions'},
        idempotency_key='task'))
    calls = []

    async def request(req):
        calls.append(req.url)
        return 200, {'items': [{'unique_code': 'one', 'description': 'original'}],
                     'next': '/frontend.js'}
    c.aoe.targets._request = request
    for endpoint in ['frontend.js', 'docs', 'attachment', 'hint', 'questions/1']:
        with pytest.raises(ContractError, match='question-list API only'):
            await c.aoe.collection.collect(parent.id, CollectionPlan(
                request={'method': 'GET', 'url': 'https://fixture.example/'+endpoint}))
    assert not calls
    with pytest.raises(ContractError, match='Pagination must stay'):
        await c.aoe.collection.collect(parent.id, CollectionPlan(
            request={'method': 'GET', 'url': 'https://fixture.example/questions'},
            items_path='items', next_url_path='next'))
    assert calls == ['https://fixture.example/questions']
    assert not (await storage.get_task(parent.id)).metadata.get('collection_complete')


async def test_preflight_failure_stops_before_list_request(tmp_path):
    _, _, _, c, parent, _ = await setup(tmp_path)
    await c.blackboard.publish(parent.id, USER_ACTOR, PublishRequest(
        kind='user_prompt', topic='task', body={'text': 'VPN check: GET http://vpn.example/\n\n'
                                                    '题目列表 GET https://fixture.example/questions'},
        idempotency_key='task'))
    calls = []

    async def request(req):
        calls.append(req.url)
        return 200, {'status': 'not-ready'}
    c.aoe.targets._request = request
    with pytest.raises(ContractError, match='connectivity check'):
        await c.aoe.collection.collect(parent.id, CollectionPlan(
            request={'method': 'GET', 'url': 'https://fixture.example/questions'},
            preflight={'method': 'GET', 'url': 'http://vpn.example/'}))
    assert calls == ['http://vpn.example/']


def test_native_overview_reads_gate_worker_once_per_context_and_limit_scout(monkeypatch, tmp_path):
    for k, v in {'TGA3_TASK_OVERVIEW_ROOT': '/task-overview', 'TGA3_WORKSPACE': str(tmp_path),
                 'TGA3_AOE_PARENT_ID': 'parent', 'TGA3_AGENT_ROLE': 'worker'}.items():
        monkeypatch.setenv(k, v)
    def event(name, command, response='', session='one'):
        return {'hook_event_name': name, 'tool_name': 'exec_command', 'session_id': session,
                'tool_input': {'cmd': command}, 'tool_response': response}
    assert overview_policy(event('PreToolUse', 'curl https://target'))
    for name in ['scene.md', 'task.md']:
        command = 'cat /task-overview/'+name
        assert not overview_policy(event('PreToolUse', command))
        overview_policy(event('PostToolUse', command, 'Output truncated'))
        assert overview_policy(event('PreToolUse', 'curl https://target'))
        overview_policy(event('PostToolUse', command, 'Full original text'))
    assert not overview_policy(event('PreToolUse', 'curl https://target'))
    assert overview_policy(event('PreToolUse', 'curl https://target', session='new-context'))
    monkeypatch.setenv('TGA3_AGENT_ROLE', 'scout')
    for command in ['curl https://fixture.example/docs', 'python -c "print(1)"',
                    'cat /task-overview/task.md; curl https://fixture.example',
                    'cat /inputs/../../etc/passwd', 'cat /workspace/script.py']:
        assert overview_policy(event('PreToolUse', command))
    assert not overview_policy(event('PreToolUse', 'cat /inputs/api.md'))
    tool = {'hook_event_name': 'PreToolUse', 'tool_name': 'mcp__intel__scout_collect_questions', 'session_id': 'one'}
    assert not overview_policy(tool)
    monkeypatch.setenv('TGA3_AOE_ROOT', str(tmp_path))
    (tmp_path / 'questions.json').write_text(json.dumps({'collection_complete': True}))
    assert 'complete' in overview_policy(tool)
    assert overview_policy(event('PreToolUse', 'cat /inputs/api.md'))


def test_bulleted_original_api_instructions_approve_only_list():
    import re
    text = ('- BENCHMARK_BASE_URL: https://fixture.example\r\n\r\n'
            '1. GET  {BENCHMARK_BASE_URL}/questions\r\n   - 获取题目列表及每题作答进度。\r\n'
            '2. POST {BENCHMARK_BASE_URL}/start?unique_code=<unique_code>\r\n'
            '   - Start instance\r\n'
            '3. GET {BENCHMARK_BASE_URL}/hint?unique_code=<unique_code>\r\n   - Fetch hint')
    urls = [url for _, url, paragraph in declared_requests(text)
            if re.search(r'题目列表', paragraph)]
    assert urls == ['https://fixture.example/questions']


async def test_pagination_streams_to_workers_and_checks_total_without_refetching_other_endpoints(tmp_path):
    import asyncio
    _, storage, runtime, c, parent, _ = await setup(tmp_path)
    await c.blackboard.publish(parent.id, USER_ACTOR, PublishRequest(
        kind='user_prompt', topic='task', body={'text': 'Question list: GET https://fixture.example/questions'},
        idempotency_key='task'))
    second_page, release = asyncio.Event(), asyncio.Event()
    calls = []
    async def request(req):
        calls.append(req.url)
        if len(calls) == 1:
            return 200, {'items': [{'unique_code': 'easy', 'description': 'first', 'difficulty': 'easy'}],
                         'next': '?page=2', 'total': 2}
        second_page.set()
        await release.wait()
        return 200, {'items': [{'unique_code': 'hard', 'description': 'second', 'difficulty': 'hard'}],
                     'next': None, 'total': 2}
    c.aoe.targets._request = request
    plan = CollectionPlan(request={'method': 'GET', 'url': 'https://fixture.example/questions'},
                          items_path='items', next_url_path='next', total_path='total')
    job = asyncio.create_task(c.aoe.collection.collect(parent.id, plan))
    try:
        await asyncio.wait_for(second_page.wait(), 2)
        await c.aoe.tick(parent.id)
        assert len(runtime.launched) == 1
        assert not (await storage.get_task(parent.id)).metadata.get('collection_complete')
        release.set()
        assert (await job)['question_count'] == 2
        assert calls == ['https://fixture.example/questions', 'https://fixture.example/questions?page=2']
    finally:
        release.set()
        await job
