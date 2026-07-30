"""Action, Artifact, Strategy, and Observer services for tool handlers."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from tga.contracts import ActionResult, ActionSpec, ArtifactRecord
from tga.evidence.artifacts import ArtifactStore
from tga.evidence.indexing import build_artifact_index, retrieve_segments
from tga.inputs import task_artifact_root
from tga.runtime.observer import build_observer_context, native_observer_triggers

class StrategyResolver:
    def __init__(self, state: Any) -> None:
        self.task = state.task
        self.store = state.store

    def resolve(self, governance: dict[str, Any]):
        if self.task.schema_version == 6:
            return None, None
        return self._resolve_strategy_step(governance)

    def _resolve_strategy_step(self, governance: dict[str, Any]):
        cards = self.store.list_strategy_cards(self.task.id)
        requested_card = str(governance.get("strategy_card_id") or "")
        card = next((item for item in cards if item.id == requested_card), None)
        if card is None:
            card = next((item for item in cards if item.active_step_id), cards[-1] if cards else None)
        if card is None:
            return None, None
        requested_step = str(governance.get("strategy_step_id") or "")
        step = next((item for item in card.steps if item.id == requested_step), None)
        if step is None:
            step = next((item for item in card.steps if item.id == card.active_step_id), None)
        if step is None:
            step = next((item for item in card.steps if item.status in {"pending", "testing"}), None)
        return card, step
class ActionRecorder:
    """Atomically records governed action state and results."""

    def __init__(self, state: Any) -> None:
        self.task = state.task
        self.store = state.store

    def start(self, action: ActionSpec) -> None:
        with self.store.transaction():
            self.store.add_action(action, status="running")

    def pending(self, action: ActionSpec) -> str:
        try:
            seconds = max(60, min(int(os.environ.get("TGA_APPROVAL_TIMEOUT_SECONDS", "900")), 86_400))
        except ValueError:
            seconds = 900
        expires_at = (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")
        with self.store.transaction():
            self.store.add_action(action, status="pending_approval", approval_expires_at=expires_at)
        return expires_at

    def resume_approved(self, action: ActionSpec) -> None:
        with self.store.transaction():
            self.store.update_action_status(action.id, "running", expected_status="approved")

    def block(self, action: ActionSpec, result: ActionResult) -> None:
        with self.store.transaction():
            self.store.add_action(action, status="blocked")
            self.store.add_action_result(result)

    def finish(self, action: ActionSpec, result: ActionResult) -> None:
        with self.store.transaction():
            self.store.add_action_result(result)
            self.store.update_action_status(action.id, result.status)

    def semantic_repeat(self, action: ActionSpec) -> str | None:
        fingerprint = self._action_fingerprint(action.capability, action.target, action.arguments)
        for item in reversed(self.store.list_actions(self.task.id)):
            if item.get("status") not in {"succeeded", "failed", "blocked"}:
                continue
            if self._action_fingerprint(
                str(item.get("capability") or ""), str(item.get("target") or ""), item.get("arguments") or {}
            ) == fingerprint:
                return str(item.get("id"))
        return None
    @staticmethod
    def _action_fingerprint(capability: str, target: str, arguments: dict[str, Any]) -> str:
        normalized = {key: value for key, value in arguments.items() if key not in {"timeout", "_tga"}}
        if "headers" in normalized:
            normalized["headers"] = sorted(
                key.casefold() for key in (normalized.get("headers") or {})
                if not re.search(r"authorization|cookie|token|secret|key", key, re.IGNORECASE)
            )
        if "body" in normalized:
            body = normalized.pop("body")
            normalized["body_sha256"] = hashlib.sha256(
                json.dumps(body, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8", errors="replace")
            ).hexdigest()
        raw = json.dumps([capability, target, normalized], ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()


class ArtifactService:
    def __init__(self, state: Any) -> None:
        self.task = state.task
        self.store = state.store
        self.run_root = state.run_root
        self.strategies = state.strategies
        self.solver_id = state.solver_id

    def save_input_evidence(
        self,
        *,
        item: Any,
        operation: str,
        payload: dict[str, Any],
        raw: bytes | None = None,
    ) -> tuple[ArtifactRecord, bool]:
        return self._save_input_evidence(item=item, operation=operation, payload=payload, raw=raw)

    def remove_file(self, artifact: ArtifactRecord) -> None:
        root = task_artifact_root(self.run_root / self.task.id, self.task)
        path = (root / artifact.path).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            return
        path.unlink(missing_ok=True)

    def index(self, artifact: ArtifactRecord):
        return self._index_artifact(artifact)

    def attach_strategy_source(self, **kwargs: Any) -> None:
        self._attach_strategy_source(**kwargs)

    def expected_marker_found(self, result: ActionResult) -> bool | None:
        return self._expected_marker_found(result)

    def http_session_metadata(self, result: ActionResult) -> dict | None:
        return self._http_session_metadata(result)

    def register(self, artifact_id: str, tool: str, target: str, **kwargs: Any) -> ArtifactRecord | None:
        return self._register_artifact(artifact_id, tool, target, **kwargs)

    def excerpt(self, artifact: ArtifactRecord, limit: int = 16_000) -> str:
        return self._artifact_excerpt(artifact, limit=limit)

    def text(self, task_id: str, artifact: ArtifactRecord) -> str:
        return self._artifact_text(task_id, artifact)

    def _save_input_evidence(
        self,
        *,
        item: Any,
        operation: str,
        payload: dict[str, Any],
        raw: bytes | None,
    ) -> tuple[ArtifactRecord, bool]:
        source_provenance = item.provenance.model_dump(mode="json")
        provenance = {
            **source_provenance,
            "input_id": item.id,
            "operation": operation,
            "source_sha256": item.sha256,
            "source_size": item.size,
            "workspace_path": item.relative_path,
            "container_path": item.container_path,
            "immutable": True,
        }
        root = task_artifact_root(self.run_root / self.task.id, self.task)
        store = ArtifactStore(root)
        if operation == "input_read":
            provenance.update({
                "offset": int(payload.get("offset") or 0),
                "next_offset": int(payload.get("next_offset") or 0),
                "eof": bool(payload.get("eof")),
            })
            evidence = {
                "schema_version": 1,
                "input_id": item.id,
                "operation": operation,
                "source_sha256": item.sha256,
                "offset": payload.get("offset"),
                "next_offset": payload.get("next_offset"),
                "eof": payload.get("eof"),
                "content": payload.get("content") or "",
            }
            encoded = json.dumps(evidence, ensure_ascii=False, indent=2).encode("utf-8")
            suffix = ".input.json"
        else:
            encoded = raw if raw is not None else b""
            suffix = ".input.bin"
        digest = hashlib.sha256(encoded).hexdigest()
        identity_context = f"{self.task.id}:{item.id}:{operation}"
        identity = hashlib.sha256(identity_context.encode("utf-8") + b"\0" + encoded).hexdigest()
        artifact_id = f"artifact_{identity[:12]}"
        known = self.store.get_artifact(artifact_id)
        if known is not None:
            if known.task_id != self.task.id or known.sha256 != digest:
                raise ValueError("content-addressed Artifact identity conflict")
            enriched = known.model_copy(update={
                "tool": operation,
                "target": item.container_path,
                "input_id": item.id,
                "provenance": provenance,
            })
            return enriched, False
        path = root / f"{artifact_id}{suffix}"
        created = not path.exists()
        artifact = store.save_bytes(
            task_id=self.task.id,
            intent_id=None,
            kind="file",
            data=encoded,
            tool=operation,
            target=item.container_path,
            suffix=suffix,
            identity_context=identity_context,
        ).model_copy(update={"input_id": item.id, "provenance": provenance})
        return artifact, created

    def _index_artifact(self, artifact: ArtifactRecord):
        existing = self.store.get_artifact_index(artifact.id)
        if existing is not None:
            return existing
        if artifact.tool == "input_materialize":
            return None
        path = task_artifact_root(self.run_root / self.task.id, self.task) / artifact.path
        try:
            raw = path.read_bytes()
        except OSError:
            return None
        index = build_artifact_index(
            task_id=self.task.id,
            artifact_id=artifact.id,
            raw=raw,
            document_type="html" if path.suffix.casefold() in {".html", ".htm"} else None,
        )
        return self.store.upsert_artifact_index(index)
    def _attach_strategy_source(self, *, action: ActionSpec, artifact: ArtifactRecord, index) -> None:
        if self.task.schema_version >= 6:
            return
        if action.capability != "http.request" or artifact.kind != "http_body":
            return
        requested = str(action.arguments.get("url") or "")
        if not requested:
            requested = urljoin(action.target.rstrip("/") + "/", str(action.arguments.get("path") or ""))
        for card in self.store.list_strategy_cards(self.task.id):
            if not any(source.url in {requested, artifact.target} for source in card.sources if source.url):
                continue
            updated = self.strategies.attach_index(card=card, url=next(
                source.url for source in card.sources if source.url in {requested, artifact.target}
            ), index=index)
            event_type = "HINT_EXTRACTED" if index.extraction_status == "extracted" else "HINT_EXTRACTION_FAILED"
            self.store.append_agent_event(
                self.task.id,
                event_type,
                {
                    "strategy_card_id": updated.id,
                    "artifact_id": artifact.id,
                    "extraction_status": index.extraction_status,
                    "segment_count": len(index.segments),
                },
                solver_id=self.solver_id,
            )
    def _expected_marker_found(self, result: ActionResult) -> bool | None:
        for artifact_id in result.artifact_ids:
            artifact = self.store.get_artifact(artifact_id)
            if artifact is None or artifact.kind != "http_response":
                continue
            try:
                payload = json.loads(self._artifact_text(self.task.id, artifact))
            except json.JSONDecodeError:
                continue
            marker = payload.get("expected_marker") if isinstance(payload, dict) else None
            if isinstance(marker, dict):
                return bool(marker.get("found"))
        return None
    def _http_session_metadata(self, result: ActionResult) -> dict | None:
        for artifact_id in result.artifact_ids:
            artifact = self.store.get_artifact(artifact_id)
            if artifact is None or artifact.kind != "http_response":
                continue
            try:
                payload = json.loads(self._artifact_text(self.task.id, artifact))
            except json.JSONDecodeError:
                continue
            metadata = payload.get("http_session") if isinstance(payload, dict) else None
            if isinstance(metadata, dict):
                return metadata
        return None
    def _register_artifact(
        self, artifact_id: str, tool: str, target: str, *,
        input_id: str | None = None, provenance: dict[str, Any] | None = None,
    ) -> ArtifactRecord | None:
        known = self.store.get_artifact(artifact_id)
        if known is not None:
            if self.task.schema_version == 6:
                return known
            if known.input_id == input_id and known.provenance == (provenance or {}):
                return known
            enriched = known.model_copy(update={"input_id": input_id or known.input_id, "provenance": provenance or known.provenance})
            self.store.add_artifact(enriched)
            return enriched
        root = task_artifact_root(self.run_root / self.task.id, self.task)
        matches = list(root.glob(f"{artifact_id}.*"))
        if len(matches) != 1:
            return None
        path = matches[0]
        data = path.read_bytes()
        if path.suffix.casefold() in {".html", ".body"} and tool == "http.request":
            kind = "http_body"
            artifact_tool = "http.request.body"
            artifact_target = self._http_body_target(root, artifact_id) or target
        else:
            kind = "http_response" if tool == "http.request" else "tool_output"
            artifact_tool = tool
            artifact_target = target
        artifact = ArtifactRecord(
            id=artifact_id,
            task_id=self.task.id,
            intent_id=None,
            kind=kind,
            path=path.name,
            sha256=hashlib.sha256(data).hexdigest(),
            tool=artifact_tool,
            target=artifact_target,
            input_id=input_id,
            provenance=provenance or {},
            created_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        )
        self.store.add_artifact(artifact)
        return artifact
    @staticmethod
    def _http_body_target(root: Path, artifact_id: str) -> str | None:
        for candidate in root.glob("artifact_*.json"):
            try:
                payload = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict) and payload.get("body_artifact_id") == artifact_id:
                return str(payload.get("final_url") or "") or None
        return None
    def _artifact_excerpt(self, artifact: ArtifactRecord, limit: int = 16_000) -> str:
        index = self.store.get_artifact_index(artifact.id)
        if index is not None:
            retrieval = retrieve_segments(index, limit=min(limit, 6000))
            return json.dumps(
                {
                    "artifact_id": artifact.id,
                    "document_type": index.document_type,
                    "extraction_status": index.extraction_status,
                    "summary": index.summary,
                    "segments": retrieval["matches"],
                },
                ensure_ascii=False,
            )
        path = task_artifact_root(self.run_root / self.task.id, self.task) / artifact.path
        try:
            return path.read_bytes()[: min(limit, 6000)].decode("utf-8", errors="replace")
        except OSError:
            return ""
    def _artifact_text(self, task_id: str, artifact: ArtifactRecord) -> str:
        if task_id != self.task.id or artifact.task_id != task_id:
            return ""
        root = task_artifact_root(self.run_root / task_id, self.task)
        try:
            path = (root / artifact.path).resolve()
            path.relative_to(root)
            return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
        except (OSError, ValueError):
            return ""


class ObserverExecutionCoordinator:
    def __init__(self, state: Any) -> None:
        self.state = state
        self.task = state.task
        self.store = state.store
        self.solver_id = state.solver_id
        self.observer = state.observer

    @property
    def observer_directive(self) -> str:
        return self.state.observer_directive

    @observer_directive.setter
    def observer_directive(self, value: str) -> None:
        self.state.observer_directive = value

    def review(self, *, action: ActionSpec, result: ActionResult) -> None:
        self._run_observer(action=action, result=result)

    def _run_observer(self, *, action: ActionSpec, result: ActionResult) -> None:
        current = {
            **action.model_dump(mode="json"),
            "status": result.status,
            "result": result.model_dump(mode="json"),
        }
        actions = self.store.list_actions(self.task.id)
        latest_metric = self.store.list_context_metrics(self.task.id)
        triggers = native_observer_triggers(
            actions=actions,
            current=current,
            context_chars=latest_metric[-1].working_chars if latest_metric else 0,
        )
        if not triggers:
            return
        session = self.store.get_session(self.task.id)
        challenge = self.store.get_challenge(self.task.id)
        legacy_runtime = (
            {
                "memory": [item.model_dump(mode="json") for item in self.store.list_memory(self.task.id)],
                "strategy_cards": [item.model_dump(mode="json") for item in self.store.list_strategy_cards(self.task.id)],
            }
            if self.task.schema_version < 6 else {"memory": [], "strategy_cards": []}
        )
        snapshot = {
            "task": self.task.model_dump(mode="json"),
            "session": session.model_dump(mode="json") if session else {},
            "actions": actions,
            "runtime": legacy_runtime,
            "challenge": challenge.model_dump(mode="json") if challenge else {},
        }
        context = build_observer_context(snapshot)
        context["triggers"] = triggers
        self.store.append_agent_event(
            self.task.id,
            "OBSERVER_TRIGGERED",
            {"triggers": triggers, "action_id": action.id},
            solver_id=self.solver_id,
        )
        if not self.observer.request(context):
            return
        try:
            patch = self.observer.drain(wait=True)
            if patch is None:
                return
            if self.task.schema_version < 6:
                self.observer.apply(task_id=self.task.id, suggestion=patch)
            self.observer_directive = patch.strategy_advice
            self.store.append_agent_event(
                self.task.id,
                "OBSERVER_DIRECTIVE",
                {"triggers": triggers, "strategy_advice": patch.strategy_advice, "suggestion": patch.model_dump(mode="json")},
                solver_id=self.solver_id,
            )
            self.store.append_agent_event(
                self.task.id,
                "OBSERVER_PATCH_APPLIED",
                {
                    "candidate_suggestions": len(patch.memory_suggestions),
                    "legacy_memory_writes": (
                        len(patch.memory_suggestions)
                        if self.task.schema_version < 6 else 0
                    ),
                },
                solver_id=self.solver_id,
            )
        except Exception as exc:
            self.store.append_agent_event(
                self.task.id,
                "OBSERVER_FAILED",
                {"reason": str(exc)[:280]},
                solver_id=self.solver_id,
            )



