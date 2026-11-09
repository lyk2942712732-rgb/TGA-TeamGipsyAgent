import json
from pathlib import Path

import pytest
from test_aoe_sessions import environment

from tga3.token_usage import TokenUsageTracker
from tga3.workers.action_journal import ActionJournal
from tga3.workers.extra_cli import OpenCodeAdapter, QwenAdapter
from tga3.submissions import response_objects, result_text


@pytest.mark.parametrize('adapter_type', [OpenCodeAdapter, QwenAdapter])
@pytest.mark.parametrize('protocol', ['openai_chat_completions', 'openai_responses', 'anthropic'])
def test_new_cli_model_mcp_hooks_and_resume(monkeypatch, tmp_path, adapter_type, protocol):
    environment(monkeypatch, tmp_path)
    monkeypatch.setenv('TGA3_AGENT_RUNTIME', adapter_type.runtime)
    monkeypatch.setenv('TGA3_PROVIDER_PROTOCOL', protocol)
    monkeypatch.setenv('TGA3_BASE_URL', 'https://provider.example/api' + ('' if protocol == 'anthropic' else '/v1'))
    monkeypatch.setenv('TGA3_WEB_SEARCH_MCP_URL', 'http://project/web-mcp/')
    adapter = adapter_type()
    command, env = adapter.command()
    assert adapter.timeout is None
    assert 'fixture-secret' not in str(command)
    if adapter_type is OpenCodeAdapter:
        config = json.loads(Path(env['OPENCODE_CONFIG']).read_text())
        assert config['mcp']['web']['headers']['X-TGA3-Agent-ID'] == 'worker-openai'
        assert config['plugin'][0].endswith('opencode_plugin.js')
        assert 'fixture-secret' not in json.dumps(config)
        if protocol == 'anthropic':
            assert config['provider']['tga']['options']['baseURL'].endswith('/api/v1')
    else:
        config = json.loads((Path(env['QWEN_HOME']) / 'settings.json').read_text())
        assert Path(env['QWEN_HOME']).parent.name == '.tga3'
        assert set(config['hooks']) >= {'UserPromptSubmit','PreToolUse','PostToolUse','PostToolUseFailure'}
        assert config['mcpServers']['web']['httpUrl'] == 'http://project/web-mcp/'
    adapter.remember_session('native-existing-session')
    resumed = adapter_type()
    assert 'native-existing-session' in resumed.command()[0]


def test_new_cli_state_is_never_collected_as_execution_attachments(tmp_path):
    for private in ['.opencode', '.qwen', '.tga3']:
        path = tmp_path / private / 'native-session.db'
        path.parent.mkdir()
        path.write_text('private CLI state')
    (tmp_path / 'proof.txt').write_text('evidence')
    journal = ActionJournal(tmp_path / 'logs', output_directory=tmp_path, identity={'agent_id': 'worker-opencode'})
    assert set(journal._snapshot()) == {'proof.txt'}


def test_qwen_tool_body_exposes_the_actual_platform_receipt():
    body = json.dumps({'llmContent': 'HTTP 200\n{"correct":true,"correct_flag_count":1,"total_flag_count":1}',
                       'returnDisplay': 'shell display'})
    assert next(response_objects(result_text(body)))['correct'] is True


async def test_new_cli_usage_is_reported_and_replayed_messages_are_deduplicated(monkeypatch, tmp_path):
    environment(monkeypatch, tmp_path)
    adapter = OpenCodeAdapter()
    async def emit(*_): pass
    await adapter.event({'type':'step_finish','sessionID':'same','part':{'id':'part-1', 'reason':'stop',
        'tokens':{'input':5,'output':3,'reasoning':2,'cache':{'read':7,'write':1}}}}, emit)
    await adapter.event({'type':'step_finish','sessionID':'same','part':{'id':'part-1', 'reason':'stop',
        'tokens':{'input':5,'output':3,'reasoning':2,'cache':{'read':7,'write':1}}}}, emit)
    tracker = TokenUsageTracker(None, None)
    usage, _ = tracker.read_updates(tmp_path)
    assert len(usage) == 1
    assert next(iter(usage.values())) == {'input_tokens':13,'output_tokens':5,'cached_input_tokens':7,
                                        'cache_write_input_tokens':1,'total_tokens':18}
