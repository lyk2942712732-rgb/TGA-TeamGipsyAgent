"""Scoped HTTP executor for LLM-planned CTF actions."""

from __future__ import annotations

import json
import re
from http.cookiejar import CookieJar
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlparse, urlunparse
from urllib.request import HTTPCookieProcessor, Request, build_opener

from tga.agent.http_action_planner import HTTPAction
from tga.contracts import ArtifactRecord, Intent, TGATask, WorkerResult
from tga.core.scope import is_in_scope
from tga.ctf.web_observer import analyze_html
from tga.evidence.artifacts import ArtifactStore


BLOCKED_HEADERS = {
    "host",
    "content-length",
    "transfer-encoding",
    "connection",
    "proxy-authorization",
    "proxy-authenticate",
}


def execute_http_actions(
    *,
    task: TGATask,
    intent: Intent,
    artifact_store: ArtifactStore,
    actions: list[HTTPAction],
    plan_meta: dict | None = None,
    timeout_s: int = 12,
) -> WorkerResult:
    opener = build_opener(HTTPCookieProcessor(CookieJar()))
    records = []
    facts = []
    leads = []

    for index, action in enumerate(actions, start=1):
        url = _action_url(task.target, action)
        if not url or not is_in_scope(url, task.scope):
            leads.append(f"skipped out-of-scope action {index}: {url or action.path or action.url}")
            continue
        method = action.method.upper()
        if method not in {"GET", "POST"}:
            leads.append(f"skipped unsupported method {method} for {url}")
            continue
        headers = _safe_headers(action.headers)
        record = _fetch(
            opener=opener,
            url=url,
            method=method,
            headers=headers,
            body=action.body,
            timeout_s=timeout_s,
        )
        record["rationale"] = action.rationale
        records.append(record)
        if record.get("error"):
            leads.append(f"{method} {url} failed: {record['error']}")
        else:
            facts.append(f"{method} {url} -> HTTP {record['status']}")

    artifact = _save_artifact(
        artifact_store=artifact_store,
        task=task,
        intent=intent,
        actions=actions,
        records=records,
        plan_meta=plan_meta or {},
    )
    combined_text = "\n".join(str(record.get("text") or "") for record in records)
    flags = _extract_flags(combined_text, task.flag_format)
    errors = ["NO_FLAG_FOUND"] if not flags else []
    if not records:
        errors.append("NO_HTTP_ACTIONS_EXECUTED")
    return WorkerResult(
        task_id=task.id,
        intent_id=intent.id,
        status="ok" if records else "blocked",
        artifacts=[artifact],
        facts=facts,
        leads=leads,
        flags=flags,
        errors=errors,
    )


def _action_url(target: str, action: HTTPAction) -> str:
    base = _base_url(target)
    raw = action.url or action.path or ""
    if not raw:
        return ""
    return urljoin(base, raw)


def _base_url(target: str) -> str:
    parsed = urlparse(target if "://" in target else f"http://{target}")
    return urlunparse((parsed.scheme or "http", parsed.netloc, "", "", "", ""))


def _safe_headers(headers: dict[str, str]) -> dict[str, str]:
    result = {"User-Agent": "TGA-LLM-CTF-Agent/0.1"}
    for key, value in headers.items():
        name = str(key).strip()
        if not name or name.lower() in BLOCKED_HEADERS:
            continue
        clean_value = str(value).replace("\r", " ").replace("\n", " ").strip()
        if clean_value:
            result[name] = clean_value[:2000]
    return result


def _fetch(
    *,
    opener,
    url: str,
    method: str,
    headers: dict[str, str],
    body: str | dict[str, str] | None,
    timeout_s: int,
) -> dict:
    data = None
    body_text = None
    request_headers = dict(headers)
    if method == "POST":
        if isinstance(body, dict):
            body_text = urlencode(body)
            request_headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
        elif body is not None:
            body_text = str(body)
            request_headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
        data = body_text.encode("utf-8") if body_text is not None else b""
    request = Request(url, data=data, headers=request_headers, method=method)
    try:
        with opener.open(request, timeout=timeout_s) as response:
            raw = response.read(1_000_000)
            text = raw.decode("utf-8", errors="replace")
            content_type = response.headers.get("Content-Type", "")
            return {
                "url": url,
                "method": method,
                "headers": request_headers,
                "body": body_text,
                "status": int(response.status),
                "error": None,
                "content_type": content_type,
                "text": text,
                "page": analyze_html(url=url, text=text, content_type=content_type),
            }
    except HTTPError as exc:
        text = exc.read(1_000_000).decode("utf-8", errors="replace")
        content_type = exc.headers.get("Content-Type", "") if exc.headers else ""
        return {
            "url": url,
            "method": method,
            "headers": request_headers,
            "body": body_text,
            "status": int(exc.code),
            "error": None,
            "content_type": content_type,
            "text": text,
            "page": analyze_html(url=url, text=text, content_type=content_type),
        }
    except (URLError, TimeoutError, OSError) as exc:
        return {
            "url": url,
            "method": method,
            "headers": request_headers,
            "body": body_text,
            "status": 0,
            "error": str(exc),
            "text": "",
        }


def _save_artifact(
    *,
    artifact_store: ArtifactStore,
    task: TGATask,
    intent: Intent,
    actions: list[HTTPAction],
    records: list[dict],
    plan_meta: dict,
) -> ArtifactRecord:
    payload = {
        "task_id": task.id,
        "intent_id": intent.id,
        "strategy": "llm_planned_http_actions",
        "plan": plan_meta,
        "actions": [action.model_dump() for action in actions],
        "responses": [
            {
                **record,
                "text": str(record.get("text") or "")[:80_000],
            }
            for record in records
        ],
    }
    return artifact_store.save_text(
        task_id=task.id,
        intent_id=intent.id,
        kind="http_response",
        text=json.dumps(payload, ensure_ascii=False, indent=2),
        tool="llm-http-agent",
        target=task.target,
        suffix=".json",
    )


def _extract_flags(text: str, flag_format: str | None) -> list[str]:
    result = []
    seen = set()
    pattern_texts = [flag_format or r"flag\{[^}]+\}"]
    if not flag_format or flag_format in {r"flag\{[^}]+\}", r"FLAG\{[^}]+\}"}:
        pattern_texts.append(r"[A-Za-z0-9_]{2,32}\{[^{}\s]{4,200}\}")
    for pattern_text in pattern_texts:
        try:
            pattern = re.compile(pattern_text)
        except re.error:
            pattern = re.compile(r"flag\{[^}]+\}")
        for match in pattern.finditer(text):
            value = match.group(0)
            if value not in seen:
                result.append(value)
                seen.add(value)
    return result
