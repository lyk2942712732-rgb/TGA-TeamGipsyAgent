"""Lightweight web observation for CTF HTTP targets.

This module extracts the interaction contract a browser would learn from a
page: links, forms, methods, actions, and input names. The LLM planner should
reason from these facts instead of guessing where parameters belong.
"""

from __future__ import annotations

import html.parser
import re
from urllib.parse import urljoin


class PageStructureParser(html.parser.HTMLParser):
    def __init__(self, *, base_url: str):
        super().__init__()
        self.base_url = base_url
        self.title = ""
        self.links: list[str] = []
        self.script_src: list[str] = []
        self.api_hints: list[str] = []
        self.websocket_hints: list[str] = []
        self.forms: list[dict] = []
        self._current_form: dict | None = None
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        data = {key.lower(): value or "" for key, value in attrs}
        if tag == "title":
            self._in_title = True
            return
        if tag in {"a", "link", "script", "img"}:
            raw = data.get("href") or data.get("src")
            if raw:
                resolved = urljoin(self.base_url, raw)
                self.links.append(resolved)
                if tag == "script":
                    self.script_src.append(resolved)
            return
        if tag == "meta":
            content = data.get("content") or ""
            if "ws://" in content or "wss://" in content:
                self.websocket_hints.append(content[:500])
        if tag == "form":
            self._current_form = {
                "method": (data.get("method") or "GET").upper(),
                "action": urljoin(self.base_url, data.get("action") or self.base_url),
                "enctype": data.get("enctype") or "application/x-www-form-urlencoded",
                "fields": [],
            }
            self.forms.append(self._current_form)
            return
        if self._current_form is None:
            return
        if tag in {"input", "textarea", "select", "button"}:
            name = data.get("name")
            if not name:
                return
            field = {
                "name": name,
                "type": data.get("type") or tag,
            }
            if data.get("value"):
                field["value"] = data["value"]
            self._current_form["fields"].append(field)

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "title":
            self._in_title = False
        if tag == "form":
            self._current_form = None


def analyze_html(*, url: str, text: str, content_type: str = "") -> dict:
    if not _looks_like_html(text=text, content_type=content_type):
        return {}
    parser = PageStructureParser(base_url=url)
    try:
        parser.feed(text)
    except Exception:
        return {}
    forms = _dedupe_forms(parser.forms)
    links = _dedupe(parser.links)[:30]
    observations = []
    for form in forms:
        fields = ", ".join(field.get("name", "") for field in form.get("fields", []) if field.get("name"))
        observations.append(
            f"form method={form.get('method')} action={form.get('action')} fields=[{fields}]"
        )
    return {
        "title": " ".join(parser.title.split())[:200],
        "forms": forms,
        "links": links,
        "script_src": _dedupe(parser.script_src)[:30],
        "api_hints": _dedupe(_api_hints(text))[:20],
        "websocket_hints": _dedupe(parser.websocket_hints + _websocket_hints(text))[:10],
        "interaction_notes": observations[:8],
    }


def _api_hints(text: str) -> list[str]:
    # These are observations, never routes to invoke automatically.
    return re.findall(r"(?:/api/|/graphql\\b)[A-Za-z0-9_./?=&%-]*", text, flags=re.IGNORECASE)


def _websocket_hints(text: str) -> list[str]:
    return re.findall(r"wss?://[^\\s'\"<>]+", text, flags=re.IGNORECASE)


def _looks_like_html(*, text: str, content_type: str) -> bool:
    lowered_type = content_type.lower()
    if "html" in lowered_type:
        return True
    sample = text[:2000].lower()
    return "<html" in sample or "<form" in sample or "<!doctype html" in sample


def _dedupe_forms(forms: list[dict]) -> list[dict]:
    result = []
    seen = set()
    for form in forms:
        key = (
            form.get("method"),
            form.get("action"),
            tuple(field.get("name") for field in form.get("fields", [])),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(
            {
                "method": form.get("method") or "GET",
                "action": form.get("action") or "",
                "enctype": form.get("enctype") or "application/x-www-form-urlencoded",
                "fields": form.get("fields") or [],
            }
        )
    return result[:12]


def text_excerpt(text: str, limit: int = 2000) -> str:
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _dedupe(values: list[str]) -> list[str]:
    result = []
    seen = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
