"""Fixed-upstream MCP relay for bridge containers; no model calls or local index."""

from __future__ import annotations

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse, StreamingResponse


class KnowledgeRelay:
    def __init__(self, config, *, transport=None):
        self.config = config
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(30, connect=10), trust_env=False, follow_redirects=False,
            # ctf42 currently returns finite JSON/405 responses, not a persistent
            # GET stream. Queue native discovery requests on the same connection
            # instead of racing new connections through the host's proxy route.
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=1, keepalive_expiry=120),
            transport=transport,
        )

    async def forward(self, request: Request):
        url = self.config.runtime.ctf_knowledge_mcp_url.strip()
        if not url:
            return JSONResponse({"error": "knowledge_disabled"}, status_code=503)
        # Do not forward task IDs, model keys, authorization, cookies or Host.
        allowed = {"accept", "content-type", "mcp-protocol-version", "mcp-session-id", "last-event-id"}
        headers = {k: v for k, v in request.headers.items() if k.lower() in allowed}
        content = await request.body()
        if len(content) > 131072:
            return JSONResponse({"error": "knowledge_request_too_large"}, status_code=413)
        try:
            upstream = await self.client.send(
                self.client.build_request(request.method, url, headers=headers, content=content), stream=True)
        except httpx.HTTPError as exc:
            return JSONResponse({"error": "knowledge_unavailable", "reason": type(exc).__name__}, status_code=502)
        response_headers = {k: v for k, v in upstream.headers.items() if k.lower() in {
            "content-type", "mcp-session-id", "mcp-protocol-version", "cache-control", "retry-after"}}

        async def body():
            try:
                # Decode upstream compression; do not forward Content-Length/Encoding.
                async for chunk in upstream.aiter_bytes():
                    yield chunk
            finally:
                await upstream.aclose()

        return StreamingResponse(body(), status_code=upstream.status_code, headers=response_headers)

    async def close(self):
        await self.client.aclose()
