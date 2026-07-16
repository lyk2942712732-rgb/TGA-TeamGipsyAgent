from __future__ import annotations

import hashlib
import json
import re
import time
from http.cookiejar import CookieJar
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse
from urllib.request import HTTPCookieProcessor, HTTPRedirectHandler, Request, build_opener

from tga.contracts import ActionSpec, TGATask
from tga.core.scope import is_in_scope
from tga.ctf.web_observer import analyze_html

from .schemas import HTTPRequestArguments
from .serializers import output_excerpt, redact_headers


BLOCKED_HEADERS = {"host", "content-length", "transfer-encoding", "connection", "proxy-authorization"}
FLAG_RE = re.compile(r"[A-Za-z0-9_]{2,32}\{[^{}\s]{4,200}\}")


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def execute_http(
    *, task: TGATask, action: ActionSpec, args: HTTPRequestArguments, max_output_bytes: int = 262_144
) -> tuple[dict, bytes, list[str], list[str]]:
    url = _resolve_url(task.target, args)
    if not is_in_scope(url, task.scope):
        raise PermissionError("OUT_OF_SCOPE")
    if args.method not in {"GET", "POST"} and (action.risk != "active" or not task.allow_active_scan):
        raise PermissionError("ACTIVE_HTTP_METHOD_NOT_ALLOWED")

    headers = _safe_headers(args.headers)
    data, request_headers = _encode_body(args.body, headers, args.method)
    opener = build_opener(_NoRedirect(), HTTPCookieProcessor(CookieJar()))
    redirects: list[dict] = []
    started = time.monotonic()
    current = url
    for _ in range(6):
        response = _request(opener, current, args.method, request_headers, data, args.timeout, max_output_bytes)
        status = response["status"]
        location = response["response_headers"].get("Location") or response["response_headers"].get("location")
        if status in {301, 302, 303, 307, 308} and location:
            next_url = urljoin(current, location)
            redirects.append({"status": status, "from": current, "to": next_url})
            if not is_in_scope(next_url, task.scope):
                raise PermissionError("REDIRECT_OUT_OF_SCOPE")
            current = next_url
            if status == 303:
                data = None
                request_headers.pop("Content-Type", None)
            continue
        break
    else:
        raise RuntimeError("REDIRECT_LIMIT_EXCEEDED")

    raw = response.pop("raw")
    excerpt, truncated = output_excerpt(raw, max_output_bytes)
    truncated = truncated or bool(response.get("output_limited"))
    content_type = response["content_type"]
    page = analyze_html(url=current, text=excerpt, content_type=content_type)
    payload = {
        "capability": "http.request",
        "semantic_fingerprint": semantic_fingerprint(action=action, args=args, url=url),
        "method": args.method,
        "requested_url": url,
        "final_url": current,
        "redirect_chain": redirects,
        "request_headers": redact_headers(request_headers),
        "status": response["status"],
        "response_headers": redact_headers(response["response_headers"]),
        "content_type": content_type,
        "body_excerpt": excerpt,
        "body_bytes": len(raw),
        "truncated": truncated,
        "duration_ms": round((time.monotonic() - started) * 1000),
        "page": page,
        "error": response.get("error"),
    }
    facts = [] if response.get("error") else [f"{args.method} {current} -> HTTP {response['status']}"]
    leads = _leads(page)
    return payload, raw, facts, leads


def semantic_fingerprint(*, action: ActionSpec, args: HTTPRequestArguments, url: str) -> str:
    parsed = urlparse(url)
    body_schema: object
    if isinstance(args.body, dict):
        body_schema = sorted(args.body.keys())
    elif isinstance(args.body, list):
        body_schema = ["list"]
    elif args.body is None:
        body_schema = None
    else:
        body_schema = type(args.body).__name__
    normalized = {
        "method": args.method,
        "path": parsed.path or "/",
        "query_names": sorted({key for key, _ in parse_qsl(parsed.query, keep_blank_values=True)} | set(args.query)),
        "body_schema": body_schema,
        "target": f"{parsed.scheme}://{parsed.netloc}",
        "hypothesis_id": action.hypothesis_id,
    }
    return hashlib.sha256(json.dumps(normalized, sort_keys=True).encode()).hexdigest()[:24]


def _resolve_url(target: str, args: HTTPRequestArguments) -> str:
    base = target if "://" in target else f"http://{target}"
    raw = args.url or args.path or ""
    url = urljoin(base.rstrip("/") + "/", raw)
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("invalid HTTP URL")
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.update({key: "" if value is None else str(value) for key, value in args.query.items()})
    return urlunparse(parsed._replace(query=urlencode(query)))


def _safe_headers(headers: dict[str, str]) -> dict[str, str]:
    result = {"User-Agent": "TGA-Capability-Runtime/2"}
    for key, value in headers.items():
        name = str(key).strip()
        if not name or name.lower() in BLOCKED_HEADERS:
            continue
        clean = str(value).replace("\r", " ").replace("\n", " ").strip()
        if clean:
            result[name] = clean[:2000]
    return result


def _encode_body(body: object, headers: dict[str, str], method: str) -> tuple[bytes | None, dict[str, str]]:
    result = dict(headers)
    if body is None or method == "GET":
        return None, result
    if isinstance(body, (dict, list)):
        if result.get("Content-Type", "").lower().startswith("application/x-www-form-urlencoded") and isinstance(body, dict):
            text = urlencode({key: str(value) for key, value in body.items()})
        else:
            result.setdefault("Content-Type", "application/json")
            text = json.dumps(body, ensure_ascii=False)
    else:
        text = str(body)
        result.setdefault("Content-Type", "text/plain; charset=utf-8")
    return text.encode("utf-8"), result


def _request(
    opener, url: str, method: str, headers: dict[str, str], data: bytes | None, timeout: int, max_output_bytes: int
) -> dict:  # type: ignore[no-untyped-def]
    request = Request(url, data=data, headers=headers, method=method)
    try:
        with opener.open(request, timeout=timeout) as response:
            raw = response.read(max_output_bytes + 1)
            return {"status": int(response.status), "response_headers": dict(response.headers.items()), "content_type": response.headers.get("Content-Type", ""), "raw": raw[:max_output_bytes], "output_limited": len(raw) > max_output_bytes, "error": None}
    except HTTPError as exc:
        raw = exc.read(max_output_bytes + 1)
        return {"status": int(exc.code), "response_headers": dict(exc.headers.items()) if exc.headers else {}, "content_type": exc.headers.get("Content-Type", "") if exc.headers else "", "raw": raw[:max_output_bytes], "output_limited": len(raw) > max_output_bytes, "error": None}
    except (URLError, TimeoutError, OSError) as exc:
        return {"status": 0, "response_headers": {}, "content_type": "", "raw": b"", "output_limited": False, "error": str(exc)}


def _leads(page: dict) -> list[str]:
    leads: list[str] = []
    for link in page.get("links", [])[:8]:
        leads.append(f"observed link: {link}")
    for hint in page.get("api_hints", [])[:5]:
        leads.append(f"observed API hint: {hint}")
    for hint in page.get("websocket_hints", [])[:3]:
        leads.append(f"observed WebSocket hint: {hint}")
    return leads


def extract_candidate_flags(raw: bytes, flag_format: str | None) -> list[str]:
    text = raw.decode("utf-8", errors="replace")
    patterns = [re.compile(flag_format)] if flag_format else [FLAG_RE]
    values: list[str] = []
    for pattern in patterns:
        values.extend(match.group(0) for match in pattern.finditer(text))
    return list(dict.fromkeys(values))
