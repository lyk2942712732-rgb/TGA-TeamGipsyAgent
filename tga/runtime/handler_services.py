"""Artifact and Observer services for governed tool execution."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tga.contracts import ActionResult, ActionSpec, ArtifactRecord
from tga.application.services import ArtifactIndexingCoordinator
from tga.domain.evidence import Artifact
from tga.evidence.artifacts import ArtifactStore
from tga.evidence.indexing import build_artifact_index, retrieve_segments
from tga.inputs import task_artifact_root
from tga.infrastructure.persistence import PersistenceBundle
from tga.runtime.observer import build_observer_context, native_observer_triggers

class ArtifactService:
    def __init__(self, state: Any) -> None:
        self.task = state.task
        self.store = state.store
        self.run_root = state.run_root
        self.solver_id = state.solver_id
        self.execution_context = state.execution_context

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
        store = ArtifactStore(root, execution_context=self.execution_context)
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
        """Index one artifact into the single retrieval projection."""
        return self._index_artifact_for_retrieval(artifact)

    def _index_artifact_for_retrieval(self, artifact: ArtifactRecord):
        repositories = PersistenceBundle(self.store)
        return ArtifactIndexingCoordinator(
            repositories=repositories.retrieval,
            raw_loader=lambda item: self._artifact_path(item).read_bytes(),
            event_repository=repositories.events,
        ).index(
            Artifact.model_validate(artifact.model_dump(mode="json")),
            task_name=self.task.name,
            solver_id=self.solver_id,
        )

    def _artifact_path(self, artifact) -> Path:
        root = task_artifact_root(self.run_root / self.task.id, self.task)
        candidates = (
            root,
            (self.run_root / self.task.id / "workspace" / "shared" / "artifacts").resolve(),
        )
        for base in candidates:
            path = (base / artifact.path).resolve()
            try:
                path.relative_to(base.resolve())
            except ValueError as exc:
                raise PermissionError("Artifact path escapes its immutable store") from exc
            if path.is_file():
                return path
        raise FileNotFoundError(f"Artifact bytes are unavailable: {artifact.id}")
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
            return known
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
        """Derive a bounded readable excerpt from the immutable artifact bytes.

        The readable index is a derived view, so it is computed on demand
        instead of being persisted as a second index of record.
        """
        path = task_artifact_root(self.run_root / self.task.id, self.task) / artifact.path
        try:
            raw = path.read_bytes()
        except OSError:
            return ""
        if artifact.tool != "input_materialize":
            index = build_artifact_index(
                task_id=self.task.id,
                artifact_id=artifact.id,
                raw=raw,
                document_type="html" if path.suffix.casefold() in {".html", ".htm"} else None,
            )
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
        return raw[: min(limit, 6000)].decode("utf-8", errors="replace")
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
        governed = PersistenceBundle(self.store).tool_governance.list_actions(
            self.task.id, limit=1_000
        )
        actions = [
            {
                **dict(item["payload"]),
                "status": item["status"],
                "result": item.get("result"),
            }
            for item in governed
        ]
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
        snapshot = {
            "task": self.task.model_dump(mode="json"),
            "session": session.model_dump(mode="json") if session else {},
            "actions": actions,
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
                    "candidate_suggestions": 0,
                    "memory_writes": 0,
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


