"""Lightweight local HTTP proxy for AOE Scout that trips on 404/405/403 path enumeration."""

from __future__ import annotations

import asyncio
import json
import os
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlsplit

import httpx

HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "proxy-connection",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}

MISS_STATUSES = {403, 404, 405}


def default_bypass_hosts(env: dict[str, str] | None = None) -> set[str]:
    source = env if env is not None else os.environ
    hosts = {"localhost", "127.0.0.1", "::1", "host.docker.internal"}
    for key in ("TGA3_BASE_URL", "TGA3_BLACKBOARD_MCP_URL", "TGA3_SCOUT_INTEL_MCP_URL", "TGA3_CONTROL_WS_URL"):
        raw = source.get(key, "").strip()
        if not raw:
            continue
        parsed = urlsplit(raw)
        if parsed.hostname:
            hosts.add(parsed.hostname.lower())
    return hosts


def build_no_proxy(existing: str = "", bypass_hosts: set[str] | None = None) -> str:
    entries = [item.strip() for item in existing.split(",") if item.strip()]
    seen = {item.lower() for item in entries}
    for host in sorted(bypass_hosts or default_bypass_hosts()):
        if host.lower() not in seen:
            entries.append(host)
            seen.add(host.lower())
    return ",".join(entries)


def _normalize_path(url: str) -> tuple[str, str]:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    if parsed.port:
        host = f"{host}:{parsed.port}"
    path = parsed.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    return host, path


