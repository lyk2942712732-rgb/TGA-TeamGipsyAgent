"""LLM-driven HTTP action planning for scoped CTF follow-up work."""

from __future__ import annotations

import json
import hashlib
import hmac
import re
import time
from typing import Any, Literal
from urllib.parse import parse_qsl, urlencode, urlparse

from pydantic import BaseModel, Field, field_validator

from tga.contracts import TGATask
from tga.models.base import ModelMessage
from tga.models.bootstrap import build_model_client_from_env


class HTTPAction(BaseModel):
    method: Literal["GET", "POST"] = "GET"
    path: str | None = None
    url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    body: str | dict[str, str] | None = None
    rationale: str = ""

    @field_validator("method", mode="before")
    @classmethod
    def normalize_method(cls, value: Any) -> str:
        return str(value or "GET").upper()


class ToolAction(BaseModel):
    tool: str
    target: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    rationale: str = ""


def plan_http_actions(
    *,
    task: TGATask,
    instruction: str,
    snapshot: dict,
    max_actions: int = 6,
) -> tuple[list[HTTPAction], dict]:
    """Ask the configured model for concrete in-scope HTTP requests.

    The model plans, but does not execute. Execution still goes through a
    scoped HTTP runner that only supports a tiny GET/POST surface.
    """
    client = build_model_client_from_env()
    fallback_actions = _actions_from_instruction(
        task=task,
        instruction=instruction,
        snapshot=snapshot,
        max_actions=max_actions,
    )
    fallback_tool_actions = _tool_actions_from_instruction(task=task, instruction=instruction, snapshot=snapshot)
    if client is None:
        return fallback_actions, {
            "enabled": False,
            "used": bool(fallback_actions or fallback_tool_actions),
            "reason": "EXPLICIT_REQUEST_FALLBACK" if fallback_actions or fallback_tool_actions else "LLM_NOT_CONFIGURED",
            "actions": [action.model_dump() for action in fallback_actions],
            "tool_actions": [action.model_dump() for action in fallback_tool_actions],
        }

    prompt = _build_prompt(task=task, instruction=instruction, snapshot=snapshot, max_actions=max_actions)
    try:
        response = client.chat([ModelMessage(role="user", content=prompt)], temperature=0.1)
        payload = json.loads(_json_object(response.content))
    except Exception as exc:  # noqa: BLE001
        if fallback_actions or fallback_tool_actions:
            return fallback_actions, {
                "enabled": True,
                "used": True,
                "reason": "EXPLICIT_REQUEST_FALLBACK",
                "summary": "LLM did not return valid action JSON, so TGA extracted explicit HTTP requests from the user instruction.",
                "llm_error": f"LLM_HTTP_PLAN_FAILED: {exc}",
                "actions": [action.model_dump() for action in fallback_actions],
                "tool_actions": [action.model_dump() for action in fallback_tool_actions],
            }
        return [], {"enabled": True, "used": False, "reason": f"LLM_HTTP_PLAN_FAILED: {exc}"}

    status = str(payload.get("status") or "continue").lower() if isinstance(payload, dict) else "continue"
    raw_actions = payload.get("actions") or payload.get("requests") if isinstance(payload, dict) else None
    tool_actions, rejected_tools = _tool_actions_from_payload(payload)
    if not tool_actions and fallback_tool_actions:
        tool_actions = fallback_tool_actions
    if status in {"done", "blocked", "wait"} and not raw_actions and not tool_actions:
        return [], {
            "enabled": True,
            "used": False,
            "reason": f"LLM_DECISION_{status.upper()}",
            **_decision_meta(payload),
        }
    if not isinstance(raw_actions, list):
        if fallback_actions:
            return fallback_actions, {
                "enabled": True,
                "used": True,
                "reason": "LLM_PLAN_UNUSABLE_FALLBACK",
                "summary": "The model did not return executable action JSON, so TGA used only explicit requests found in the prompt as a fallback.",
                "raw": _truncate(response.content),
                "actions": [action.model_dump() for action in fallback_actions],
                "tool_actions": [action.model_dump() for action in tool_actions],
                "rejected_tools": rejected_tools,
            }
        if tool_actions:
            return [], {
                "enabled": True,
                "used": True,
                "reason": "LLM_TOOL_PLAN_CREATED",
                **_decision_meta(payload),
                "tool_actions": [action.model_dump() for action in tool_actions],
                "rejected_tools": rejected_tools,
            }
        return [], {
            "enabled": True,
            "used": False,
            "reason": "LLM_HTTP_PLAN_INVALID",
            "raw": _truncate(response.content),
        }

    actions: list[HTTPAction] = []
    rejected: list[str] = []
    for raw in raw_actions[:max_actions]:
        if not isinstance(raw, dict):
            rejected.append("non_object_action")
            continue
        try:
            action = HTTPAction.model_validate(raw)
        except Exception as exc:  # noqa: BLE001
            rejected.append(f"invalid_action: {exc}")
            continue
        if not action.path and not action.url:
            rejected.append("missing_path_or_url")
            continue
        actions.append(action)

    if not actions and fallback_actions:
        return fallback_actions, {
            "enabled": True,
            "used": True,
            "reason": "LLM_EMPTY_ACTIONS_FALLBACK",
            "summary": "The model returned no usable actions, so TGA used only explicit requests found in the prompt as a fallback.",
            "actions": [action.model_dump() for action in fallback_actions],
            "tool_actions": [action.model_dump() for action in tool_actions],
            "rejected": rejected,
            "rejected_tools": rejected_tools,
            **_decision_meta(payload),
        }

    return actions, {
        "enabled": True,
        "used": bool(actions),
        "reason": "LLM_HTTP_PLAN_CREATED" if actions else "LLM_HTTP_PLAN_EMPTY",
        **_decision_meta(payload),
        "actions": [action.model_dump() for action in actions],
        "tool_actions": [action.model_dump() for action in tool_actions],
        "rejected": rejected,
        "rejected_tools": rejected_tools,
    }


