"""Small, scoped Web CTF flag hunter.

The hunter is intentionally conservative: it only touches in-scope URLs, stores
all responses as artifacts, and reports flags only when they appear in real
response text.
"""

from __future__ import annotations

import html.parser
import json
import re
from dataclasses import dataclass, field
from http.cookiejar import CookieJar
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse
from urllib.request import HTTPCookieProcessor, Request, build_opener

from tga.contracts import ArtifactRecord, Intent, TGATask, WorkerResult
from tga.core.scope import is_in_scope
from tga.evidence.artifacts import ArtifactStore
from tga.models.base import ModelMessage
from tga.models.bootstrap import build_model_client_from_env


COMMON_PATHS = [
    "/",
    "/flag",
    "/flag.txt",
    "/flags",
    "/robots.txt",
    "/.git/HEAD",
    "/backup.zip",
    "/www.zip",
    "/index.php.bak",
    "/admin",
    "/login",
    "/debug",
    "/source",
    "/src",
    "/api",
    "/api/flag",
]

ACTIVE_PAYLOADS = [
    "flag",
    "' or '1'='1",
    "\" or \"1\"=\"1",
    "{{7*7}}",
    "../../../flag",
    ";cat /flag",
]

POST_PROBES = [
    {"username": "tga_user", "password": "tga_pass"},
    {"username": "admin", "password": "admin"},
    {"name": "tga", "q": "flag"},
]


@dataclass
class FetchRecord:
    url: str
    status: int
    text: str
    method: str = "GET"
    body: str | None = None
    error: str | None = None


@dataclass
class HuntState:
    queue: list[str] = field(default_factory=list)
    visited: set[str] = field(default_factory=set)
    post_visited: set[str] = field(default_factory=set)
    records: list[FetchRecord] = field(default_factory=list)
    facts: list[str] = field(default_factory=list)
    leads: list[str] = field(default_factory=list)


class WebFlagHunter:
    def __init__(self, artifact_store: ArtifactStore, *, timeout_s: int = 12, max_requests: int = 28):
        self.artifact_store = artifact_store
        self.timeout_s = timeout_s
        self.max_requests = max_requests

    def run(self, *, task: TGATask, intent: Intent, workspace: str) -> WorkerResult:
        state = HuntState(queue=_seed_urls(task.target))
        state.queue.extend(_llm_candidate_urls(task=task, observations=[]))
        opener = build_opener(HTTPCookieProcessor(CookieJar()))
        consecutive_failures = 0

        while state.queue and len(state.visited) < self.max_requests:
            url = state.queue.pop(0)
            if url in state.visited or not _url_in_scope(url, task):
                continue
            state.visited.add(url)
            record = _fetch(opener=opener, url=url, timeout_s=self.timeout_s)
            state.records.append(record)
            if record.error:
                consecutive_failures += 1
                state.leads.append(f"fetch failed: {url} {record.error}")
                if consecutive_failures >= 3:
                    state.leads.append("stopped after three consecutive fetch failures")
                    break
                continue

            consecutive_failures = 0
            state.facts.append(f"{record.method} {url} -> HTTP {record.status}")
            parser = LinkAndFormParser(base_url=url)
            parser.feed(record.text[:200_000])
            _enqueue_links(state, task, parser.links)
            _enqueue_links(state, task, _source_paths(base_url=url, text=record.text))

            observations = state.facts[-8:] + _source_observations(record.text)
            _enqueue_links(state, task, _llm_candidate_urls(task=task, observations=observations))

            if task.allow_active_scan and task.intensity != "passive":
                _enqueue_links(state, task, _active_candidates(url, parser.forms))
                self._probe_post_routes(opener=opener, task=task, state=state, base_url=url, text=record.text)

        artifact = self._save_hunt_artifact(task=task, intent=intent, state=state)
        combined_text = "\n".join(record.text for record in state.records)
        flags = _extract_flags(combined_text, task.flag_format)
        errors = ["NO_FLAG_FOUND"] if not flags else []
        return WorkerResult(
            task_id=task.id,
            intent_id=intent.id,
            status="ok",
            artifacts=[artifact],
            facts=state.facts,
            leads=state.leads,
            flags=flags,
            errors=errors,
        )

    def _probe_post_routes(self, *, opener, task: TGATask, state: HuntState, base_url: str, text: str) -> None:
        for path in _source_paths(base_url=base_url, text=text, methods={"post"}):
            if path in state.post_visited or not _url_in_scope(path, task):
                continue
            state.post_visited.add(path)
            for body in POST_PROBES:
                if len(state.records) >= self.max_requests:
                    return
                encoded = urlencode(body)
                record = _fetch(opener=opener, url=path, timeout_s=self.timeout_s, method="POST", body=encoded)
                state.records.append(record)
                if record.error:
                    state.leads.append(f"post failed: {path} {record.error}")
                else:
                    state.facts.append(f"POST {path} -> HTTP {record.status}")

    def _save_hunt_artifact(self, *, task: TGATask, intent: Intent, state: HuntState) -> ArtifactRecord:
        payload = {
            "task_id": task.id,
            "intent_id": intent.id,
            "strategy": "web_ctf_flag_hunt",
            "visited": sorted(state.visited),
            "facts": state.facts,
            "leads": state.leads,
            "responses": [
                {
                    "url": record.url,
                    "method": record.method,
                    "body": record.body,
                    "status": record.status,
                    "error": record.error,
                    "text": record.text[:80_000],
                }
                for record in state.records
            ],
        }
        return self.artifact_store.save_text(
            task_id=task.id,
            intent_id=intent.id,
            kind="http_response",
            text=json.dumps(payload, ensure_ascii=False, indent=2),
            tool="web-flag-hunter",
            target=task.target,
            suffix=".json",
        )


