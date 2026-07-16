"""BreachWeave-style persistent AgentSession for the product runtime.

The model owns one native tool loop.  Assistant tool-call envelopes and tool
results stay in the same conversation instead of being flattened into a
Manager-created hypothesis and a synthetic one-action planning request.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from tga.capabilities.registry import build_default_registry
from tga.contracts import ActionResult, ActionSpec, ArtifactRecord, SolverRecord, TGATask
from tga.evidence.artifacts import ArtifactStore
from tga.evidence.store import EvidenceStore, utc_now
from tga.runtime.events import EventStore
from tga.runtime.session import AgentSession as DurableSession
from tga.runtime.solver_session import SolverSessionState


FINISH_TOOL = "finish_session"


class AgentToolSession:
    """A durable, native function-calling session with direct tool feedback."""

    def __init__(
        self,
        *,
        task: TGATask,
        store: EvidenceStore,
        run_root: Path,
        client: Any,
        executor: Any,
        max_turns: int,
    ) -> None:
        self.task = task
        self.store = store
        self.run_root = run_root
        self.client = client
        self.executor = executor
        self.max_turns = max_turns
        self.events = EventStore(store)
        self.registry = build_default_registry()
        self.solver_id = self._ensure_solver()
        self.workspace = SolverSessionState(
            run_root=run_root, task_id=task.id, solver_id=self.solver_id
        ).workspace
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.session_dir = run_root / task.id / "solvers" / self.solver_id / "session"
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.transcript_path = self.session_dir / "messages.json"
        self.messages = self._load_messages()
        self.tool_by_name = self._build_tool_map()
        self.last_artifact_id: str | None = self._latest_artifact_id()

    def run(self) -> dict[str, Any]:
        session = self.store.get_session(self.task.id)
        if session is None:
            session = DurableSession(
                store=self.store, run_root=self.run_root, task_id=self.task.id
            ).ensure(max_turns=self.max_turns)
        if session.status in {"completed", "cancelled", "failed", "paused"}:
            return self.store.task_snapshot(self.task.id)

        if not self.messages:
            self.messages = [
                {"role": "system", "content": self._system_prompt()},
                {"role": "user", "content": self._initial_prompt()},
            ]
        session = self.store.update_session(
            self.task.id,
            status="running",
            active_solver_id=self.solver_id,
            started_at=session.started_at or utc_now(),
            finished_at=None,
            stop_reason="",
        )
        self.store.update_solver(self.solver_id, status="running", finished_at=None)
        self.events.append(
            self.task.id,
            "SESSION_STARTED",
            {"max_turns": session.max_turns, "runtime": "agent_session"},
            solver_id=self.solver_id,
        )
        self.events.append(
            self.task.id,
            "SOLVER_STARTED",
            {"role": "main", "model_name": getattr(self.client, "model", "")},
            solver_id=self.solver_id,
        )

        plain_turns = 0
        while True:
            session = self.store.get_session(self.task.id)
            if session is None or session.status != "running":
                break
            if session.turn_count >= session.max_turns:
                self._stop("blocked", "session_turn_limit")
                break

            self._sync_hints()

            self.events.append(
                self.task.id,
                "MESSAGE_START",
                {"role": "assistant", "turn": session.turn_count + 1},
                solver_id=self.solver_id,
            )
            try:
                response = self.client.chat_tools(
                    self.messages,
                    tools=self._tool_definitions(),
                    temperature=0.2,
                )
            except Exception as exc:
                # A provider/protocol error is recoverable.  Keep the session
                # resumable and show the actual error instead of fabricating
                # several waiting Solvers and a generic planning failure.
                self.events.append(
                    self.task.id,
                    "AGENT_ERROR",
                    {"phase": "model_turn", "message": str(exc)[:1000]},
                    solver_id=self.solver_id,
                )
                self._stop("blocked", "model_request_failed")
                break

            message = self._normalize_assistant_message(response["message"])
            self.messages.append(message)
            self._save_messages()
            tool_calls = message.get("tool_calls") or []
            content = self._message_text(message.get("content"))
            self.events.append(
                self.task.id,
                "MESSAGE_END",
                {
                    "role": "assistant",
                    "content": content[:12000],
                    "tool_calls": [
                        {
                            "id": item.get("id"),
                            "name": (item.get("function") or {}).get("name"),
                            "arguments": str((item.get("function") or {}).get("arguments") or "")[:4000],
                        }
                        for item in tool_calls
                        if isinstance(item, dict)
                    ],
                    "finish_reason": response.get("finish_reason"),
                },
                solver_id=self.solver_id,
            )

            session = self.store.update_session(
                self.task.id, turn_count=session.turn_count + 1
            )
            if not tool_calls:
                flag = self._first_flag(content)
                if flag:
                    artifact_id = self.last_artifact_id or self._session_result_artifact(content, flag)
                    self._accept_flag(flag, artifact_id)
                    self._stop("completed", "solver_returned_flag")
                    break
                plain_turns += 1
                if plain_turns >= 2:
                    self._stop("blocked", "agent_stopped_without_finish")
                    break
                self.messages.append(
                    {
                        "role": "user",
                        "content": "Continue the task with a concrete tool call. Use finish_session only when the requested outcome is ready.",
                    }
                )
                self._save_messages()
                continue

            plain_turns = 0
            terminal = False
            for call in tool_calls:
                result = (
                    {"ok": False, "cancelled": True, "reason": "session completed by an earlier tool call"}
                    if terminal
                    else self._handle_tool_call(call)
                )
                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": str(call.get("id") or ""),
                        "name": str((call.get("function") or {}).get("name") or ""),
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )
                self._save_messages()
                if result.get("terminal"):
                    terminal = True
            DurableSession(
                store=self.store, run_root=self.run_root, task_id=self.task.id
            ).checkpoint()
            if terminal:
                break

        self._save_messages()
        DurableSession(
            store=self.store, run_root=self.run_root, task_id=self.task.id
        ).checkpoint()
        return self.store.task_snapshot(self.task.id)

    def _ensure_solver(self) -> str:
        session = self.store.get_session(self.task.id)
        records = self.store.list_solvers(self.task.id)
        # Runs created by the abandoned role-fanout refactor can contain three
        # waiting pseudo-Solvers before any action happened. They are retired
        # when the task first enters the native AgentSession path.
        for item in records:
            if item.role == "main" or item.status not in {"starting", "running", "waiting"}:
                continue
            self.store.update_solver(item.id, status="cancelled", finished_at=utc_now())
            self.store.update_subagent_status(item.id, "cancelled")
        active_id = session.active_solver_id if session else None
        existing = next((item for item in records if item.id == active_id), None)
        if existing is None:
            existing = next((item for item in records if item.role == "main"), None)
        if existing is not None:
            return existing.id
        solver_id = f"solver_{uuid4().hex[:12]}"
        self.store.add_solver(
            SolverRecord(
                id=solver_id,
                task_id=self.task.id,
                role="main",
                status="running",
                model_name=getattr(self.client, "model", "configured-model"),
                started_at=utc_now(),
            )
        )
        return solver_id

    def _system_prompt(self) -> str:
        return (
            "You are a persistent cybersecurity AgentSession. Work directly with the provided tools and keep going until the task is complete. "
            "The target supplied by the user is authorized for this session; do not ask for separate scope, intensity, risk, or active-scan settings. "
            "Tool results are returned to this same conversation. Use finish_session when done. Do not emit a JSON action plan and do not wait for a Manager hypothesis assignment."
        )

    def _initial_prompt(self) -> str:
        hints = [
            {"id": item.id, "content": item.content}
            for item in self.store.list_memory(self.task.id)
            if item.kind == "hint"
        ]
        return json.dumps(
            {
                "session": self.task.name,
                "mode": self.task.mode,
                "target": self.task.target,
                "goal": self.task.goal,
                "target_theme": self.task.target_theme,
                "target_description": self.task.target_description,
                "flag_format": self.task.flag_format,
                "hints": hints,
            },
            ensure_ascii=False,
        )

    def _sync_hints(self) -> None:
        rendered = json.dumps(self.messages, ensure_ascii=False)
        changed = False
        for item in self.store.list_memory(self.task.id):
            if item.kind != "hint" or item.id in rendered:
                continue
            self.messages.append(
                {"role": "user", "content": f"Session hint [{item.id}]:\n{item.content}"}
            )
            changed = True
        if changed:
            self._save_messages()

    def _build_tool_map(self) -> dict[str, str]:
        values: dict[str, str] = {}
        for item in self.registry.snapshot()["capabilities"]:
            if self.task.mode not in item["modes"]:
                continue
            values[self._provider_tool_name(item["name"])] = item["name"]
        return values

    def _tool_definitions(self) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        snapshot = {item["name"]: item for item in self.registry.snapshot()["capabilities"]}
        for provider_name, capability in self.tool_by_name.items():
            item = snapshot[capability]
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": provider_name,
                        "description": item.get("description") or f"Execute {capability}",
                        "parameters": item["input_schema"],
                    },
                }
            )
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": FINISH_TOOL,
                    "description": "Finish this session with the final result.",
                    "parameters": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "summary": {"type": "string"},
                            "flag": {"type": "string"},
                        },
                        "required": ["summary"],
                    },
                },
            }
        )
        return tools

    def _handle_tool_call(self, call: dict[str, Any]) -> dict[str, Any]:
        function = call.get("function") or {}
        name = str(function.get("name") or "")
        raw_arguments = function.get("arguments") or "{}"
        try:
            arguments = raw_arguments if isinstance(raw_arguments, dict) else json.loads(raw_arguments)
        except (TypeError, json.JSONDecodeError) as exc:
            return {"ok": False, "error": f"invalid tool arguments: {exc}"}
        if not isinstance(arguments, dict):
            return {"ok": False, "error": "tool arguments must be an object"}

        if name == FINISH_TOOL:
            summary = str(arguments.get("summary") or "").strip()
            flag = str(arguments.get("flag") or "").strip()
            if flag:
                artifact_id = self.last_artifact_id or self._session_result_artifact(summary, flag)
                self._accept_flag(flag, artifact_id)
            self.events.append(
                self.task.id,
                "AGENT_FINISHED",
                {"summary": summary[:4000], "flag": flag or None},
                solver_id=self.solver_id,
            )
            self._stop("completed", "agent_finished")
            return {"ok": True, "terminal": True, "status": "completed"}

        capability = self.tool_by_name.get(name)
        if capability is None:
            return {"ok": False, "error": f"unknown tool: {name}"}
        registered = self.registry.get(capability)
        if registered is None:
            return {"ok": False, "error": f"capability unavailable: {capability}"}
        try:
            self.registry.validate(capability, arguments)
        except Exception as exc:
            return {"ok": False, "error": f"invalid {capability} arguments: {str(exc)[:800]}"}

        action_id = f"act_{uuid4().hex[:12]}"
        risk = registered.spec.risk
        if capability == "http.request" and str(arguments.get("method") or "GET").upper() != "GET":
            risk = "active"
        action = ActionSpec(
            id=action_id,
            task_id=self.task.id,
            solver_id=self.solver_id,
            hypothesis_id=f"session_{self.solver_id}",
            kind=registered.spec.kind,
            capability=capability,
            target=self.task.target,
            arguments=arguments,
            rationale="native AgentSession tool call",
            risk=risk,
        )
        self.store.add_action(action, status="running")
        self.events.append(
            self.task.id,
            "TOOL_EXECUTION_START",
            {"tool_call_id": call.get("id"), "action_id": action_id, "tool_name": name, "arguments": arguments},
            solver_id=self.solver_id,
        )
        try:
            result = self.executor.execute(
                task=self._execution_task(arguments),
                action=action,
                workspace=self.workspace,
            )
        except Exception as exc:
            result = ActionResult(
                action_id=action.id,
                task_id=self.task.id,
                solver_id=self.solver_id,
                status="failed",
                summary=f"tool raised: {str(exc)[:800]}",
            )
        self.store.add_action_result(result)
        self.store.update_action_status(action.id, result.status)
        excerpts: list[dict[str, str]] = []
        for artifact_id in result.artifact_ids:
            artifact = self._register_artifact(artifact_id, capability, action.target)
            if artifact is None:
                continue
            self.last_artifact_id = artifact.id
            excerpts.append({"artifact_id": artifact.id, "content": self._artifact_excerpt(artifact)})
        for candidate in result.candidate_flags:
            if self.last_artifact_id:
                self._accept_flag(candidate, self.last_artifact_id)
        payload = {
            "ok": result.status == "succeeded",
            "status": result.status,
            "summary": result.summary,
            "facts": result.facts,
            "leads": result.leads,
            "candidate_flags": result.candidate_flags,
            "artifacts": excerpts,
            "error": result.error.model_dump(mode="json") if result.error else None,
        }
        self.events.append(
            self.task.id,
            "TOOL_EXECUTION_END",
            {"tool_call_id": call.get("id"), "action_id": action_id, "tool_name": name, **payload},
            solver_id=self.solver_id,
        )
        if result.candidate_flags:
            self._stop("completed", "flag_observed")
            payload["terminal"] = True
        return payload

    def _execution_task(self, arguments: dict[str, Any]) -> TGATask:
        # The Session target is the authorization contract, matching
        # BreachWeave challenge sessions.  Legacy scope/intensity switches do
        # not participate in product execution anymore.
        requested = str(arguments.get("url") or arguments.get("target") or self.task.target)
        parsed = urlparse(requested)
        origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else self.task.target
        insecure = [origin] if origin.startswith("https://") else []
        return self.task.model_copy(
            update={
                "scope": ["*"],
                "intensity": "active",
                "allow_active_scan": True,
                "insecure_tls_origins": insecure,
            }
        )

    def _register_artifact(self, artifact_id: str, tool: str, target: str) -> ArtifactRecord | None:
        known = self.store.get_artifact(artifact_id)
        if known is not None:
            return known
        root = self.run_root / self.task.id / "artifacts"
        matches = list(root.glob(f"{artifact_id}.*"))
        if len(matches) != 1:
            return None
        path = matches[0]
        data = path.read_bytes()
        kind = "http_response" if tool == "http.request" else "tool_output"
        artifact = ArtifactRecord(
            id=artifact_id,
            task_id=self.task.id,
            intent_id=None,
            kind=kind,
            path=path.name,
            sha256=hashlib.sha256(data).hexdigest(),
            tool=tool,
            target=target,
            created_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        )
        self.store.add_artifact(artifact)
        return artifact

    def _artifact_excerpt(self, artifact: ArtifactRecord, limit: int = 16_000) -> str:
        path = self.run_root / self.task.id / "artifacts" / artifact.path
        try:
            return path.read_bytes()[:limit].decode("utf-8", errors="replace")
        except OSError:
            return ""

    def _accept_flag(self, value: str, artifact_id: str) -> None:
        value = value.strip()
        if not value:
            return
        existing = {item["value"] for item in self.store.task_snapshot(self.task.id).get("flags") or []}
        if value not in existing:
            self.store.add_flag(self.task.id, value, artifact_id)
            self.events.append(
                self.task.id,
                "FLAG_FOUND",
                {"value": value, "artifact_id": artifact_id},
                solver_id=self.solver_id,
            )
        challenge = self.store.get_challenge(self.task.id)
        if challenge is not None:
            self.store.upsert_challenge(
                challenge.model_copy(
                    update={
                        "status": "solved",
                        "completion_proof_artifact_id": artifact_id,
                        "status_reason": "agent_session_flag",
                        "solved_at": utc_now(),
                    }
                )
            )

    def _session_result_artifact(self, summary: str, flag: str) -> str:
        artifact = ArtifactStore(self.run_root / self.task.id / "artifacts").save_text(
            task_id=self.task.id,
            intent_id=None,
            kind="report",
            text=json.dumps({"summary": summary, "result": flag}, ensure_ascii=False, indent=2),
            tool=FINISH_TOOL,
            target=self.task.target,
            suffix=".json",
        )
        self.store.add_artifact(artifact)
        self.last_artifact_id = artifact.id
        return artifact.id

    def _stop(self, status: str, reason: str) -> None:
        finished = utc_now()
        self.store.update_session(
            self.task.id, status=status, stop_reason=reason, finished_at=finished
        )
        self.store.update_solver(
            self.solver_id,
            status="completed" if status == "completed" else "waiting",
            finished_at=finished,
        )
        self.events.append(
            self.task.id,
            "SESSION_STOPPED",
            {"status": status, "reason": reason},
            solver_id=self.solver_id,
        )

    def _load_messages(self) -> list[dict[str, Any]]:
        if not self.transcript_path.is_file():
            return []
        try:
            value = json.loads(self.transcript_path.read_text(encoding="utf-8"))
            return value if isinstance(value, list) else []
        except (OSError, json.JSONDecodeError):
            return []

    def _save_messages(self) -> None:
        temporary = self.transcript_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.messages, ensure_ascii=False, indent=2), encoding="utf-8", errors="replace")
        temporary.replace(self.transcript_path)

    def _latest_artifact_id(self) -> str | None:
        artifacts = self.store.task_snapshot(self.task.id).get("artifacts") or []
        return str(artifacts[-1]["id"]) if artifacts else None

    def _first_flag(self, text: str) -> str | None:
        if not text or not self.task.flag_format:
            return None
        try:
            match = re.search(self.task.flag_format, text)
        except re.error:
            return None
        return match.group(0) if match else None

    @staticmethod
    def _provider_tool_name(capability: str) -> str:
        return f"tga_{re.sub(r'[^A-Za-z0-9_-]+', '_', capability)}"

    @staticmethod
    def _message_text(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "\n".join(
                str(item.get("text") or "") for item in content if isinstance(item, dict)
            )
        return ""

    @staticmethod
    def _normalize_assistant_message(message: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in message.items()
            if key in {"role", "content", "reasoning_content", "tool_calls"}
        }
