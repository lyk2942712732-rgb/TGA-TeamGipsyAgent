"""One durable, system-owned correction request per rejected Worker submission."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
from contextlib import nullcontext
from datetime import datetime
from uuid import uuid4

import httpx

from .aoe_models import active_attempts
from .errors import ContractError
from .submissions import EXECUTION_TOOLS, Submissions, accepted_receipt, receipt_value, response_objects


def records_root(config, question):
    # The question root is system-owned; Workers mount only their workspace/logs and selected inputs.
    return config.task_path(question.id) / "submission-protection"


def contract_digest(spec):
    return hashlib.sha256(spec.model_dump_json().encode()).hexdigest()


def protected_acceptance(config, question, body, spec):
    for path in records_root(config, question).glob("*.json"):
        record = json.loads(path.read_text(encoding="utf-8"))
        if (
            record.get("status") == "accepted"
            and record.get("flag") == body.conclusion.strip()
            and record.get("contract") == contract_digest(spec)
            and (not body.submission_action_id or body.submission_action_id == record["action_id"])
            and accepted_receipt(spec, record["response"], record["http_status"])
        ):
            return record["action_id"]
    return None


def replace_flag(value, flag):
    if isinstance(value, str):
        return value.replace("{flag}", flag)
    if isinstance(value, list):
        return [replace_flag(v, flag) for v in value]
    if isinstance(value, dict):
        return {k: replace_flag(v, flag) for k, v in value.items()}
    return value


class SubmissionGuard:
    def __init__(self, config, storage, *, client=None):
        self.config, self.storage, self.client = config, storage, client
        self.submissions = Submissions(config)

    async def recover(self, task_id, agent_id):
        """Redeliver an accepted receipt if a hook response was lost, without another request."""
        question = await self.storage.get_task(task_id)
        if question.parent_task_id is None or not question.metadata.get("aoe_auto_submit_flag", True):
            return None
        attempt = next((a for a in active_attempts(question) if a["agent_id"] == agent_id), None)
        spec = self.submissions.spec(question)
        if not attempt or not spec:
            return None
        paths = sorted(
            records_root(self.config, question).glob("*.json"), key=lambda p: p.stat().st_mtime_ns, reverse=True
        )
        for path in paths:
            record = json.loads(path.read_text(encoding="utf-8"))
            if (
                record.get("agent_id") == agent_id
                and record.get("attempt_id") == attempt["id"]
                and record.get("status") == "accepted"
                and record.get("contract") == contract_digest(spec)
            ):
                return record
        return None

    @staticmethod
    def command(action):
        operation = action.get("action", {})
        return operation.get("command") or operation.get("input", {}).get("command", "")

    def rejection(self, question, spec, action):
        operation, command = action.get("action", {}), self.command(action)
        if (
            operation.get("tool", "").lower() not in EXECUTION_TOOLS
            or action.get("status") not in {"completed", "failed"}
            or spec.request.url not in command
            or not re.search(r"\bcurl\b|urllib\.request|requests\.(?:post|request)|httpx", command)
        ):
            return False
        # Require the literal original question ID, rather than accepting q-1 inside q-10.
        source = str(question.metadata.get("source_key", ""))
        if not source or not re.search(r"(?<![\w-])" + re.escape(source) + r"(?![\w-])", command):
            return False
        normalized = command.replace('\\"', '"').replace("\\'", "'")
        if isinstance(spec.request.body, dict):
            for key, value in spec.request.body.items():
                if (
                    isinstance(value, str)
                    and "{flag}" not in value
                    and not re.search(
                        r"[\"']"
                        + re.escape(key)
                        + r"[\"']\s*:\s*(?:"
                        + re.escape(json.dumps(value, ensure_ascii=False))
                        + "|"
                        + re.escape(repr(value))
                        + ")",
                        normalized,
                    )
                ):
                    return False
        try:
            text = self.submissions._body(question, action["_agent_id"], action)
        except (OSError, ValueError, ContractError):
            return False
        statuses = re.findall(r"\bHTTP(?:/\d(?:\.\d)?)?\s*[:= ]\s*(\d{3})", text)
        if statuses and int(statuses[-1]) not in (200, 400, 422):
            return False
        return any(
            receipt_value(r, spec.success_path) is False
            and not r.get("error")
            and r.get("code") in (None, 0, 200, 400, 422, "ok", "success", "wrong_answer", "incorrect")
            for r in response_objects(text)
        )

    async def check(self, task_id, agent_id, action_id=""):
        if not action_id:
            return None
        question = await self.storage.get_task(task_id)
        if question.parent_task_id is None or not question.metadata.get("aoe_auto_submit_flag", True):
            return None
        attempt = next((a for a in active_attempts(question) if a["agent_id"] == agent_id), None)
        if attempt is None or attempt.get("deadline") is None or attempt["deadline"] <= time.time():
            return None
        spec = self.submissions.spec(question)
        if spec is None:
            return None
        actions = self.submissions._actions(question, agent_id)
        for index in range(len(actions) - 1, max(-1, len(actions) - 20), -1):
            action = actions[index]
            if action_id and action["action_id"] != action_id:
                continue
            if (
                not action.get("started_at")
                or datetime.fromisoformat(action["started_at"]).timestamp() < attempt["started_at"]
            ):
                continue
            if not self.rejection(question, spec, {**action, "_agent_id": agent_id}):
                continue
            token = hashlib.sha256((agent_id + ":" + action["action_id"]).encode()).hexdigest()
            path = records_root(self.config, question) / (token + ".json")
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                # Claim BEFORE the HTTP request; a restart or lost response must never resend it.
                claim = os.open(path.with_suffix(".claim"), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                return (
                    json.loads(path.read_text(encoding="utf-8"))
                    if path.exists()
                    else {
                        "status": "claimed",
                        "failed_action_id": action["action_id"],
                        "instruction": "Submission protection was already claimed; it will not submit again.",
                    }
                )
            else:
                os.close(claim)
            record = {
                "status": "no_flag",
                "action_id": "submission-protection-" + token,
                "failed_action_id": action["action_id"],
                "agent_id": agent_id,
                "attempt_id": attempt["id"],
                "contract": contract_digest(spec),
            }
            # Reuse the existing output filters, bounded to evidence preceding this failed request.
            candidates = list(self.submissions.flag_outputs(question, attempt, actions[max(0, index - 19) : index + 1]))
            if candidates:
                newest = candidates[0][0]["action_id"]
                flags = list(dict.fromkeys(flag for evidence, flag in candidates if evidence["action_id"] == newest))
                if len(flags) == 1:
                    record.update(flag=flags[0], source_action_id=newest)
                    await self.submit(spec, record)
                else:
                    record["status"] = "ambiguous_flag"
            record["instruction"] = (
                "System submission protection obtained platform success. Use this exact flag as conclusion and "
                "action_id as submission_action_id in final-candidate.json, cite your published Findings, "
                "and publish the final candidate. Do not submit this flag again."
                if record["status"] == "accepted"
                else "Submission protection ran once for this failed action and will not retry it. "
                "Continue analysis; only an accepted platform receipt permits a final candidate."
            )
            temporary = path.with_suffix("." + uuid4().hex + ".tmp")
            try:
                temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
            return record
        return None

    async def submit(self, spec, record):
        request = spec.request
        payload = request.model_dump()
        if "{flag}" not in json.dumps(payload):
            record["status"] = "missing_flag_placeholder"
            return
        payload = replace_flag(payload, record["flag"])
        try:
            async with asyncio.timeout(6):
                connection = (
                    httpx.AsyncClient(timeout=4, follow_redirects=False)
                    if self.client is None
                    else nullcontext(self.client)
                )
                async with connection as client:
                    response = await client.request(
                        payload["method"],
                        payload["url"],
                        headers=payload["headers"],
                        params=payload["params"],
                        json=payload["body"],
                        timeout=4,
                        follow_redirects=False,
                    )
            try:
                value = response.json()
            except ValueError:
                value = response.text[:10000]
            # Never deliver platform credentials back in an echoed response.
            text = json.dumps(value, ensure_ascii=False)
            for secret in request.headers.values():
                if secret:
                    text = text.replace(json.dumps(secret, ensure_ascii=False)[1:-1], "[redacted]")
            value = json.loads(text)
            record.update(
                http_status=response.status_code,
                response=value,
                status=(
                    "accepted"
                    if isinstance(value, dict) and accepted_receipt(spec, value, response.status_code)
                    else "rejected"
                ),
            )
        except (httpx.HTTPError, TimeoutError) as exc:
            record.update(status="request_failed", error=type(exc).__name__)
