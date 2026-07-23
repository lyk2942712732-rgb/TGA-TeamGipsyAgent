"""Type-aware Artifact indexing and bounded retrieval.

The index is a derived projection only. Raw bytes remain the authoritative,
content-addressed Artifact and every returned segment carries a stable source ref.
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from typing import Iterable

from tga.contracts import ArtifactIndex, ArtifactSegment
from tga.evidence.store import utc_now


_BLOCK_TAGS = {"article", "main", "section", "p", "pre", "li", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6", "br", "tr"}
_HIDDEN_TAGS = {"script", "style", "svg", "noscript", "template"}


class _ReadableHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hidden_depth = 0
        self.focus_depth = 0
        self.focus_stack: list[tuple[str, bool]] = []
        self.all_parts: list[str] = []
        self.focus_parts: list[str] = []
        self.title_parts: list[str] = []
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        if tag in _HIDDEN_TAGS:
            self.hidden_depth += 1
            return
        attrs_text = " ".join(str(value or "") for _, value in attrs).casefold()
        if tag in {"article", "main", "section", "div"}:
            activated = tag in {"article", "main"} or any(
                token in attrs_text
                for token in ("article", "post-content", "entry-content", "markdown-body", "main-content")
            )
            self.focus_stack.append((tag, activated))
            if activated:
                self.focus_depth += 1
        if tag == "title":
            self._in_title = True
        if tag in _BLOCK_TAGS:
            self._append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag in _HIDDEN_TAGS:
            self.hidden_depth = max(0, self.hidden_depth - 1)
            return
        if tag == "title":
            self._in_title = False
        if tag in {"article", "main", "section", "div"} and self.focus_stack:
            _, activated = self.focus_stack.pop()
            if activated:
                self.focus_depth = max(0, self.focus_depth - 1)
        if tag in _BLOCK_TAGS:
            self._append("\n")

    def handle_data(self, data: str) -> None:
        if self.hidden_depth or not data.strip():
            return
        if self._in_title:
            self.title_parts.append(data)
        self._append(data)

    def _append(self, value: str) -> None:
        if self.hidden_depth:
            return
        self.all_parts.append(value)
        if self.focus_depth:
            self.focus_parts.append(value)

    def readable_text(self) -> str:
        focused = _normalize_text(" ".join(self.focus_parts))
        all_text = _normalize_text(" ".join(self.all_parts))
        # A tiny focus node is usually a navigation shell, not the article.
        return focused if len(focused) >= 120 else all_text

    def title(self) -> str:
        return " ".join(" ".join(self.title_parts).split())[:300]


def build_artifact_index(
    *, task_id: str, artifact_id: str, raw: bytes, content_type: str = "", document_type: str | None = None
) -> ArtifactIndex:
    """Extract readable, source-located segments without external NLP services."""
    text = raw.decode("utf-8", errors="replace")
    kind = document_type or _document_type(content_type, text)
    status = "extracted"
    title = ""
    try:
        if kind == "html":
            parser = _ReadableHTMLParser()
            parser.feed(text)
            readable = parser.readable_text()
            title = parser.title()
            if len(readable) < 80:
                status = "failed"
                readable = ""
        elif kind == "json":
            value = json.loads(text)
            readable = _normalize_text(json.dumps(value, ensure_ascii=False, indent=2))
        else:
            readable = _normalize_text(text)
    except (UnicodeError, ValueError, json.JSONDecodeError):
        status = "failed"
        readable = ""

    segments = _segments(artifact_id, readable) if readable else []
    summary_parts = [segment.text for segment in segments[:3]]
    summary = _normalize_text("\n".join(([title] if title else []) + summary_parts))[:2400]
    return ArtifactIndex(
        artifact_id=artifact_id,
        task_id=task_id,
        document_type=kind,
        extraction_status=status,
        summary=summary,
        segments=segments,
        created_at=utc_now(),
    )


def retrieve_segments(
    index: ArtifactIndex,
    *,
    query: str | None = None,
    offset: int = 0,
    limit: int = 6000,
    section: str | None = None,
) -> dict:
    """Return bounded matches and never repeat an unrelated document prefix."""
    values = index.segments
    if section:
        folded = section.casefold()
        values = [item for item in values if folded in item.heading.casefold() or folded in item.ref.casefold()]
    if query:
        terms = [term.casefold() for term in re.findall(r"[\w.-]+", query) if len(term) > 1]
        ranked = []
        for item in values:
            folded = f"{item.heading}\n{item.text}".casefold()
            score = sum(folded.count(term) for term in terms)
            if score:
                ranked.append((score, item))
        values = [item for _, item in sorted(ranked, key=lambda pair: (-pair[0], pair[1].char_start))]
    else:
        values = [item for item in values if item.char_end > offset]

    remaining = max(1, limit)
    selected: list[dict] = []
    for item in values:
        text = item.text
        if not query and offset > item.char_start:
            text = text[max(0, offset - item.char_start) :]
        if not text:
            continue
        excerpt = text[:remaining]
        selected.append({"ref": item.ref, "heading": item.heading, "text": excerpt})
        remaining -= len(excerpt)
        if remaining <= 0:
            break
    return {
        "artifact_id": index.artifact_id,
        "document_type": index.document_type,
        "extraction_status": index.extraction_status,
        "summary": index.summary,
        "matches": selected,
        "query": query,
        "offset": offset,
        "truncated": remaining <= 0 or len(selected) < len(values),
    }


def _segments(artifact_id: str, text: str, *, target_chars: int = 1800) -> list[ArtifactSegment]:
    paragraphs = [value.strip() for value in re.split(r"\n{2,}", text) if value.strip()]
    if not paragraphs:
        paragraphs = [text] if text else []
    segments: list[ArtifactSegment] = []
    cursor = 0
    buffer: list[str] = []
    start = 0
    heading = ""

    def emit(parts: Iterable[str]) -> None:
        nonlocal cursor, start, heading
        value = "\n\n".join(parts).strip()
        if not value:
            return
        end = start + len(value)
        segments.append(
            ArtifactSegment(
                ref=f"{artifact_id}#segment-{len(segments) + 1}",
                heading=heading,
                text=value[:8000],
                char_start=start,
                char_end=end,
            )
        )
        cursor = end + 2
        start = cursor

    for paragraph in paragraphs:
        if len(paragraph) <= 140 and not paragraph.endswith((".", "。", ":", "：", ";", "；")):
            heading = paragraph[:300]
        if buffer and sum(len(item) for item in buffer) + len(paragraph) > target_chars:
            emit(buffer)
            buffer = []
        buffer.append(paragraph)
    emit(buffer)
    return segments[:128]


def _normalize_text(value: str) -> str:
    value = value.replace("\r", "\n")
    value = re.sub(r"[ \t\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    return re.sub(r"\n{3,}", "\n\n", value).strip()


def _document_type(content_type: str, text: str) -> str:
    folded = content_type.casefold()
    if "html" in folded or re.search(r"(?is)<(?:html|article|main|body)\b", text[:4000]):
        return "html"
    if "json" in folded or text.lstrip().startswith(("{", "[")):
        return "json"
    return "text"
