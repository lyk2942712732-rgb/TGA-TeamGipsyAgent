import gzip
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, Request

from tga3.knowledge_relay import KnowledgeRelay


def application(handler, *, url="https://ctf42.online/mcp"):
    relay = KnowledgeRelay(SimpleNamespace(runtime=SimpleNamespace(ctf_knowledge_mcp_url=url)),
                           transport=httpx.MockTransport(handler))
    app = FastAPI()

    @app.api_route("/knowledge-mcp/", methods=["GET", "POST", "DELETE"])
    async def forward(request: Request):
        return await relay.forward(request)

    return relay, app


async def test_protocol_is_forwarded_but_credentials_task_headers_and_query_override_are_not():
    seen = []
    result = {"jsonrpc": "2.0", "id": 7, "result": {"tools": [{"name": "search_challenges"}]}}

    def upstream(request):
        seen.append(request)
        return httpx.Response(200, json=result, headers={"Mcp-Session-Id": "session-fixture"})

    relay, app = application(upstream)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            payload = {"jsonrpc": "2.0", "id": 7, "method": "tools/list"}
            reply = await client.post("/knowledge-mcp/?url=https://other.invalid/", json=payload, headers={
                "Authorization": "Bearer model-secret", "Cookie": "private-session=secret",
                "X-TGA3-Task-ID": "private-task", "X-TGA3-Agent-ID": "worker-openai",
                "MCP-Protocol-Version": "2025-03-26", "Mcp-Session-Id": "old-session",
                "Accept": "application/json, text/event-stream"})
        assert reply.json() == result and reply.headers["mcp-session-id"] == "session-fixture"
        assert str(seen[0].url) == "https://ctf42.online/mcp"
        assert json.loads(seen[0].content) == payload
        assert seen[0].headers["mcp-protocol-version"] == "2025-03-26"
        assert seen[0].headers["mcp-session-id"] == "old-session"
        assert not {"authorization", "cookie", "x-tga3-task-id", "x-tga3-agent-id"} & seen[0].headers.keys()
    finally:
        await relay.close()


@pytest.mark.parametrize("method,status", [("GET", 405), ("DELETE", 202), ("POST", 204)])
async def test_streamable_http_methods_and_status_codes_are_preserved(method, status):
    relay, app = application(lambda _: httpx.Response(status, headers={"Retry-After": "5"}))
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            reply = await client.request(method, "/knowledge-mcp/")
        assert reply.status_code == status and reply.headers["retry-after"] == "5"
    finally:
        await relay.close()


async def test_sse_and_compression_are_relayed_without_corrupt_length_or_encoding():
    body = b'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{}}\n\n'
    relay, app = application(lambda _: httpx.Response(200, content=gzip.compress(body), headers={
        "Content-Type": "text/event-stream", "Content-Encoding": "gzip"}))
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            reply = await client.post("/knowledge-mcp/", json={"method": "initialize"})
        assert reply.content == body
        assert reply.headers["content-type"] == "text/event-stream"
        assert "content-encoding" not in reply.headers and "content-length" not in reply.headers
    finally:
        await relay.close()


async def test_failure_is_bounded_and_does_not_return_secret_error_details():
    def fail(_):
        raise httpx.ConnectTimeout("secret=do-not-return")

    relay, app = application(fail)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            reply = await client.post("/knowledge-mcp/", json={"method": "initialize"})
        assert reply.status_code == 502 and reply.json()["reason"] == "ConnectTimeout"
        assert "secret" not in reply.text
    finally:
        await relay.close()


async def test_disabled_upstream_and_oversized_requests_make_no_network_call():
    def forbidden(_):
        raise AssertionError("No upstream call expected")

    relay, app = application(forbidden, url="")
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.post("/knowledge-mcp/", content=b"{}")).status_code == 503
            relay.config.runtime.ctf_knowledge_mcp_url = "https://ctf42.online/mcp"
            assert (await client.post("/knowledge-mcp/", content=b"x" * 131073)).status_code == 413
    finally:
        await relay.close()