def _build_prompt(*, task: TGATask, instruction: str, snapshot: dict, max_actions: int) -> str:
    evidence = _evidence_digest(snapshot)
    return (
        "You are the planning brain for an authorized CTF web agent. "
        "Return only JSON. You are not a path generator: first judge the current evidence, "
        "then decide whether to continue, stop, or ask for missing information. "
        "Do not claim a flag unless it appears in evidence. "
        "Plan concrete HTTP requests and, when needed, MCP/security-tool actions that stay within the authorized target and scope. "
        "For HTTP actions, allowed methods are GET and POST only. You may set useful CTF headers such as "
        "X-Forwarded-For, Cookie, Referer, User-Agent, Timestamp, Signature, Token, or custom challenge headers. "
        "Use the latest HTTP response bodies, errors, status codes, prior assistant/user messages, "
        "and rejected/repeated actions as observations for self-correction. "
        "Use web affordances as hard evidence: if a form says method=POST and field=query, "
        "place the payload in POST body {'query': ...}; do not move it to URL query unless evidence shows a GET form or URL parameter. "
        "If the user supplies a target URL, payload, source code detail, encoding clue, or failed result, "
        "treat it as evidence and adapt instead of repeating canned discovery. "
        "If the user asks to use a tool such as sqlmap, ffuf, whatweb, nuclei, or nmap, emit tool_actions instead of pretending it is an HTTP request. "
        "Do not request other hosts, do not use destructive actions, and do not output raw shell commands.\n\n"
        "JSON schema:\n"
        "{\n"
        '  "status": "continue|blocked|done",\n'
        '  "observations": ["facts from evidence that changed the plan"],\n'
        '  "hypothesis": "brief testable interpretation of the challenge state",\n'
        '  "summary": "why these requests are next",\n'
        '  "actions": [\n'
        '    {"method":"GET","path":"/robots.txt","headers":{},"rationale":"check hidden route"},\n'
        '    {"method":"POST","path":"/check","headers":{"X-Forwarded-For":"127.0.0.1"},"body":{"token":"..."},"rationale":"try bypass"}\n'
        "  ],\n"
        '  "tool_actions": [\n'
        '    {"tool":"sqlmap","target":"http://host/","args":{"mcp_tool":"sql_scan","target":"http://host/","data":"query=1","level":1,"risk":1},"rationale":"use sqlmap MCP on observed POST parameter"}\n'
        "  ]\n"
        "}\n\n"
        f"Target: {task.target}\n"
        f"Scope: {task.scope}\n"
        f"Target theme: {task.target_theme or 'not provided'}\n"
        f"Target description: {task.target_description or 'not provided'}\n"
        f"Task goal: {task.goal}\n"
        f"User instruction for this round: {instruction}\n"
        f"Flag format: {task.flag_format or 'not configured'}\n"
        f"Maximum actions: {max_actions}\n"
        f"Evidence so far:\n{json.dumps(evidence, ensure_ascii=False, indent=2)}"
    )


