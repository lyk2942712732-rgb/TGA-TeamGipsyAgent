"""Fetch only the supplied list API; map source fields without model transcription."""

from __future__ import annotations

import asyncio
import re
from collections import defaultdict
from typing import Any
from urllib.parse import urljoin, urlsplit

from pydantic import BaseModel, ConfigDict, Field

from .aoe_models import AOEQuestionInput
from .errors import ContractError
from .flag_signals import declared_format
from .submissions import SubmissionSpec
from .target_instances import TargetRequest, TargetSpec


class CollectionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request: TargetRequest
    items_path: str = ""
    next_url_path: str = ""
    total_path: str = ""
    fields: dict[str, str] = Field(
        default_factory=lambda: {"source_key": "unique_code", "description": "description", "difficulty": "difficulty"}
    )
    target: TargetSpec = Field(default_factory=lambda: TargetSpec(mode="unknown"))
    submission: SubmissionSpec | None = None
    connection_info: str = ""
    submission_instructions: str = ""
    preflight: TargetRequest | None = None


def field(value, path):
    for key in path.split(".") if path else []:
        if isinstance(value, dict):
            value = value.get(key)
        elif isinstance(value, list) and key.isdigit() and int(key) < len(value):
            value = value[int(key)]
        else:
            return None
    return value


def expand(value: Any, item: dict):
    if isinstance(value, str):
        # Preserve {flag}, which belongs to the submission runtime, not the list.
        return re.sub(
            r"\{([\w.]+)\}",
            lambda m: str(field(item, m[1])) if m[1] != "flag" and field(item, m[1]) is not None else m[0],
            value,
        )
    if isinstance(value, dict):
        return {k: expand(v, item) for k, v in value.items()}
    if isinstance(value, list):
        return [expand(v, item) for v in value]
    return value


def declared_requests(text: str) -> list[tuple[str, str, str]]:
    # Resolve only literal URL variables explicitly present in supplied instructions.
    text = text.replace("\r\n", "\n")
    variables = dict(re.findall(r'(?m)^\s*(?:[-*]\s+)?([A-Z][A-Z0-9_]+)\s*[:=]\s*(https?://[^\s`"<>]+)', text))
    for key, url in variables.items():
        text = text.replace("{" + key + "}", url.rstrip("/"))
    results = []
    declarations = list(re.finditer(r'\b(GET|POST)\s+(https?://[^\s`"<>]+)', text))
    for index, match in enumerate(declarations):
        url = match[2].rstrip(".,;，。；)）")
        # Labels apply to this declaration only, never other APIs in the same list.
        start = text.rfind("\n", 0, match.start()) + 1
        previous = text[:start].rstrip("\n").split("\n")[-1]
        if previous.rstrip().endswith((":", "：")) and not re.search(r"\b(GET|POST)\b", previous):
            start = max(0, start - len(previous) - 1)
        end = text.find("\n\n", match.end())
        end = end if end >= 0 else len(text)
        if index + 1 < len(declarations):
            end = min(end, text.rfind("\n", match.end(), declarations[index + 1].start()) + 1 or end)
        results.append((match[1], url, text[start:end]))
    for match in re.finditer(r'(?:QUESTION_LIST_URL|题单接口|题目列表接口)\s*[:=：]\s*(https?://[^\s`"<>]+)', text):
        results.append(("GET", match[1].rstrip(".,;，。；"), "question list"))
    return results


