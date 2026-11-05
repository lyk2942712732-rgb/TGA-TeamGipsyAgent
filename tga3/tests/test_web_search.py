import json
import tomllib

import httpx
import pytest
from pydantic import SecretStr

from test_action_findings import setup as finding_setup
from tga3.mcp_server import build_web_mcp
from tga3.web_search import WebSearchService
from tga3.workers.cli_adapter import CodexAdapter, ClaudeAdapter
from tga3.workers.prompts import worker_instructions
from test_aoe_sessions import environment


async def setup(tmp_path):
    result = await finding_setup(tmp_path)
    for provider in result[0].models.providers:
        provider.api_keys[0].api_key = SecretStr('fixture-key')
    return result


async def test_web_search_has_real_sources_bounded_results_and_never_returns_reasoning(tmp_path):
    cfg, storage, task, _, _, _ = await setup(tmp_path)
    calls = []
    def provider(request):
        body = json.loads(request.content)
        calls.append(body)
        return httpx.Response(200, json={'output':[
            {'type':'web_search_call','status':'completed','action':{'sources':[
                {'url':'https://docs.python.org/3/library/argparse.html'}, {'url':'https://example.org/reference'}]}},
            {'type':'reasoning','content':[{'text':'private reasoning must not be relayed'}]},
            {'type':'message','content':[{'type':'output_text','text':'Relevant public references',
                'annotations':[{'url':'https://docs.python.org/3/library/argparse.html','title':'argparse'}]}]}]})
    service = WebSearchService(cfg, storage, transport=httpx.MockTransport(provider))
    try:
        result = await service.search(task.id, 'worker-claude', 'Python argparse documentation', 1)
        assert result['status'] == 'ok' and len(result['results']) == 1
        assert result['results'][0]['title'] == 'argparse'
        assert 'private reasoning' not in json.dumps(result)
        assert calls[0]['max_tool_calls'] == 1 and calls[0]['tools'] == [{'type':'web_search'}]
    finally:
        await service.close()


async def test_two_failures_disable_search_without_more_network_calls_and_new_attempt_can_retry(tmp_path):
    cfg, storage, task, _, _, _ = await setup(tmp_path)
    calls = []
    def provider(request):
        calls.append(request)
        return httpx.Response(503, json={'error':'upstream unavailable'})
    service = WebSearchService(cfg, storage, transport=httpx.MockTransport(provider))
    try:
        first = await service.search(task.id, 'worker-openai', 'public method')
        second = await service.search(task.id, 'worker-openai', 'another public query')
        disabled = await service.search(task.id, 'worker-openai', 'do not retry this')
        assert first['failed_attempts'] == 1 and second['stop_searching']
        assert disabled['status'] == 'disabled' and len(calls) == 2
        await storage.update_agent(task.id, 'worker-openai', metadata={'aoe_attempt_id':'new-attempt'})
        assert (await service.search(task.id, 'worker-openai', 'new attempt'))['failed_attempts'] == 1
        assert len(calls) == 3
        with pytest.raises(ValueError, match='assigned Workers'):
            await service.search(task.id, 'scout', 'public query')
    finally:
        await service.close()


async def test_search_rejects_model_memory_answer_without_completed_web_call(tmp_path):
    cfg, storage, task, _, _, _ = await setup(tmp_path)
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json={'output':[
        {'type':'message','content':[{'type':'output_text','text':'I know this URL from memory.'}]}]}))
    service = WebSearchService(cfg, storage, transport=transport)
    try:
        result = await service.search(task.id, 'worker-openai', 'public query')
        assert result['status'] == 'unavailable'
    finally:
        await service.close()


async def test_search_mcp_is_read_only_and_has_no_publication_tools(tmp_path):
    cfg, storage, _, _, _, _ = await setup(tmp_path)
    service = WebSearchService(cfg, storage)
    tools = await build_web_mcp(cfg, service).list_tools()
    assert [t.name for t in tools] == ['web_search']
    assert set(tools[0].inputSchema['properties']) == {'query', 'max_results'}


@pytest.mark.parametrize('kind', ['codex','claude'])
def test_worker_tool_catalog_native_file_publication_and_enabled_search(monkeypatch, tmp_path, kind):
    environment(monkeypatch, tmp_path, claude=kind=='claude')
    monkeypatch.setenv('TGA3_WEB_SEARCH_MCP_URL','http://fixture/web-mcp/')
    monkeypatch.setenv('TGA3_CTF_KNOWLEDGE_ROOT','/knowledge/des-ctf-knowledge')
    adapter = ClaudeAdapter() if kind=='claude' else CodexAdapter()
    args, _ = adapter.command()
    if kind=='codex':
        config = tomllib.loads((tmp_path/'.codex/config.toml').read_text())
        assert config['web_search'] == 'live' and config['mcp_servers']['web']['required']
        assert config['mcp_servers']['web']['url'] == 'http://fixture/web-mcp/'
    else:
        assert 'WebSearch' in args[args.index('--tools')+1] and 'WebFetch' in args[args.index('--tools')+1]
        assert 'mcp__web__web_search' in args[args.index('--allowedTools')+1]
    prompt = worker_instructions()
    for tool in ['find_technique','find_atom','find_wp','search','read_topic','get_payload','run_script','solve_hint','web_search']:
        assert tool in prompt
    assert 'Codex exec_command/apply_patch; Claude Write/Bash' in prompt
    assert 'After two consecutive unsuccessful searches, STOP' in prompt
    assert '/workspace/final-candidate.json' in prompt and '/workspace/result/final-candidate.json' not in prompt
