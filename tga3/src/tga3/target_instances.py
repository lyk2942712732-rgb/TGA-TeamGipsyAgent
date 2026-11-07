"""File-backed platform target leases; never execute Scout-supplied shell commands."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from copy import deepcopy
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator


class TargetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    method: Literal["GET", "POST", "PUT", "DELETE"] = "POST"
    url: str = Field(min_length=1, max_length=4000)
    headers: dict[str, str] = Field(default_factory=dict)
    params: dict[str, str] = Field(default_factory=dict)
    body: Any = None

    @model_validator(mode="after")
    def http_url(self):
        parsed = urlsplit(self.url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Target interface must be an HTTP(S) URL without embedded credentials")
        return self


class TargetSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["none", "static", "managed", "unknown"]
    start: TargetRequest | None = None
    stop: TargetRequest | None = None
    status: TargetRequest | None = None
    addresses_path: str = "container_addr"
    quota_limit: int | None = Field(default=None, ge=1, le=10000)
    quota_scope: str = Field(default="", max_length=200)
    quota_error: str = Field(default="max active challenge instances reached", max_length=500)

    @model_validator(mode="after")
    def managed_contract(self):
        if self.mode == "managed" and (self.start is None or self.stop is None):
            raise ValueError("Managed targets need both start and stop interfaces")
        if self.mode != "managed" and any((self.start, self.stop, self.status)):
            raise ValueError("Only managed targets have lifecycle interfaces")
        return self


class TargetInstances:
    def __init__(self, config, *, clock=time.time, client: httpx.AsyncClient | None = None):
        self.config, self.clock, self.client = config, clock, client
        self.lock = asyncio.Lock()
        self.records: dict[str, dict] = {}
        self._specs: dict[UUID, tuple[dict, TargetSpec]] = {}
        root = config.resolve_path(config.runtime.task_root)
        for path in root.glob("*/target-instances.json"):
            value = json.loads(path.read_text(encoding="utf8"))
            self.records.update(value.get("instances", {}))

    def _save(self, parent: str) -> None:
        path = self.config.task_path(UUID(parent)) / "target-instances.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f".{uuid4().hex}.tmp")
        try:
            temporary.write_text(json.dumps({"instances": {k: v for k, v in self.records.items()
                if v["parent_id"] == parent}}, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def spec(self, question) -> TargetSpec:
        cached = self._specs.get(question.id)
        value = question.metadata.get("target")
        if value is None:
            if cached is not None:
                return cached[1]
            path = self.config.task_path(question.id) / "question.json"
            value = json.loads(path.read_text(encoding="utf8")).get("target") if path.is_file() else None
        value = value or {"mode": "unknown"}
        if cached is None or cached[0] != value:
            parsed = TargetSpec.model_validate(value)
            cached = self._specs[question.id] = (deepcopy(value), parsed)
        return cached[1]

    def register(self, question_id: UUID, spec: TargetSpec) -> None:
        self._specs[question_id] = (spec.model_dump(), spec)

    @staticmethod
    def group(spec: TargetSpec) -> str:
        # Credentials distinguish account quotas. Persist only a digest as the group identifier.
        start = spec.start
        value = (urlsplit(start.url).netloc, spec.quota_scope, sorted(start.headers.items()))
        return hashlib.sha256(json.dumps(value).encode()).hexdigest()

    async def _request(self, request: TargetRequest) -> tuple[int, Any]:
        if self.client is None:
            self.client = httpx.AsyncClient(timeout=10, follow_redirects=False)
        response = await self.client.request(request.method, request.url, headers=request.headers,
                                             params=request.params, json=request.body)
        try:
            body = response.json()
        except ValueError:
            body = response.text[:10000]
        return response.status_code, body

    @staticmethod
    def _addresses(body: Any, path: str) -> list[str]:
        for part in path.split(".") if path else []:
            if not isinstance(body, dict):
                return []
            body = body.get(part)
        if isinstance(body, str):
            body = [body]
        return [x for x in body if isinstance(x, str) and x.strip()] if isinstance(body, list) else []

    def state(self, question_id: UUID) -> dict:
        record = self.records.get(str(question_id), {})
        return {key: record[key] for key in ("state", "addresses", "reason", "retry_at") if key in record}

    def quota_blocked(self, questions: list) -> bool:
        for question in questions:
            spec = self.spec(question)
            if spec.mode != "managed":
                continue
            group = self.group(spec)
            peers = [v for v in self.records.values() if v["group"] == group]
            if (spec.quota_limit is not None and sum(bool(v.get("owned")) for v in peers) >= spec.quota_limit
                    or any(v.get("reason") == "quota" and v.get("retry_at", 0) > self.clock() for v in peers)):
                return True
        return False

    async def prepare(self, question) -> bool:
        spec = self.spec(question)
        if spec.mode != "managed":
            return True
        async with self.lock:
            ident, parent, now = str(question.id), str(question.parent_task_id), self.clock()
            group = self.group(spec)
            record = self.records.get(ident)
            if record and record["state"] == "ready":
                return True
            if record and record.get("retry_at", 0) > now:
                return False
            if record and record["state"] == "closing":
                return False
            peers = [v for k, v in self.records.items() if k != ident and v["group"] == group]
            occupied = [v for v in peers if v.get("owned")]
            blocked = any(v.get("reason") == "quota" and v.get("retry_at", 0) > now for v in peers)
            if not (record and record.get("owned")) and (
                    blocked or spec.quota_limit is not None and len(occupied) >= spec.quota_limit):
                self.records[ident] = {"parent_id": parent, "group": group, "state": "waiting",
                    "owned": False, "reason": "quota", "retry_at": now + 10}
                self._save(parent)
                return False
            # Do not share a platform question between independently scheduled parent tasks.
            resource = hashlib.sha256(json.dumps(spec.start.model_dump(), sort_keys=True).encode()).hexdigest()
            if any(v.get("resource") == resource and v.get("owned") for v in peers):
                return False
            previously_owned = bool(record and record.get("owned"))
            record = self.records[ident] = {**(record or {}), "parent_id": parent, "group": group,
                "resource": resource, "contract": spec.model_dump(), "state": "starting",
                "owned": True, "held_until": now + 30}
            self._save(parent)
            try:
                status, body = await self._request(spec.status if previously_owned and spec.status else spec.start)
                if spec.quota_error and spec.quota_error.casefold() in json.dumps(body).casefold():
                    record.update(state="waiting", owned=previously_owned, reason="quota", retry_at=now + 10)
                elif status >= 400:
                    record.update(state="waiting", owned=previously_owned, reason=f"http_{status}", retry_at=now + 30)
                elif isinstance(body, dict) and body.get("code") not in (None, 0, 200, "ok", "success"):
                    record.update(state="waiting", owned=previously_owned, reason="application_error", retry_at=now + 30)
                else:
                    record["owned"] = True
                    addresses = self._addresses(body, spec.addresses_path)
                    record.update(state="ready" if addresses else "starting", addresses=addresses,
                                  reason="" if addresses else "not_ready", retry_at=now + 2)
            except (httpx.HTTPError, ValueError) as exc:
                # A timed-out start may already have created an instance. Retain the cleanup contract.
                record.update(state="starting", owned=True, reason=type(exc).__name__, retry_at=now + 10)
            self._save(parent)
            return record["state"] == "ready"

    async def release(self, question_id: UUID, *, force: bool = False) -> bool:
        async with self.lock:
            record = self.records.get(str(question_id))
            if record is None or not record.get("owned"):
                return True
            if not force and record["state"] == "closing" and record.get("retry_at", 0) > self.clock():
                return False
            spec = TargetSpec.model_validate(record["contract"])
            record.update(state="closing", retry_at=self.clock() + 10)
            self._save(record["parent_id"])
            try:
                status, body = await self._request(spec.stop)
                # A 2xx response containing an explicit failure must not free local capacity.
                failed = isinstance(body, dict) and (body.get("closed") is False or body.get("success") is False
                                                     or body.get("code") not in (None, 0, 200, "ok", "success"))
                if status < 300 and not failed:
                    record.update(state="released", owned=False, addresses=[], reason="", retry_at=0)
                    for peer in self.records.values():
                        if peer["group"] == record["group"] and peer.get("reason") == "quota":
                            peer["retry_at"] = 0
                            self._save(peer["parent_id"])
            except (httpx.HTTPError, ValueError):
                pass
            self._save(record["parent_id"])
            return not record["owned"]

    async def reconcile(self, questions: list, *, parent_id: UUID | None = None) -> None:
        from .aoe_models import active_attempts

        active = {str(q.id) for q in questions if active_attempts(q)
                  and q.metadata.get("status") not in {"solved", "abandoned", "cancelled"}}
        for ident, record in list(self.records.items()):
            if parent_id is not None and record["parent_id"] != str(parent_id):
                continue
            if record.get("owned") and ident not in active and (
                    record["state"] == "closing" or record.get("held_until", 0) <= self.clock()):
                await self.release(UUID(ident))

    async def release_parent(self, parent_id: UUID) -> bool:
        outcomes = [await self.release(UUID(k), force=True) for k, v in list(self.records.items())
                    if v["parent_id"] == str(parent_id)]
        return all(outcomes)

    async def close(self) -> None:
        if self.client is not None:
            await self.client.aclose()

    def forget_parent(self, parent_id: UUID) -> None:
        self.records = {k: v for k, v in self.records.items() if v["parent_id"] != str(parent_id)}
        self._specs.clear()
