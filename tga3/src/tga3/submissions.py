"""File-backed submission contracts and receipt checks; never submits on an agent's behalf."""

from __future__ import annotations

import json
import re
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

from .action_logs import read_action_log
from .errors import ContractError
from .evidence import evidence_files
from .flag_signals import FLAG
from .target_instances import TargetRequest


class SubmissionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request: TargetRequest
    success_path: str = Field(default="correct", min_length=1, max_length=200)
    duplicate_code: str | None = "duplicate"


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
        return result_text(value["content"] if isinstance(value['content'], str) else json.dumps(value['content']))
    if isinstance(value, dict) and 'llmContent' in value:
        content = value['llmContent']
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return '\n'.join(str(part.get('text', '')) if isinstance(part, dict) else str(part) for part in content)
    return raw


def response_objects(text):
    decoder = json.JSONDecoder()
    position = 0
    while (start := text.find('{', position)) >= 0:
        try:
            value, consumed = decoder.raw_decode(text[start:])
        except ValueError:
            position = start + 1
            continue
        position = start + consumed
        if isinstance(value, dict):
            yield value


class Submissions:
    def __init__(self, config):
        self.config = config
        self.specs = {}
        self._progress = {}

    def register(self, question_id, spec):
        self.specs[question_id] = spec

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

    def spec(self, question):
        if question.id in self.specs:
            return self.specs[question.id]
        path = self.config.task_path(question.id) / "question.json"
        document = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        value = document.get("submission")
        spec = SubmissionSpec.model_validate(value) if value else None
        # Legacy TSec records already document the exact endpoint and payload.
        # Never synthesize a path from a hostname, nor borrow credentials across origins.
        if spec is None:
            spec = self.legacy_spec(question, document.get("target", {}))
        self.specs[question.id] = spec
        return spec

    def write_context(self, question, agent_id):
        spec = self.spec(question)
        context = {"question_id": str(question.id), "source_key": question.metadata.get("source_key"),
                   "submission": spec.model_dump(mode="json") if spec else None,
                   "instructions": question.metadata.get("submission_instructions", ""),
                   "flag_placeholder": "{flag}",
                   "receipt_requirement": "Save the actual platform response in the command output. "
                   "Use the exact flag and original question ID in the submission command, then cite "
                   "submission_action_id in final-candidate.json. A wrong answer is not success."}
        path = self.config.task_path(question.id, "workspaces", agent_id) / ".tga3/submission.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(context, ensure_ascii=False, indent=2) + "\n"
        if not path.is_file() or path.read_text(encoding="utf-8") != content:
            temporary = path.with_suffix(".tmp")
            temporary.write_text(content, encoding="utf-8")
            temporary.replace(path)
        return {"submission_path": "/workspace/.tga3/submission.json",
                "submission_instruction": "The submission endpoint and credentials have been sent again in "
                "/workspace/.tga3/submission.json. Read that file, submit the exact flag, retain the actual "
                "response, then retry final-candidate.json. Do not mark an unsubmitted flag solved."}

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
        spec = self.spec(question)
        if spec is None:
            raise SubmissionMissing("No documented submission contract; collect the endpoint/credentials first")
        flag = body.conclusion.strip()
        if not flag or len(flag) > 4096:
            raise SubmissionRejected("Final conclusion must contain the exact flag, at most 4096 characters")
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
                        or str(question.metadata.get("source_key", "")) not in command
                        or not re.search(r"\bcurl\b|urllib\.request|requests\.(?:post|request)|httpx", command)):
                    continue
                # Bind literal JSON request fields, not a substring such as g-1 in g-10.
                normalized = command.replace('\\"', '"').replace("\\'", "'")
                payload = spec.request.body
                if isinstance(payload, dict):
                    literals = {k: (flag if v == '{flag}' else v) for k, v in payload.items()
                                if isinstance(v, str)}
                    if any(not re.search(
                            r"[\"']" + re.escape(k) + r"[\"']\s*:\s*(?:" +
                            re.escape(json.dumps(v, ensure_ascii=False)) + '|' + re.escape(repr(v)) + ')', normalized)
                           for k, v in literals.items()):
                        continue
                try:
                    text = self._body(question, agent_id, action)
                except (OSError, ValueError, ContractError):
                    continue
                for receipt in response_objects(text):
                    value = receipt
                    for part in spec.success_path.split("."):
                        value = value.get(part) if isinstance(value, dict) else None
                    if value is False:
                        rejected = True
                    duplicate = spec.duplicate_code and receipt.get("code") == spec.duplicate_code
                    if value is True or duplicate:
                        statuses = re.findall(r"\bHTTP(?:/\d(?:\.\d)?)?\s*[:= ]\s*(\d{3})", text)
                        if ((statuses and int(statuses[-1]) >= 400 and not duplicate)
                                or not duplicate and receipt.get('code') not in (None, 0, 200, 'ok', 'success')):
                            continue
                        if ("total_flag_count" in receipt and
                                receipt.get("correct_flag_count") != receipt["total_flag_count"]):
                            raise SubmissionRejected("Platform accepted only some flags; the question is not complete")
                        return action["action_id"]
        if rejected:
            raise SubmissionRejected("The platform rejected this flag; correct the answer before publishing")
        raise SubmissionMissing("Missing a platform acceptance receipt bound to this question and exact flag")

    def recent_flag(self, question, attempt):
        """Inspect recent execution bodies once per changed journal, only in the closing window."""
        agent_id = attempt["agent_id"]
        path = self.config.task_path(question.id, "logs", agent_id) / f"{agent_id}-actions.json"
        if not path.is_file():
            return None
        signature = (path.stat().st_mtime_ns, path.stat().st_size)
        key = (question.id, attempt["id"])
        if self._progress.get(key, (None, None))[0] == signature:
            return self._progress[key][1]
        found = None
        for action in reversed(self._actions(question, agent_id)[-20:]):
            operation = action.get("action", {})
            native_read = (operation.get('tool', '').lower() == 'read'
                           and operation.get('input', {}).get('file_path', '').startswith('/workspace/')
                           and not re.search(r"/\.tga3/|\.(?:py|md)$", operation.get('input', {}).get('file_path', '')))
            if ((operation.get("tool", "").lower() not in EXECUTION_TOOLS and not native_read)
                    or action.get("status") != "completed"):
                continue
            from datetime import datetime

            started = action.get("started_at")
            if not started or datetime.fromisoformat(started).timestamp() < attempt["started_at"]:
                continue
            command = operation.get("command") or operation.get("input", {}).get("command", "")
            if re.search(r"github|ctftime|writeup|/experience/|/skills/|/logs|/task-overview/|"
                         r"/evidence-files/|/evidence-logs/|hook_outputs", command, re.I):
                continue
            try:
                text = self._body(question, agent_id, action)
            except (OSError, ValueError, ContractError):
                continue
            for match in FLAG.finditer(text):
                flag = match[0]
                if flag not in command and not re.search(r"\.\.\.|<|>|example|local_test", flag, re.I):
                    found = action["action_id"]
                    break
            if found:
                break
        self._progress[key] = (signature, found)
        return found
