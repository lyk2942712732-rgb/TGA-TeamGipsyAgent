"""System-managed flag submission, receipt classification, and flag-signal detection."""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field

from .action_logs import read_action_log
from .errors import ContractError
from .evidence import evidence_files
from .flag_signals import flag_pattern, question_flag_format
from .target_instances import TargetRequest

DEFAULT_DUPLICATE_CODES: tuple[str | int, ...] = ("duplicate", "already_solved", 402)
DEFAULT_DUPLICATE_KEYWORDS: tuple[str, ...] = (
    "已解出",
    "已经解出",
    "已通过",
    "已提交过",
    "重复提交",
    "already solved",
    "already submitted",
    "already correctly submitted",
    "already_solved",
    "duplicate",
)
FALLBACK_SUCCESS_CHECKS: tuple[tuple[str, Any], ...] = (
    ("correct", True),
    ("data.result", True),
    ("result", True),
    ("data.status", "correct"),
    ("status", "correct"),
    ("solved", True),
)
FALLBACK_REJECT_CHECKS: tuple[tuple[str, Any], ...] = (
    ("correct", False),
    ("data.result", False),
    ("result", False),
    ("data.status", "incorrect"),
    ("data.status", "wrong"),
    ("status", "incorrect"),
    ("status", "wrong"),
)
REJECT_KEYWORDS: tuple[str, ...] = (
    "flag错误",
    "答案错误",
    "flag不正确",
    "错误flag",
    "wrong flag",
    "wrong answer",
    "incorrect",
)


class SubmissionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request: TargetRequest
    success_path: str = Field(default="correct", min_length=1, max_length=200)
    success_value: Any = True
    duplicate_code: str | int | None = "duplicate"
    duplicate_codes: list[str | int] = Field(default_factory=lambda: list(DEFAULT_DUPLICATE_CODES))
    duplicate_keywords: list[str] = Field(default_factory=lambda: list(DEFAULT_DUPLICATE_KEYWORDS))


class SubmissionMissing(ContractError):
    pass


class SubmissionRejected(ContractError):
    pass


EXECUTION_TOOLS = {"bash", "exec_command", "command_execution", "shell", "shell_command", "run_shell_command"}


def result_text(raw: str) -> str:
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        return raw
    if isinstance(value, dict) and "stdout" in value:
        return str(value["stdout"]) + "\n" + str(value.get("stderr", ""))
    if isinstance(value, dict) and "content" in value:
        return result_text(value["content"] if isinstance(value["content"], str) else json.dumps(value["content"]))
    if isinstance(value, dict) and "llmContent" in value:
        content = value["llmContent"]
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "\n".join(str(part.get("text", "")) if isinstance(part, dict) else str(part) for part in content)
    return raw


def response_objects(text: str):
    decoder = json.JSONDecoder()
    position = 0
    while (start := text.find("{", position)) >= 0:
        try:
            value, consumed = decoder.raw_decode(text[start:])
        except ValueError:
            position = start + 1
            continue
        position = start + consumed
        if isinstance(value, dict):
            yield value


def receipt_value(receipt: dict[str, Any], path: str) -> Any:
    value: Any = receipt
    for part in path.split("."):
        value = value.get(part) if isinstance(value, dict) else None
    return value


def matches_success(actual: Any, expected: Any) -> bool:
    if isinstance(expected, bool):
        return actual is expected
    return not isinstance(actual, bool) and actual == expected


def is_rejected_value(actual: Any, expected: Any) -> bool:
    if actual is None:
        return False
    if isinstance(expected, bool):
        return actual is (not expected)
    return not matches_success(actual, expected)


def _code_matches(actual: Any, candidates: list[str | int]) -> bool:
    if actual is None or isinstance(actual, bool):
        return False
    for candidate in candidates:
        if isinstance(candidate, bool):
            continue
        if actual == candidate or str(actual).lower() == str(candidate).lower():
            return True
    return False


def _replace_flag(value: Any, flag: str) -> Any:
    if isinstance(value, str):
        return value.replace("{flag}", flag)
    if isinstance(value, list):
        return [_replace_flag(item, flag) for item in value]
    if isinstance(value, dict):
        return {k: _replace_flag(v, flag) for k, v in value.items()}
    return value