def _decision_meta(payload: dict | None) -> dict:
    if not isinstance(payload, dict):
        return {"summary": ""}
    observations = payload.get("observations") or payload.get("evidence") or []
    if isinstance(observations, str):
        observations = [observations]
    if not isinstance(observations, list):
        observations = []
    return {
        "status": str(payload.get("status") or "continue")[:80],
        "observations": [str(item)[:500] for item in observations[:8]],
        "hypothesis": str(payload.get("hypothesis") or payload.get("theory") or "")[:1000],
        "summary": str(payload.get("summary") or payload.get("rationale") or "")[:1000],
    }


def _tool_actions_from_payload(payload: dict | None) -> tuple[list[ToolAction], list[str]]:
    if not isinstance(payload, dict):
        return [], []
    raw_actions = payload.get("tool_actions") or payload.get("tools") or []
    if not isinstance(raw_actions, list):
        return [], ["tool_actions_not_list"]
    actions: list[ToolAction] = []
    rejected: list[str] = []
    for raw in raw_actions[:4]:
        if not isinstance(raw, dict):
            rejected.append("non_object_tool_action")
            continue
        try:
            action = ToolAction.model_validate(raw)
        except Exception as exc:  # noqa: BLE001
            rejected.append(f"invalid_tool_action: {exc}")
            continue
        if not action.tool:
            rejected.append("missing_tool")
            continue
        actions.append(action)
    return actions, rejected


def _evidence_digest(snapshot: dict) -> dict:
    flags = snapshot.get("flags") or []
    events = []
    for event in (snapshot.get("events") or [])[-16:]:
        payload = event.get("payload") or {}
        events.append(
            {
                "type": event.get("type"),
                "summary": payload.get("summary")
                or payload.get("reason")
                or payload.get("status")
                or payload.get("content")
                or payload.get("action")
                or "",
            }
        )

    responses = []
    web_affordances = []
    for item in snapshot.get("evidence_summary") or []:
        for response in item.get("responses") or []:
            forms = response.get("forms") or []
            for form in forms:
                web_affordances.append(
                    {
                        "source_url": response.get("url"),
                        "method": form.get("method") or "GET",
                        "action": form.get("action"),
                        "fields": [field.get("name") for field in form.get("fields") or [] if field.get("name")],
                    }
                )
            responses.append(
                {
                    "method": response.get("method") or "GET",
                    "url": response.get("url"),
                    "status": response.get("status"),
                    "body": response.get("body"),
                    "error": response.get("error"),
                    "forms": forms,
                    "interaction_notes": response.get("interaction_notes") or [],
                    "excerpt": response.get("excerpt"),
                }
            )

    return {
        "confirmed_flags": flags,
        "recent_events": events,
        "web_affordances": web_affordances[-12:],
        "recent_http_responses": responses[-24:],
    }


