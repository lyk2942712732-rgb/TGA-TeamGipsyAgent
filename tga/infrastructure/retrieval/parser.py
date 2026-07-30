"""Conservative type-aware parser that always preserves extraction status."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable

from tga.domain.retrieval import (
    ChunkLocator,
    CorpusDocument,
    CorpusSource,
    DocumentChunk,
    DocumentRevision,
)


INJECTION_PATTERNS = (
    r"ignore (?:all |any )?(?:previous|prior) instructions",
    r"(?:system|developer) prompt",
    r"you are (?:now|chatgpt)",
    r"do not follow (?:the )?(?:system|developer|user)",
    r"(?:execute|run)\s+(?:this )?(?:command|shell|powershell|bash)",
    r"rm\s+-rf",
)


class StructuredDocumentParser:
    parser_id = "structured-v1"

    def parse(
        self,
        *,
        document: CorpusDocument,
        revision: DocumentRevision,
        raw: bytes,
        source: CorpusSource | None = None,
    ) -> tuple[DocumentRevision, tuple[DocumentChunk, ...]]:
        media_type = (revision.media_type or "").casefold()
        suffix = (document.canonical_uri or document.title).casefold()
        try:
            if "pdf" in media_type or suffix.endswith(".pdf"):
                text = raw.decode("utf-8")
                parts = self._pages(text)
            elif (
                "octet-stream" in media_type
                or any(suffix.endswith(value) for value in (".exe", ".dll", ".elf", ".bin"))
            ):
                extracted = str(revision.metadata.get("auditable_extracted_text") or "")
                if not extracted:
                    raise ValueError("binary indexing requires auditable extracted text")
                text = extracted
                parts = [(text, ChunkLocator(
                    kind="binary_extraction", source_ref="revision.metadata.auditable_extracted_text"
                ))]
            else:
                text = raw.decode("utf-8")
                if "json" in media_type or suffix.endswith(".json"):
                    parts = self._json(text)
                elif "markdown" in media_type or suffix.endswith((".md", ".markdown")):
                    parts = self._markdown(text)
                elif any(suffix.endswith(value) for value in (
                    ".py", ".js", ".ts", ".tsx", ".java", ".go", ".rs", ".c", ".cpp",
                )):
                    parts = self._code(text)
                elif "http" in media_type or revision.metadata.get("document_type") == "http":
                    parts = self._http(text)
                elif "log" in media_type or suffix.endswith((".log", ".jsonl")):
                    parts = self._logs(text)
                else:
                    parts = self._paragraphs(text)
        except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
            return revision.model_copy(update={
                "content_sha256": hashlib.sha256(raw).hexdigest(),
                "byte_size": len(raw),
                "extraction_status": "failed",
                "error": str(exc)[:4_000],
            }), ()

        effective_source = source
        channel = effective_source.channel if effective_source else str(
            document.metadata.get("channel") or "reference"
        )
        trust = effective_source.trust_level if effective_source else str(
            document.metadata.get("trust_level") or "unverified"
        )
        chunks: list[DocumentChunk] = []
        for index, (content, locator) in enumerate(parts):
            content = content.strip()
            if not content:
                continue
            digest = hashlib.sha256(content.encode()).hexdigest()
            chunk_id = "chunk_" + hashlib.sha256(
                f"{revision.id}:{index}:{digest}".encode()
            ).hexdigest()[:32]
            chunks.append(DocumentChunk(
                id=chunk_id,
                knowledge_base_id=document.knowledge_base_id,
                source_id=document.source_id,
                document_id=document.id,
                revision_id=revision.id,
                channel=channel,
                owner=document.owner,
                trust_level=trust,
                content=content[:200_000],
                content_sha256=digest,
                token_count=max(1, (len(content) + 3) // 4),
                locator=locator,
                safety_flags=self._safety_flags(content),
                metadata={"parser_id": self.parser_id},
                created_at=revision.created_at,
            ))
        return revision.model_copy(update={
            "content_sha256": hashlib.sha256(raw).hexdigest(),
            "byte_size": len(raw),
            "extraction_status": "parsed" if chunks else "failed",
            "error": None if chunks else "parser produced no auditable text",
        }), tuple(chunks)

    @staticmethod
    def _safety_flags(content: str) -> tuple[str, ...]:
        folded = content.casefold()
        flags = [
            "prompt_injection"
            for pattern in INJECTION_PATTERNS
            if re.search(pattern, folded)
        ]
        return tuple(dict.fromkeys(flags))

    @staticmethod
    def _paragraphs(text: str, *, target: int = 2_000):
        values = []
        for match in re.finditer(r"(?s)\S.*?(?=\n\s*\n|\Z)", text):
            raw = match.group(0)
            leading = len(raw) - len(raw.lstrip())
            trailing = len(raw.rstrip())
            start = match.start() + leading
            end = match.start() + trailing
            while end - start > target:
                boundary = text.rfind(" ", start, start + target)
                if boundary <= start:
                    boundary = start + target
                content = text[start:boundary].strip()
                content_start = text.find(content, start, boundary + 1)
                values.append((content, ChunkLocator(
                    kind="text_range", char_start=content_start,
                    char_end=content_start + len(content),
                )))
                start = boundary
                while start < end and text[start].isspace():
                    start += 1
            content = text[start:end].strip()
            if content:
                content_start = text.find(content, start, end + 1)
                values.append((content, ChunkLocator(
                    kind="text_range", char_start=content_start,
                    char_end=content_start + len(content),
                )))
        return values

    @staticmethod
    def _markdown(text: str):
        matches = list(re.finditer(r"(?m)^(#{1,6})\s+(.+)$", text))
        if not matches:
            return StructuredDocumentParser._paragraphs(text)
        values = []
        headings: list[str] = []
        for index, match in enumerate(matches):
            level = len(match.group(1))
            headings = headings[: level - 1] + [match.group(2).strip()]
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            content = text[match.start():end].strip()
            values.append((content, ChunkLocator(
                kind="text_range", char_start=match.start(), char_end=end,
                heading_path=tuple(headings),
            )))
        return values

    @staticmethod
    def _code(text: str):
        pattern = re.compile(
            r"(?m)^(?:async\s+def|def|class|function|func|fn|pub\s+fn|(?:export\s+)?(?:const|let|var)\s+\w+\s*=)\s*([\w.$:-]+)?"
        )
        matches = list(pattern.finditer(text))
        if not matches:
            lines = text.splitlines(keepends=True)
            return StructuredDocumentParser._line_windows(lines, window=80)
        values = []
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            symbol = match.group(1) or match.group(0).strip()[:200]
            values.append((text[match.start():end].strip(), ChunkLocator(
                kind="symbol", symbol=symbol,
                char_start=match.start(), char_end=end,
            )))
        return values

    @staticmethod
    def _logs(text: str):
        return StructuredDocumentParser._line_windows(text.splitlines(keepends=True), window=50)

    @staticmethod
    def _http(text: str):
        names = ("request", "headers", "body")
        blocks = text.split("\n\n", 2)
        values = []
        cursor = 0
        for index, block in enumerate(blocks):
            start = text.find(block, cursor)
            end = start + len(block)
            cursor = end
            values.append((block, ChunkLocator(
                kind="http_part", http_part=names[min(index, 2)],
                char_start=start, char_end=end,
            )))
        return values

    @staticmethod
    def _json(text: str):
        value = json.loads(text)
        values = []
        if isinstance(value, dict):
            for key, item in value.items():
                content = json.dumps(item, ensure_ascii=False, indent=2)
                values.append((content, ChunkLocator(kind="json_path", json_path=f"$.{key}")))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                content = json.dumps(item, ensure_ascii=False, indent=2)
                values.append((content, ChunkLocator(kind="json_path", json_path=f"$[{index}]")))
        else:
            values.append((str(value), ChunkLocator(kind="json_path", json_path="$")))
        return values

    @staticmethod
    def _pages(text: str):
        values = []
        for index, page in enumerate(text.split("\f"), start=1):
            if page.strip():
                values.append((page, ChunkLocator(kind="page", page=index)))
        return values

    @staticmethod
    def _line_windows(lines: list[str], *, window: int):
        values = []
        for offset in range(0, len(lines), window):
            part = "".join(lines[offset:offset + window])
            if part.strip():
                values.append((part, ChunkLocator(
                    kind="line_range", line_start=offset + 1,
                    line_end=min(len(lines), offset + window),
                )))
        return values

    @staticmethod
    def _bounded_text_parts(text: str, blocks: Iterable[str], *, target: int):
        values = []
        buffer: list[str] = []
        start = 0
        for block in blocks:
            if buffer and sum(map(len, buffer)) + len(block) > target:
                content = "\n\n".join(buffer)
                position = text.find(buffer[0], start)
                end = position + len(content)
                values.append((content, ChunkLocator(
                    kind="text_range", char_start=position, char_end=end,
                )))
                start = end
                buffer = []
            buffer.append(block)
        if buffer:
            content = "\n\n".join(buffer)
            position = max(0, text.find(buffer[0], start))
            values.append((content, ChunkLocator(
                kind="text_range", char_start=position,
                char_end=position + len(content),
            )))
        return values


__all__ = ["StructuredDocumentParser"]
