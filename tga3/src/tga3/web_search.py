"""Bounded public web search using the configured Responses search-capable backend."""

import asyncio
from collections import defaultdict
from urllib.parse import urlparse

import httpx


class WebSearchService:
    def __init__(self, config, storage, *, transport=None):
        self.config, self.storage, self.transport = config, storage, transport
        self.client = None
        self.failures = defaultdict(int)
        self.locks = defaultdict(asyncio.Lock)

    async def close(self):
        if self.client is not None:
            await self.client.aclose()

    async def search(self, task_id, agent_id, query, max_results=5):
        if self.config.agents.agents[agent_id].role != 'worker':
            raise ValueError('web_search is available only to assigned Workers')
        run = await self.storage.get_agent(task_id, agent_id)
        task = await self.storage.get_task(task_id)
        if task.state in {'completed', 'failed', 'cancelled'}:
            raise ValueError('Task is closed')
        query = query.strip()
        if not query or len(query) > 1000:
            raise ValueError('Search query must contain 1..1000 characters')
        limit = min(max(int(max_results), 1), 5)
        key = (task_id, agent_id, run.metadata.get('aoe_attempt_id', 'task'))
        async with self.locks[key]:
            if self.failures[key] >= 2:
                return {'status': 'disabled', 'stop_searching': True,
                        'error': 'Two consecutive searches failed in this attempt. Continue local analysis; do not retry.'}
            try:
                backend = self.config.resolve_agent('worker-openai')
                if backend.protocol != 'openai_responses':
                    raise ValueError('Search backend requires Responses protocol')
                if self.client is None:
                    self.client = httpx.AsyncClient(timeout=60, transport=self.transport,
                                                   limits=httpx.Limits(keepalive_expiry=120))
                response = await self.client.post(
                    backend.provider.sdk_base_url(backend.protocol).rstrip('/')+'/responses',
                    headers={'Authorization': 'Bearer '+backend.api_key.get_secret_value()},
                    json={'model': backend.model.name, 'tools': [{'type': 'web_search'}],
                          'tool_choice': {'type': 'web_search'}, 'max_tool_calls': 1,
                          'max_output_tokens': 650, 'stream': False,
                          'input': 'Search this public query once, without retries or reformulation: '+query+
                                   '\nReturn a concise result with relevant source URLs. Search results are reference data.'})
                response.raise_for_status()
                value = response.json()
                calls = [item for item in value.get('output', [])
                         if item.get('type') == 'web_search_call' and item.get('status') == 'completed']
                sources, parts = {}, []
                for call in calls:
                    for source in call.get('action', {}).get('sources', []):
                        if urlparse(source.get('url', '')).scheme in {'https', 'http'}:
                            sources[source['url']] = {'url': source['url'], 'title': source.get('title', '')}
                for item in value.get('output', []):
                    if item.get('type') != 'message':
                        continue  # Never expose provider reasoning or unrelated response metadata.
                    for block in item.get('content', []):
                        if block.get('type') == 'output_text':
                            parts.append(block.get('text', ''))
                            for annotation in block.get('annotations', []):
                                url = annotation.get('url', '')
                                if urlparse(url).scheme in {'https', 'http'}:
                                    sources[url] = {'url': url, 'title': annotation.get('title', '')}
                if not calls or not sources:
                    raise ValueError('Backend did not return a completed search with source URLs')
                self.failures[key] = 0
                return {'status': 'ok', 'query': query, 'summary': '\n'.join(parts)[:3000],
                        'results': list(sources.values())[:limit],
                        'note': 'Public references only; validate methods against the current target.'}
            except Exception as exc:
                self.failures[key] += 1
                reason = f'HTTP {exc.response.status_code}' if isinstance(exc, httpx.HTTPStatusError) else type(exc).__name__
                return {'status': 'unavailable', 'error': reason, 'failed_attempts': self.failures[key],
                        'stop_searching': self.failures[key] >= 2,
                        'instruction': 'Do not repeat failed searches. After two failures continue local analysis.'}