class LinkAndFormParser(html.parser.HTMLParser):
    def __init__(self, *, base_url: str):
        super().__init__()
        self.base_url = base_url
        self.links: list[str] = []
        self.forms: list[dict] = []
        self._current_form: dict | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        data = {key.lower(): value or "" for key, value in attrs}
        if tag in {"a", "link", "script", "img"}:
            raw = data.get("href") or data.get("src")
            if raw:
                self.links.append(urljoin(self.base_url, raw))
        if tag == "form":
            self._current_form = {
                "action": urljoin(self.base_url, data.get("action") or self.base_url),
                "method": (data.get("method") or "get").lower(),
                "inputs": [],
            }
            self.forms.append(self._current_form)
        if tag == "input" and self._current_form is not None:
            name = data.get("name")
            if name:
                self._current_form["inputs"].append(name)

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self._current_form = None


def _seed_urls(target: str) -> list[str]:
    parsed = urlparse(target)
    if not parsed.scheme:
        target = "http://" + target
        parsed = urlparse(target)
    base = urlunparse((parsed.scheme, parsed.netloc, "", "", "", ""))
    urls = [target]
    urls.extend(urljoin(base, path) for path in COMMON_PATHS)
    return _dedupe(urls)


def _fetch(*, opener, url: str, timeout_s: int, method: str = "GET", body: str | None = None) -> FetchRecord:
    data = body.encode("utf-8") if body is not None else None
    headers = {"User-Agent": "TGA-CTF-Agent/0.1"}
    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    request = Request(url, data=data, headers=headers, method=method)
    try:
        with opener.open(request, timeout=timeout_s) as response:
            raw = response.read(1_000_000)
            text = raw.decode("utf-8", errors="replace")
            return FetchRecord(url=url, status=int(response.status), text=text, method=method, body=body)
    except HTTPError as exc:
        text = exc.read(1_000_000).decode("utf-8", errors="replace")
        return FetchRecord(url=url, status=int(exc.code), text=text, method=method, body=body, error=None)
    except (URLError, TimeoutError, OSError) as exc:
        return FetchRecord(url=url, status=0, text="", method=method, body=body, error=str(exc))