def _actions_from_instruction(
    *,
    task: TGATask,
    instruction: str,
    snapshot: dict,
    max_actions: int,
) -> list[HTTPAction]:
    text = instruction.replace("\r", "\n")
    headers = _headers_from_text(text)
    candidates: list[HTTPAction] = []

    for url in _full_url_candidates(text):
        candidates.append(
            HTTPAction(
                method="GET",
                url=url,
                headers=headers,
                rationale="extracted full URL from user instruction",
            )
        )

    for method, raw_target in _explicit_method_targets(text):
        candidates.append(
            HTTPAction(
                method=method,
                path=_target_to_path(task=task, raw_target=raw_target),
                url=raw_target if raw_target.startswith(("http://", "https://")) else None,
                headers=headers,
                rationale="extracted explicit HTTP request from user instruction",
            )
        )

    paths = _path_candidates(text)
    queries = _query_candidates(text)
    payloads = _payload_candidates(text)
    form_actions = _actions_from_forms(snapshot=snapshot, payloads=payloads, headers=headers, max_actions=max_actions)
    if form_actions:
        candidates.extend(form_actions)
    if not paths:
        paths = _recent_paths(snapshot)
    if paths:
        if queries:
            for path in paths[:2]:
                for query in queries[:2]:
                    candidates.append(
                        HTTPAction(
                            method="GET",
                            path=_join_path_query(path, query),
                            headers=headers,
                            rationale="extracted path, query, and headers from user instruction",
                        )
                    )
        else:
            for path in paths[:max_actions]:
                candidates.append(
                    HTTPAction(
                        method="GET",
                        path=path,
                        headers=headers,
                        rationale="extracted path and headers from user instruction",
                    )
                )

    return _dedupe_actions(candidates)[:max_actions]


def _tool_actions_from_instruction(*, task: TGATask, instruction: str, snapshot: dict) -> list[ToolAction]:
    lowered = instruction.lower()
    actions: list[ToolAction] = []
    if "sqlmap" in lowered:
        args = _sqlmap_args_from_context(task=task, instruction=instruction, snapshot=snapshot)
        actions.append(
            ToolAction(
                tool="sqlmap",
                target=task.target,
                args=args,
                rationale="user requested sqlmap; run sqlmap MCP through policy-gated tool runner",
            )
        )
    return actions


def _sqlmap_args_from_context(*, task: TGATask, instruction: str, snapshot: dict) -> dict[str, Any]:
    args: dict[str, Any] = {
        "mcp_tool": "sql_scan",
        "target": task.target,
        "level": 1,
        "risk": 1,
        "timeout_seconds": 300,
    }
    payloads = _payload_candidates(instruction)
    if not payloads:
        for item in snapshot.get("evidence_summary") or []:
            for response in item.get("responses") or []:
                body = response.get("body")
                if body:
                    args["data"] = str(body)
                    return args
    for form in _form_affordances(snapshot):
        if str(form.get("method") or "").upper() != "POST":
            continue
        fields = [str(name) for name in form.get("fields") or [] if name]
        if fields:
            field = _best_payload_field(fields)
            value = payloads[0] if payloads else "1"
            args["target"] = str(form.get("action") or task.target)
            args["data"] = urlencode({field: value})
            args["params"] = field
            return args
    if payloads:
        args["data"] = payloads[0]
    return args


def _actions_from_forms(
    *,
    snapshot: dict,
    payloads: list[str],
    headers: dict[str, str],
    max_actions: int,
) -> list[HTTPAction]:
    if not payloads:
        return []
    actions: list[HTTPAction] = []
    for form in _form_affordances(snapshot):
        method = str(form.get("method") or "GET").upper()
        action_url = str(form.get("action") or "")
        field_names = [str(name) for name in form.get("fields") or [] if name]
        if not field_names:
            continue
        field = _best_payload_field(field_names)
        for payload in payloads[:2]:
            if method == "POST":
                actions.append(
                    HTTPAction(
                        method="POST",
                        url=action_url,
                        headers=headers,
                        body={field: payload},
                        rationale="placed user payload into observed POST form field",
                    )
                )
            else:
                query = urlencode({field: payload})
                separator = "&" if "?" in action_url else "?"
                actions.append(
                    HTTPAction(
                        method="GET",
                        url=f"{action_url}{separator}{query}",
                        headers=headers,
                        rationale="placed user payload into observed GET form field",
                    )
                )
            if len(actions) >= max_actions:
                return actions
    return actions