def _extract_message_text(receipt: dict[str, Any], raw_text: str) -> str:
    parts: list[str] = []
    if isinstance(receipt, dict):
        for key in ("msg", "message", "detail", "error", "status"):
            val = receipt.get(key)
            if isinstance(val, str) and val.strip():
                parts.append(val)
        data = receipt.get("data")
        if isinstance(data, dict):
            for key in ("msg", "message", "detail", "status"):
                val = data.get(key)
                if isinstance(val, str) and val.strip():
                    parts.append(val)
    if raw_text:
        parts.append(raw_text)
    return " ".join(parts)


def classify_receipt(
    spec: SubmissionSpec,
    receipt: dict[str, Any] | None,
    status: int = 200,
    *,
    raw_text: str = "",
) -> Literal["accepted", "duplicate", "partial_incomplete", "rejected", "error"]:
    body = receipt if isinstance(receipt, dict) else {}
    if "total_flag_count" in body and body.get("correct_flag_count") != body["total_flag_count"]:
        return "partial_incomplete"

    # Explicit duplicate / already-solved check (e.g. HTTP 402 "该团队已解出该题目" or code="duplicate").
    dup_codes: list[str | int] = list(spec.duplicate_codes)
    if spec.duplicate_code is not None and spec.duplicate_code not in dup_codes:
        dup_codes.append(spec.duplicate_code)
    message_text = _extract_message_text(body, raw_text).lower()
    keywords = [kw.lower() for kw in spec.duplicate_keywords if kw]
    keyword_hit = any(kw in message_text for kw in keywords)
    code_hit = _code_matches(body.get("code"), dup_codes)
    if code_hit or (status in {200, 400, 402, 409} and keyword_hit):
        return "duplicate"

    # Explicit success check.
    has_error = bool(body.get("error"))
    if 200 <= status < 300 and not has_error:
        primary = receipt_value(body, spec.success_path)
        if matches_success(primary, spec.success_value):
            if body.get("code") in (None, 0, 200, "ok", "success"):
                return "accepted"
        if primary is None:
            for path, expected in FALLBACK_SUCCESS_CHECKS:
                if matches_success(receipt_value(body, path), expected):
                    if body.get("code") in (None, 0, 200, "ok", "success"):
                        return "accepted"

    # Explicit wrong-answer check.
    primary = receipt_value(body, spec.success_path)
    if is_rejected_value(primary, spec.success_value):
        return "rejected"
    for path, expected in FALLBACK_REJECT_CHECKS:
        if matches_success(receipt_value(body, path), expected):
            return "rejected"
    if status in {200, 400, 422} and any(kw in message_text for kw in REJECT_KEYWORDS):
        return "rejected"
    return "error"


def accepted_receipt(spec: SubmissionSpec, receipt: dict[str, Any], status: int = 200, *, raw_text: str = "") -> bool:
    return classify_receipt(spec, receipt, status, raw_text=raw_text) in {"accepted", "duplicate"}


def payload_matches_command(command: str, spec: SubmissionSpec, source_key: str, *, flag: str | None = None) -> bool:
    normalized = command.replace('\\"', '"').replace("\\'", "'")
    payload = spec.request.body
    has_bound_field = False
    if isinstance(payload, dict):
        for key, value in payload.items():
            if isinstance(value, str):
                if "{flag}" in value:
                    if flag is None:
                        continue
                    expected = value.replace("{flag}", flag)
                else:
                    expected = value
                    has_bound_field = True
                pattern = (
                    r"[\"']" + re.escape(key) + r"[\"']\s*:\s*(?:"
                    + re.escape(json.dumps(expected, ensure_ascii=False)) + "|" + re.escape(repr(expected)) + ")"
                )
                if not re.search(pattern, normalized):
                    return False
            elif isinstance(value, bool):
                has_bound_field = True
                expected_lit = "true" if value else "false"
                py_lit = "True" if value else "False"
                pattern = (
                    r"[\"']" + re.escape(key) + r"[\"']\s*:\s*(?:" + expected_lit + "|" + py_lit + r")(?![\w])"
                )
                if not re.search(pattern, normalized):
                    return False
            elif isinstance(value, int):
                has_bound_field = True
                pattern = r"[\"']" + re.escape(key) + r"[\"']\s*:\s*" + re.escape(str(value)) + r"(?![\w])"
                if not re.search(pattern, normalized):
                    return False
    for param_value in spec.request.params.values():
        if isinstance(param_value, str) and param_value and "{flag}" not in param_value:
            has_bound_field = True
            if param_value not in normalized:
                return False
    if not has_bound_field:
        if not source_key or not re.search(r"(?<![\w-])" + re.escape(source_key) + r"(?![\w-])", command):
            return False
    return True