def _extract_flags(text: str, flag_format: str | None) -> list[str]:
    pattern_texts = [flag_format or r"flag\{[^}]+\}"]
    if not flag_format or flag_format in {r"flag\{[^}]+\}", r"FLAG\{[^}]+\}"}:
        pattern_texts.append(r"[A-Za-z0-9_]{2,32}\{[^{}\s]{4,200}\}")
    values: list[str] = []
    for pattern_text in pattern_texts:
        try:
            pattern = re.compile(pattern_text)
        except re.error:
            pattern = re.compile(r"flag\{[^}]+\}")
        values.extend(match.group(0) for match in pattern.finditer(text))
    return _dedupe(values)


def _active_candidates(url: str, forms: list[dict]) -> list[str]:
    candidates: list[str] = []
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    names = set(query) or {"q", "id", "page", "file", "cmd", "name", "search"}
    for name in names:
        for payload in ACTIVE_PAYLOADS:
            updated = dict(query)
            updated[name] = payload
            candidates.append(urlunparse(parsed._replace(query=urlencode(updated))))
    for form in forms:
        if form.get("method") != "get":
            continue
        action = form.get("action") or url
        inputs = form.get("inputs") or ["q"]
        for payload in ACTIVE_PAYLOADS[:4]:
            candidates.append(action + ("&" if "?" in action else "?") + urlencode({name: payload for name in inputs}))
    return candidates


def _source_paths(*, base_url: str, text: str, methods: set[str] | None = None) -> list[str]:
    wanted = {item.lower() for item in methods} if methods else None
    paths = []
    for match in re.finditer(r"app\.(get|post|all)\([\"']([^\"']+)[\"']", text):
        method = match.group(1).lower()
        if wanted and method not in wanted and method != "all":
            continue
        paths.append(urljoin(base_url, match.group(2)))
    return paths


def _source_observations(text: str) -> list[str]:
    observations = []
    excerpt = re.sub(r"\s+", " ", text).strip()[:1200]
    if excerpt:
        observations.append(excerpt)
    for match in re.finditer(r"app\.(?:get|post|all)\([\"']([^\"']+)[\"']", text):
        observations.append(f"express route discovered: {match.group(1)}")
    return observations[:6]


def _llm_candidate_urls(*, task: TGATask, observations: list[str]) -> list[str]:
    client = build_model_client_from_env()
    if client is None:
        return []
    prompt = (
        "你是授权 CTF 靶机辅助规划器。只输出 JSON，格式为 "
        '{"paths":["/flag"],"rationale":"..."}。'
        "只能建议当前目标下的相对路径，不能建议越权目标，不能声称已经获得 flag。\n"
        f"目标: {task.target}\n范围: {task.scope}\n任务: {task.goal}\n观察: {observations}"
    )
    try:
        response = client.chat([ModelMessage(role="user", content=prompt)], temperature=0.1)
        data = json.loads(_json_object(response.content))
    except Exception:
        return []
    paths = data.get("paths") if isinstance(data, dict) else None
    if not isinstance(paths, list):
        return []
    parsed = urlparse(task.target if "://" in task.target else "http://" + task.target)
    base = urlunparse((parsed.scheme, parsed.netloc, "", "", "", ""))
    return [urljoin(base, str(path)) for path in paths if str(path).startswith("/")]


def _json_object(text: str) -> str:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("no json object")
    return text[start : end + 1]


def _enqueue_links(state: HuntState, task: TGATask, links: list[str]) -> None:
    for link in links:
        if _url_in_scope(link, task) and link not in state.visited and link not in state.queue:
            state.queue.append(link)


def _url_in_scope(url: str, task: TGATask) -> bool:
    return is_in_scope(url, task.scope)


def _dedupe(values) -> list[str]:
    result: list[str] = []
    seen = set()
    for value in values:
        if value and value not in seen:
            result.append(value)
            seen.add(value)
    return result