def _form_affordances(snapshot: dict) -> list[dict]:
    forms = []
    for item in snapshot.get("evidence_summary") or []:
        for response in item.get("responses") or []:
            for form in response.get("forms") or []:
                fields = [
                    field.get("name")
                    for field in form.get("fields") or []
                    if field.get("name") and str(field.get("type") or "").lower() not in {"submit", "button"}
                ]
                forms.append(
                    {
                        "method": form.get("method") or "GET",
                        "action": form.get("action") or response.get("url"),
                        "fields": fields,
                    }
                )
    return forms[-12:]


def _best_payload_field(fields: list[str]) -> str:
    preferred = ["query", "q", "search", "id", "cmd", "payload", "name", "input"]
    lowered = {field.lower(): field for field in fields}
    for name in preferred:
        if name in lowered:
            return lowered[name]
    return fields[0]


def _payload_candidates(text: str) -> list[str]:
    candidates = []
    patterns = [
        r"payload\b[^\r\nA-Za-z0-9]*(.+)",
        r"(?:payload|载荷|参数|输入)\s*(?:is|是|为|:|：)\s*([^\r\n]+)",
        r"`([^`\r\n]*(?:select|sql_mode|union|flag|sleep|or\s+1=1)[^`\r\n]*)`",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            value = _clean_payload(match.group(1))
            if value:
                candidates.append(value)
    return _dedupe(candidates)


def _clean_payload(value: str) -> str:
    cleaned = value.strip().strip("'\"`")
    for marker in ("，", "。", "、", "�"):
        if marker in cleaned:
            cleaned = cleaned.split(marker, 1)[0]
    return cleaned.strip().strip("'\"`")


def _explicit_method_targets(text: str) -> list[tuple[str, str]]:
    results = []
    for match in re.finditer(r"\b(GET|POST)\s+((?:https?://|/)[^\s`'\"<>，。]+)", text, flags=re.IGNORECASE):
        target = match.group(2).strip()
        if _looks_like_path(target):
            results.append((match.group(1).upper(), target))
    return results


def _full_url_candidates(text: str) -> list[str]:
    urls = []
    for match in re.finditer(r"https?://[^\s`'\"<>，。)）]+", text):
        url = match.group(0).rstrip(".,;，。")
        parsed = urlparse(url)
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            urls.append(url)
    return _dedupe(urls)


def _headers_from_text(text: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    xff = re.search(r"X-Forwarded-For\s*[:=]?\s*([0-9a-fA-F:.]+)", text, flags=re.IGNORECASE)
    if xff:
        headers["X-Forwarded-For"] = xff.group(1)
    for match in re.finditer(r"\b(Header|header|头|请求头)\s+([A-Za-z0-9-]+)\s*[:=]\s*([^\s`，。]+)", text):
        headers[match.group(2)] = match.group(3)
    return headers


def _path_candidates(text: str) -> list[str]:
    text = _strip_full_urls(text)
    paths = []
    for match in re.finditer(r"(?<![\w:])(/[A-Za-z0-9._~/%+-]+(?:\?[A-Za-z0-9._~%=&:+-]+)?)", text):
        path = match.group(1).rstrip(".,;，。)")
        if _looks_like_path(path):
            paths.append(path)
    return _dedupe(paths)


def _strip_full_urls(text: str) -> str:
    return re.sub(r"https?://[^\s`'\"<>，。)）]+", " ", text)


def _query_candidates(text: str) -> list[str]:
    queries = []
    patterns = [
        r"\?([A-Za-z_][A-Za-z0-9_.%-]*=[^\s`'\"<>，。]+(?:&[A-Za-z_][A-Za-z0-9_.%-]*=[^\s`'\"<>，。]+)+)",
        r"(?<![\w%])([A-Za-z_][A-Za-z0-9_.%-]*=[^\s`'\"<>，。]+(?:&[A-Za-z_][A-Za-z0-9_.%-]*=[^\s`'\"<>，。]+)+)",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            query = match.group(1).strip().rstrip(".,;，。)")
            if "{" in query or "}" in query:
                continue
            queries.append(query)
    dynamic_queries = _dynamic_hmac_md5_queries(text=text, queries=queries)
    return _dedupe(dynamic_queries + queries)


def _dynamic_hmac_md5_queries(*, text: str, queries: list[str]) -> list[str]:
    lowered = text.lower()
    if not any(marker in lowered for marker in ("hmac", "hash_hmac", "md5")):
        return []
    if "ts" not in lowered or "sig" not in lowered:
        return []

    seed = _first_query_params(queries)
    user = seed.get("user") or _field_from_text(text, "user")
    token = seed.get("token") or _field_from_text(text, "token")
    nonce = seed.get("nonce") or _field_from_text(text, "nonce")
    if not user or not token:
        return []

    key = "NULL" if "null" in lowered else ""
    now = int(time.time())
    results = []
    for ts in (now, now - 1, now + 1):
        msg = f"{user}{token}{ts}".encode()
        digest = hmac.new(key.encode(), msg, hashlib.md5).hexdigest()
        for zero_count in (32, 26):
            params = {
                "user": user,
                "token": token,
                "ts": str(ts),
                "sig": digest[:6] + ("0" * zero_count),
            }
            if nonce:
                params["nonce"] = nonce
            results.append(urlencode(params))
    return results


def _first_query_params(queries: list[str]) -> dict[str, str]:
    for query in queries:
        params = {key: value for key, value in parse_qsl(query, keep_blank_values=True)}
        if params:
            return params
    return {}


def _field_from_text(text: str, name: str) -> str | None:
    match = re.search(rf"\b{name}\s*[=:]\s*([A-Za-z0-9_.%+-]+)", text, flags=re.IGNORECASE)
    if match:
        return match.group(1)
    return None


def _has_strong_explicit_request(instruction: str) -> bool:
    lowered = instruction.lower()
    return bool(
        _path_candidates(instruction)
        and (
            "payload" in lowered
            or "x-forwarded-for" in lowered
            or "sig=" in lowered
            or "token=" in lowered
            or "nonce=" in lowered
            or "hmac" in lowered
        )
    )


def _recent_paths(snapshot: dict) -> list[str]:
    paths = []
    for item in snapshot.get("evidence_summary") or []:
        for response in item.get("responses") or []:
            url = str(response.get("url") or "")
            parsed = urlparse(url)
            if parsed.path and parsed.path != "/" and _looks_like_path(parsed.path):
                paths.append(parsed.path)
    return _dedupe(paths[-6:])


def _target_to_path(*, task: TGATask, raw_target: str) -> str | None:
    if raw_target.startswith(("http://", "https://")):
        parsed = urlparse(raw_target)
        return parsed.path + (f"?{parsed.query}" if parsed.query else "")
    return raw_target


def _join_path_query(path: str, query: str) -> str:
    clean_query = query[1:] if query.startswith("?") else query
    separator = "&" if "?" in path else "?"
    return f"{path}{separator}{clean_query}"


def _looks_like_path(value: str) -> bool:
    blocked_prefixes = ("/api/v2", "/settings", "/mcp")
    if not value.startswith("/"):
        return False
    if any(value.startswith(prefix) for prefix in blocked_prefixes):
        return False
    if value in {"/", "//"}:
        return False
    return True


def _dedupe_actions(actions: list[HTTPAction]) -> list[HTTPAction]:
    result = []
    seen = set()
    for action in actions:
        key = json.dumps(action.model_dump(), sort_keys=True, ensure_ascii=False)
        if key in seen:
            continue
        seen.add(key)
        result.append(action)
    return result


def _dedupe(values: list[str]) -> list[str]:
    result = []
    seen = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _json_object(text: str) -> str:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("no json object")
    return text[start : end + 1]


def _truncate(text: str, limit: int = 1000) -> str:
    return " ".join(text.split())[:limit]