class ScoutProbeGuard:
    """Trips on repeated distinct-path 404/405/403 misses on the same host without throttling 2xx traffic."""

    def __init__(
        self,
        *,
        max_misses: int = 6,
        bypass_hosts: set[str] | None = None,
        state_path: Path | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.max_misses = max(2, int(max_misses))
        self.bypass_hosts = {h.lower() for h in (bypass_hosts if bypass_hosts is not None else default_bypass_hosts())}
        self.state_path = state_path
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(follow_redirects=False, timeout=30.0, trust_env=False)
        self.known_good: defaultdict[str, set[str]] = defaultdict(set)
        self.misses: defaultdict[str, set[str]] = defaultdict(set)
        self.tripped_hosts: set[str] = set()
        self.server: asyncio.AbstractServer | None = None
        self.url: str = ""

    def _is_bypassed(self, url: str) -> bool:
        hostname = (urlsplit(url).hostname or "").lower()
        return not hostname or hostname in self.bypass_hosts

    def _persist_state(self) -> None:
        if self.state_path is None:
            return
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "max_misses": self.max_misses,
                "tripped_hosts": sorted(self.tripped_hosts),
                "miss_counts": {host: len(paths) for host, paths in self.misses.items()},
            }
            temporary = self.state_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            temporary.replace(self.state_path)
        except OSError:
            pass

    def should_block(self, url: str) -> tuple[bool, str]:
        if self._is_bypassed(url):
            return False, ""
        host, path = _normalize_path(url)
        if host in self.tripped_hosts and path not in self.known_good[host]:
            return True, (
                f"Probe guard tripped on {host} after {len(self.misses[host])} distinct 403/404/405 paths. "
                "Stop guessing undocumented URLs; leave unavailable attachments/fields empty, record why in "
                "collection_notes, import staged questions and call scout_finish_collection."
            )
        return False, ""

    def record_response(self, url: str, status_code: int) -> None:
        if self._is_bypassed(url):
            return
        host, path = _normalize_path(url)
        if 200 <= status_code < 400:
            self.known_good[host].add(path)
            return
        if status_code in MISS_STATUSES and path not in self.known_good[host]:
            self.misses[host].add(path)
            if len(self.misses[host]) >= self.max_misses:
                self.tripped_hosts.add(host)
            self._persist_state()

    async def start(self) -> str:
        self.server = await asyncio.start_server(self._handle_client, "127.0.0.1", 0)
        sock = self.server.sockets[0]
        port = sock.getsockname()[1]
        self.url = f"http://127.0.0.1:{port}"
        return self.url

    async def stop(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
            self.server = None
        if self._owns_client:
            await self.client.aclose()

    @staticmethod
    async def _send_response(
        writer: asyncio.StreamWriter, status: int, reason: str, headers: dict[str, str], body: bytes
    ) -> None:
        lines = [f"HTTP/1.1 {status} {reason}"]
        cleaned = {k: v for k, v in headers.items() if k.lower() not in HOP_BY_HOP and k.lower() != "content-length"}
        cleaned["Content-Length"] = str(len(body))
        cleaned["Connection"] = "close"
        for key, value in cleaned.items():
            lines.append(f"{key}: {value}")
        head = ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1", errors="replace")
        writer.write(head + body)
        await writer.drain()

    async def _handle_connect(self, target: str, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        host, _, port_text = target.rpartition(":")
        port = int(port_text) if port_text.isdigit() else 443
        try:
            remote_reader, remote_writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=15.0)
        except Exception as exc:
            await self._send_response(
                writer, 502, "Bad Gateway", {"Content-Type": "text/plain"}, f"CONNECT failed: {exc}".encode()
            )
            return
        writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await writer.drain()

        async def pipe(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
            try:
                while chunk := await src.read(16384):
                    dst.write(chunk)
                    await dst.drain()
            except Exception:
                pass
            finally:
                try:
                    dst.close()
                except Exception:
                    pass

        await asyncio.gather(pipe(reader, remote_writer), pipe(remote_reader, writer), return_exceptions=True)

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            raw_head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=15.0)
        except Exception:
            writer.close()
            return
        try:
            header_lines = raw_head.decode("latin-1", errors="replace").split("\r\n")
            if not header_lines or not header_lines[0]:
                return
            parts = header_lines[0].split(" ", 2)
            if len(parts) < 2:
                await self._send_response(writer, 400, "Bad Request", {}, b"Malformed HTTP request")
                return
            method, target = parts[0].upper(), parts[1]
            headers: dict[str, str] = {}
            for line in header_lines[1:]:
                if ":" in line:
                    key, value = line.split(":", 1)
                    headers[key.strip()] = value.strip()
            if method == "CONNECT":
                await self._handle_connect(target, reader, writer)
                return
            if not target.startswith(("http://", "https://")):
                host_header = headers.get("Host") or headers.get("host") or ""
                if not host_header:
                    await self._send_response(writer, 400, "Bad Request", {}, b"Missing Host header")
                    return
                target = f"http://{host_header}{target}"
            content_length = int(headers.get("Content-Length") or headers.get("content-length") or 0)
            body = (
                await asyncio.wait_for(reader.readexactly(content_length), timeout=30.0)
                if content_length > 0
                else b""
            )
            blocked, message = self.should_block(target)
            if blocked:
                payload = json.dumps(
                    {"error": "probe_guard_tripped", "message": message}, ensure_ascii=False
                ).encode()
                await self._send_response(
                    writer,
                    429,
                    "Too Many Requests",
                    {"Content-Type": "application/json; charset=utf-8"},
                    payload,
                )
                return
            forward_headers = {k: v for k, v in headers.items() if k.lower() not in HOP_BY_HOP and k.lower() != "host"}
            try:
                response = await self.client.request(method, target, headers=forward_headers, content=body)
            except Exception as exc:
                await self._send_response(
                    writer,
                    502,
                    "Bad Gateway",
                    {"Content-Type": "text/plain; charset=utf-8"},
                    f"Upstream request failed: {exc}".encode(),
                )
                return
            self.record_response(target, response.status_code)
            resp_headers = {k: v for k, v in response.headers.items() if k.lower() not in HOP_BY_HOP}
            await self._send_response(
                writer,
                response.status_code,
                response.reason_phrase or "OK",
                resp_headers,
                response.content,
            )
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass


__all__ = ["ScoutProbeGuard", "build_no_proxy", "default_bypass_hosts"]