def _contract_digest(spec: SubmissionSpec) -> str:
    return hashlib.sha256(spec.model_dump_json().encode()).hexdigest()


class Submissions:
    def __init__(self, config, *, client: httpx.AsyncClient | None = None):
        self.config = config
        self.client = client
        self.specs: dict[Any, SubmissionSpec | None] = {}
        self._progress: dict[tuple[Any, str], tuple[Any, Any]] = {}

    def register(self, question_id, spec):
        self.specs[question_id] = spec

    def receipts_root(self, question) -> Path:
        return self.config.task_path(question.id) / "submission-receipts"

    @staticmethod
    def legacy_spec(question, target):
        text = question.metadata.get("submission_instructions", "")
        match = re.search(r"POST\s+(https?://[^\s<>\"']+/openapi/v1/challenges/submit)\b", text)
        start = target.get("start") or {}
        origin = urlsplit(start.get("url", ""))
        if match:
            destination = urlsplit(match[1])
            if ("unique_code" in text and "flag" in text
                    and (origin.scheme, origin.netloc) == (destination.scheme, destination.netloc)):
                return SubmissionSpec(request=TargetRequest(
                    method="POST", url=match[1], headers=start.get("headers", {}),
                    body={"unique_code": question.metadata["source_key"], "flag": "{flag}"}))
        return None

    def spec(self, question) -> SubmissionSpec | None:
        if not question.metadata.get("aoe_auto_submit_flag", True):
            return None
        if question.id in self.specs:
            return self.specs[question.id]
        value = question.metadata.get("submission")
        document = None
        if value is None:
            path = self.config.task_path(question.id) / "question.json"
            document = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
            value = document.get("submission")
        spec = SubmissionSpec.model_validate(value) if value else None
        if spec is None:
            target = question.metadata.get("target")
            if target is None:
                if document is None:
                    path = self.config.task_path(question.id) / "question.json"
                    document = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
                target = document.get("target", {})
            spec = self.legacy_spec(question, target)
        self.specs[question.id] = spec
        return spec

    def write_context(self, question, agent_id: str) -> dict[str, str]:
        spec = self.spec(question)
        if spec is not None:
            # System-managed submission: never hand platform credentials to Workers.
            # Expose only blocked_url for the PreToolUse Hook and flag_format for detection.
            context = {
                "question_id": str(question.id),
                "source_key": question.metadata.get("source_key"),
                "flag_format": question_flag_format(self.config, question),
                "managed_by_system": True,
                "blocked_url": spec.request.url,
                "instructions": (
                    "Flag submission is managed exclusively by the TGA3 host. "
                    "Do NOT call the platform submission endpoint yourself. "
                    "Write the exact flag to /workspace/final-candidate.json (conclusion, rationale, finding_ids); "
                    "TGA3 will submit it automatically and return the real platform receipt."
                ),
            }
        else:
            context = {
                "question_id": str(question.id),
                "source_key": question.metadata.get("source_key"),
                "flag_format": question_flag_format(self.config, question),
                "managed_by_system": False,
                "blocked_url": "",
                "instructions": question.metadata.get("submission_instructions", ""),
            }
        path = self.config.task_path(question.id, "workspaces", agent_id) / ".tga3/submission.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(context, ensure_ascii=False, indent=2) + "\n"
        if not path.is_file() or path.read_text(encoding="utf-8") != content:
            temporary = path.with_suffix(".tmp")
            temporary.write_text(content, encoding="utf-8")
            temporary.replace(path)
        return {
            "submission_path": "/workspace/.tga3/submission.json",
            "submission_instruction": context["instructions"],
        }

    def _find_cached_acceptance(self, question, flag: str, spec: SubmissionSpec) -> str | None:
        digest = _contract_digest(spec)
        root = self.receipts_root(question)
        if root.is_dir():
            for path in sorted(root.glob("*.json"), key=lambda p: p.stat().st_mtime_ns, reverse=True):
                try:
                    record = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if (
                    record.get("outcome") in {"accepted", "duplicate"}
                    and record.get("flag") == flag
                    and record.get("contract") == digest
                ):
                    return str(record["action_id"])
        from .submission_guard import protected_acceptance

        class _Proxy:
            conclusion = flag
            submission_action_id = None

        return protected_acceptance(self.config, question, _Proxy(), spec)

    async def submit(
        self,
        question,
        body,
        *,
        agent_id: str = "",
        client: httpx.AsyncClient | None = None,
    ) -> str | None:
        """Submit the candidate flag directly from the host control plane and persist the receipt."""
        flag = body.conclusion.strip()
        if not flag or len(flag) > 4096:
            raise SubmissionRejected("Final conclusion must contain the exact flag, at most 4096 characters")
        spec = self.spec(question)
        if spec is None:
            return None
        if cached := self._find_cached_acceptance(question, flag, spec):
            return cached

        req = spec.request
        url = _replace_flag(req.url, flag)
        headers = _replace_flag(req.headers, flag)
        params = _replace_flag(req.params, flag)
        payload = _replace_flag(req.body, flag)
        kwargs: dict[str, Any] = {"headers": headers, "params": params}
        if isinstance(payload, (dict, list)):
            kwargs["json"] = payload
        elif payload is not None:
            kwargs["content"] = str(payload).encode("utf-8")

        active_client = client or self.client
        owns = active_client is None
        if owns:
            active_client = httpx.AsyncClient(timeout=30.0)
        try:
            response = await active_client.request(req.method, url, **kwargs)
        except Exception as exc:
            raise SubmissionRejected(f"Platform submission request failed: {type(exc).__name__}: {exc}") from exc
        finally:
            if owns:
                await active_client.aclose()

        raw_text = response.text or ""
        try:
            parsed = response.json()
            receipt = parsed if isinstance(parsed, dict) else {"value": parsed}
        except ValueError:
            receipt = next(response_objects(raw_text), {})

        outcome = classify_receipt(spec, receipt, response.status_code, raw_text=raw_text)
        action_id = f"system-submission-{uuid4().hex[:16]}"
        record = {
            "action_id": action_id,
            "question_id": str(question.id),
            "source_key": question.metadata.get("source_key"),
            "agent_id": agent_id,
            "flag": flag,
            "contract": _contract_digest(spec),
            "url": url,
            "method": req.method,
            "http_status": response.status_code,
            "outcome": outcome,
            "response": receipt,
            "raw_text": raw_text[:4000],
            "submitted_at": time.time(),
        }
        root = self.receipts_root(question)
        root.mkdir(parents=True, exist_ok=True)
        target = root / f"{action_id}.json"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(target)

        if outcome in {"accepted", "duplicate"}:
            return action_id
        if outcome == "partial_incomplete":
            raise SubmissionRejected("Platform accepted only some flags; the question is not complete")
        snippet = raw_text.strip()[:400] or json.dumps(receipt, ensure_ascii=False)[:400]
        if outcome == "rejected":
            raise SubmissionRejected(
                f"Platform rejected flag (HTTP {response.status_code}): {snippet}"
            )
        raise SubmissionRejected(
            f"Platform submission did not confirm acceptance (HTTP {response.status_code}): {snippet}"
        )

    def _actions(self, question, agent_id):
        directory = self.config.task_path(question.id, "logs", agent_id)
        return read_action_log(directory, task_id=question.id, agent_id=agent_id)["actions"]

    def _body(self, question, agent_id, action):
        relative = action.get("artifact", {}).get("output_path")
        if not relative:
            return ""
        workspace = self.config.task_path(question.id, "workspaces", agent_id)
        path = next(iter(evidence_files(workspace, [relative]).values()))
        with path.open(encoding="utf-8", errors="replace") as handle:
            return result_text(handle.read(256 * 1024))

    def validate(self, question, body):
        """Synchronous check against persisted host receipts or historical action logs."""
        spec = self.spec(question)
        if spec is None:
            raise SubmissionMissing("No documented submission contract; collect the endpoint/credentials first")
        flag = body.conclusion.strip()
        if not flag or len(flag) > 4096:
            raise SubmissionRejected("Final conclusion must contain the exact flag, at most 4096 characters")
        if cached := self._find_cached_acceptance(question, flag, spec):
            if not body.submission_action_id or body.submission_action_id == cached:
                return cached
        rejected = False
        for agent_id in self.config.worker_agent_ids:
            for action in reversed(self._actions(question, agent_id)):
                if body.submission_action_id and action["action_id"] != body.submission_action_id:
                    continue
                operation = action.get("action", {})
                command = operation.get("command") or operation.get("input", {}).get("command", "")
                if (operation.get("tool", "").lower() not in EXECUTION_TOOLS
                        or action.get("status") not in {"completed", "failed"}
                        or spec.request.url not in command or flag not in command
                        or not re.search(r"\bcurl\b|urllib\.request|requests\.(?:post|request)|httpx", command)):
                    continue
                if not payload_matches_command(
                    command, spec, str(question.metadata.get("source_key", "")), flag=flag
                ):
                    continue
                try:
                    text = self._body(question, agent_id, action)
                except (OSError, ValueError, ContractError):
                    continue
                statuses = re.findall(r"\bHTTP(?:/\d(?:\.\d)?)?\s*[:= ]\s*(\d{3})", text)
                status = int(statuses[-1]) if statuses else 200
                for receipt in response_objects(text):
                    outcome = classify_receipt(spec, receipt, status, raw_text=text)
                    if outcome == "partial_incomplete":
                        raise SubmissionRejected("Platform accepted only some flags; the question is not complete")
                    if outcome in {"accepted", "duplicate"}:
                        return action["action_id"]
                    if outcome == "rejected":
                        rejected = True
        if rejected:
            raise SubmissionRejected("The platform rejected this flag; correct the answer before publishing")
        raise SubmissionMissing("Missing a platform acceptance receipt bound to this question and exact flag")

    def recent_flag(self, question, attempt):
        """Inspect recent execution bodies once per changed journal, only in the closing window."""
        agent_id = attempt["agent_id"]
        path = self.config.task_path(question.id, "logs", agent_id) / f"{agent_id}-actions.json"
        if not path.is_file():
            return None
        signature = (path.stat().st_mtime_ns, path.stat().st_size, question_flag_format(self.config, question))
        key = (question.id, attempt["id"])
        if self._progress.get(key, (None, None))[0] == signature:
            return self._progress[key][1]
        found = None
        for action, _flag in self.flag_outputs(question, attempt, self._actions(question, agent_id)[-20:]):
            found = action["action_id"]
            break
        self._progress[key] = (signature, found)
        return found

    def flag_outputs(self, question, attempt, actions):
        """Yield exact flags from current-attempt output, newest first; never from command literals."""
        agent_id = attempt["agent_id"]
        pattern = flag_pattern(question_flag_format(self.config, question))
        for action in reversed(actions):
            operation = action.get("action", {})
            native_read = (
                operation.get("tool", "").lower() == "read"
                and operation.get("input", {}).get("file_path", "").startswith("/workspace/")
                and not re.search(r"/\.tga3/|\.(?:py|md)$", operation.get("input", {}).get("file_path", ""))
            )
            if ((operation.get("tool", "").lower() not in EXECUTION_TOOLS and not native_read)
                    or action.get("status") != "completed"):
                continue
            from datetime import datetime

            started = action.get("started_at")
            if not started or datetime.fromisoformat(started).timestamp() < attempt["started_at"]:
                continue
            command = operation.get("command") or operation.get("input", {}).get("command", "")
            if re.search(
                r"github|ctftime|writeup|/experience/|/skills/|/logs|/task-overview/|"
                r"/evidence-files/|/evidence-logs/|hook_outputs",
                command,
                re.I,
            ):
                continue
            try:
                text = self._body(question, agent_id, action)
            except (OSError, ValueError, ContractError):
                continue
            for match in pattern.finditer(text):
                flag = match[0]
                if flag not in command and not re.search(r"\.\.\.|<|>|example|local_test", flag, re.I):
                    yield action, flag