class QuestionCollection:
    def __init__(self, aoe):
        self.aoe = aoe
        self.locks = defaultdict(asyncio.Lock)

    async def collect(self, parent_id, plan: CollectionPlan) -> dict:
        async with self.locks[parent_id]:
            parent = await self.aoe.storage.get_task(parent_id)
            if parent.metadata.get("collection_complete"):
                return {
                    "saved": True,
                    "collection_complete": True,
                    "question_count": len(await self.aoe.storage.list_questions(parent_id)),
                }
            overview = await self.aoe.overview.prepare(parent_id)
            supplied = (overview / "task.md").read_text(encoding="utf-8")
            # Uploaded API instructions are user-provided too; never scan arbitrary files.
            for upload in await self.aoe.storage.list_input_files(parent_id):
                path = self.aoe.config.resolve_path(upload.storage_path)
                if (
                    path.suffix.lower() in {".txt", ".md", ".json", ".yaml", ".yml"}
                    and path.stat().st_size <= 1_000_000
                ):
                    supplied += "\n\n" + path.read_text(encoding="utf-8", errors="replace")
            declared = declared_requests(supplied)
            if not set(plan.fields) <= {"source_key", "title", "description", "difficulty", "attachments", "flag_format"}:
                raise ContractError("fields maps only original ID/title/description/difficulty/attachments/flag_format")
            allowed = {
                (method, url)
                for method, url, paragraph in declared
                if re.search(r"题单|题目列表|question[- ]list|challenge[- ]list|list.{0,20}challenges", paragraph, re.I)
            }
            if (plan.request.method, plan.request.url) not in allowed:
                raise ContractError(
                    "Use the explicitly supplied question-list API only; missing declarations "
                    "must be clarified, never probe documentation, attachments or other endpoints"
                )
            required_preflight = {(m, u) for m, u, p in declared if re.search(r"VPN|连通|preflight", p, re.I)}
            if required_preflight and not plan.preflight:
                raise ContractError("Supplied connectivity preflight must run before the question-list API")
            if plan.preflight:
                approved = {(m, u) for m, u, p in declared if re.search(r"VPN|连通|preflight", p, re.I)}
                if (plan.preflight.method, plan.preflight.url) not in approved or plan.preflight.method != "GET":
                    raise ContractError("Preflight must be an explicitly supplied GET connectivity check")
                status, body = await self.aoe.targets._request(plan.preflight)
                if status != 200 or not isinstance(body, dict) or body.get("status") != "ok":
                    raise ContractError("Supplied connectivity check did not return status=ok; collection stopped")
            request, visited, ids, expected_total = plan.request, set(), set(), None
            while True:
                identity = (request.url, tuple(sorted(request.params.items())))
                if identity in visited:
                    raise ContractError("Question-list pagination repeated a page; collection remains incomplete")
                visited.add(identity)
                status, body = await self.aoe.targets._request(request)
                if status != 200:
                    raise ContractError(f"Question-list API returned HTTP {status}; retry the same supplied endpoint")
                if plan.total_path:
                    total = field(body, plan.total_path)
                    if not isinstance(total, int) or isinstance(total, bool) or total < 0:
                        raise ContractError("total_path must identify the source's integer question count")
                    if expected_total is not None and total != expected_total:
                        raise ContractError("Source question count changed during pagination; retry the same API")
                    expected_total = total
                items = field(body, plan.items_path)
                if not isinstance(items, list):
                    raise ContractError(
                        "items_path does not identify the question array; no endpoint exploration allowed"
                    )
                specs = []
                for item in items:
                    if not isinstance(item, dict):
                        raise ContractError("Question-list items must be objects")
                    mapped = {name: field(item, path) for name, path in plan.fields.items()}
                    mapped["flag_format"] = mapped.get("flag_format") or declared_format(item)
                    if mapped.get("attachments") is None:
                        mapped.pop("attachments", None)
                    if not mapped.get("source_key") or not mapped.get("description"):
                        raise ContractError("Map original question ID and full description before collecting")
                    key = str(mapped["source_key"])
                    if key in ids:
                        raise ContractError("Question-list pagination contains duplicate original IDs")
                    ids.add(key)
                    mapped.update(
                        source_key=key,
                        title=mapped.get("title") or key,
                        connection_info=expand(plan.connection_info, item),
                        submission_instructions=expand(plan.submission_instructions, item),
                        target=expand(plan.target.model_dump(mode="json"), item),
                        submission=expand(plan.submission.model_dump(mode="json"), item) if plan.submission else None,
                        source=item,
                    )
                    specs.append(AOEQuestionInput.model_validate(mapped))
                # Registration wakes the scheduler; the full list is not returned through the model.
                if specs:
                    await self.aoe.register_questions(parent_id, specs)
                next_url = field(body, plan.next_url_path) if plan.next_url_path else None
                if not next_url:
                    break
                next_url = urljoin(request.url, str(next_url))
                origin, next_parts = urlsplit(plan.request.url), urlsplit(next_url)
                if (origin.scheme, origin.netloc, origin.path) != (
                    next_parts.scheme,
                    next_parts.netloc,
                    next_parts.path,
                ):
                    raise ContractError("Pagination must stay on the supplied list API; other endpoints are forbidden")
                request = plan.request.model_copy(update={"url": next_url, "params": {}})
            if expected_total is not None and len(ids) != expected_total:
                raise ContractError("Registered count does not cover the source total; collection remains incomplete")
            await self.aoe.finish_collection(parent_id, len(ids))
            return {
                "saved": True,
                "collection_complete": True,
                "question_count": len(ids),
                "manifest": "/aoe/questions.json",
                "instruction": "Collection complete. Stop now; no further exploration.",
            }
