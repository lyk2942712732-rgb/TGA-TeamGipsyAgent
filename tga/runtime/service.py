"""Application service shared by FastAPI and CLI adapters."""

from __future__ import annotations

import shutil
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from tga.contracts import TGATask
from tga.evidence.store import EvidenceStore
from tga.evidence.database import DatabaseSchemaVersionError
from tga.reporting.markdown_report import render_markdown_report
from tga.runtime.protocol import RUNTIME_SCHEMA_VERSION
from tga.infrastructure.persistence.legacy_v5 import LegacyV5TaskReader
from tga.infrastructure.persistence.bundle import PersistenceBundle
from tga.domain.task.spec import TaskDirective, TaskSpec
from tga.domain.skills.compatibility import legacy_skill_bundle_to_task_common
from tga.domain.skills.models import TaskCommonSkillSnapshot
from tga.evidence.database import utc_now


class UnsupportedTaskSchemaError(ValueError):
    code = "SCHEMA_VERSION_UNSUPPORTED"

    def __init__(self, schema_version: int):
        super().__init__(f"task schema {schema_version} is not executable; migrate it to schema 6")
        self.schema_version = schema_version


def require_current_task_schema(task: TGATask) -> None:
    if task.schema_version != 6:
        raise UnsupportedTaskSchemaError(task.schema_version)


class TaskRuntimeService:
    """Own task commands and queries without transport-specific behavior.

    The service never executes capabilities itself. Lifecycle mutations are
    delegated to Manager and all reads come from EvidenceStore.
    """

    def __init__(self, *, run_root: str | Path, manager: Any | None = None):
        self.run_root = Path(run_root)
        self._injected_manager = manager

    def task_root(self, task_id: str) -> Path:
        if not task_id or task_id.strip() != task_id:
            raise ValueError("invalid task id")
        root = self.run_root.resolve()
        candidate = (root / task_id).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ValueError("invalid task id") from exc
        return candidate

    def create_task(
        self,
        task: TGATask,
        *,
        initial_hint: str | None = None,
        task_common_skill_snapshot: TaskCommonSkillSnapshot | None = None,
    ) -> dict[str, Any]:
        require_current_task_schema(task)
        store = EvidenceStore(self.task_root(task.id) / "evidence.db")
        try:
            if store.get_task(task.id) is not None:
                raise ValueError("task id already exists")
            store.create_task(task)
            persistence = PersistenceBundle(store)
            created_at = utc_now()
            prompt = task.session_input.prompt.strip()
            persistence.tasks.save_task_spec(TaskSpec(
                task_id=task.id,
                objective=task.goal,
                instructions=[TaskDirective(
                    id=f"directive_initial_{task.id}",
                    task_id=task.id,
                    kind="instruction",
                    content=prompt,
                    source="user",
                    created_at=created_at,
                    provenance={"source": "session_input.prompt"},
                )] if prompt else [],
                resources=[],
                provenance={
                    "source": "task_creation",
                    "session_resources_projected": False,
                    "initial_prompt_is_hint": False,
                },
            ))
            if task_common_skill_snapshot is not None:
                if task_common_skill_snapshot.task_id != task.id:
                    raise ValueError("Task Common Skill snapshot belongs to another task")
                persistence.tasks.save_task_common_skill_snapshot(
                    task_common_skill_snapshot
                )
            elif task.skill_bundle_snapshot is not None:
                # Explicit compatibility for callers constructing an older
                # TGATask payload. New Task creation never writes this field.
                persistence.tasks.save_task_common_skill_snapshot(
                    legacy_skill_bundle_to_task_common(
                        task.skill_bundle_snapshot, task_id=task.id, created_at=created_at,
                    )
                )
            store.append_agent_event(
                task.id,
                "TASK_INPUT_ANALYZED",
                {
                    "prompt_present": bool(task.session_input.prompt.strip()),
                    "file_count": len(task.session_input.files),
                    "task_entry_url": task.task_entry_url,
                },
            )
            store.append_agent_event(
                task.id,
                "NETWORK_SEEDS_EXTRACTED",
                {
                    "seed_origins": task.execution_policy.network.seed_origins,
                    "task_entry_url": task.task_entry_url,
                    "access": task.execution_policy.network.access,
                },
            )
            if task.model_snapshot is not None:
                store.append_agent_event(
                    task.id,
                    "MODEL_CONFIG_SNAPSHOTTED",
                    {
                        "provider": task.model_snapshot.provider,
                        "model": task.model_snapshot.model,
                        "verification_id": task.model_snapshot.verification_id,
                        "capability_fingerprint": task.model_snapshot.capability_fingerprint,
                    },
                )
            common_skills = persistence.tasks.get_task_common_skill_snapshot(task.id)
            if common_skills is not None:
                store.append_agent_event(
                    task.id,
                    "SKILLS_SNAPSHOTTED",
                    {
                        "scope": "task_common",
                        "selector": common_skills.selector,
                        "count": len(common_skills.skills),
                        "total_chars": common_skills.total_chars,
                        "legacy_import": common_skills.legacy_import,
                        "skills": [
                            {
                                "name": item.name,
                                "version": item.version,
                                "source": item.origin,
                                "content_sha256": item.content_sha256,
                                "selection_reasons": item.selection_reasons,
                            }
                            for item in common_skills.skills
                        ],
                    },
                )
        finally:
            store.close()
        result = self.command("start_session", task.id, initial_hint=initial_hint)
        return {"schema_version": RUNTIME_SCHEMA_VERSION, "task_id": task.id, **result}

    def run_task(self, task_id: str) -> dict[str, Any]:
        self._require_executable_database(task_id)
        return self._manager().run_session(task_id)

    def command(self, method_name: str, task_id: str, **payload: Any) -> dict[str, Any]:
        self._require_executable_database(task_id)
        method = getattr(self._manager(), method_name)
        result = method(task_id=task_id, **payload)
        return result if isinstance(result, dict) else {"accepted": True, "status": "accepted"}

    def snapshot(self, task_id: str) -> dict[str, Any]:
        db_path = self.task_root(task_id) / "evidence.db"
        if not db_path.is_file():
            raise KeyError(f"task not found: {task_id}")
        schema_version = self._database_schema(db_path)
        if schema_version == 5:
            reader = LegacyV5TaskReader(db_path)
            try:
                snapshot = reader.snapshot(task_id)
            finally:
                reader.close()
            snapshot.setdefault("solvers", [])
            snapshot.setdefault("flags", [])
            snapshot.setdefault("actions", [])
            snapshot.setdefault("artifact_indexes", [])
            snapshot.setdefault("context_metrics", [])
            snapshot["runtime"] = {
                "memory": snapshot.pop("memory", []),
                "strategy_cards": snapshot.pop("strategy_cards", []),
            }
            snapshot["schema_version"] = RUNTIME_SCHEMA_VERSION
            events = snapshot.get("agent_events") or []
            snapshot["latest_seq"] = events[-1]["seq"] if events else 0
            return snapshot
        try:
            store = EvidenceStore(db_path)
        except DatabaseSchemaVersionError as exc:
            raise UnsupportedTaskSchemaError(exc.schema_version) from exc
        try:
            snapshot = store.get_session_snapshot(task_id)
        finally:
            store.close()
        if not snapshot.get("task") or not snapshot.get("session"):
            raise KeyError(f"runtime session not found: {task_id}")
        require_current_task_schema(TGATask.model_validate(snapshot["task"]))
        snapshot["schema_version"] = RUNTIME_SCHEMA_VERSION
        store = EvidenceStore(db_path)
        try:
            snapshot["latest_seq"] = store.latest_agent_event_seq(task_id)
        finally:
            store.close()
        return snapshot

    def events(self, task_id: str, *, after_seq: int = 0, limit: int = 200) -> list[dict[str, Any]]:
        db_path = self.task_root(task_id) / "evidence.db"
        if not db_path.is_file():
            raise KeyError(f"task not found: {task_id}")
        if self._database_schema(db_path) == 5:
            reader = LegacyV5TaskReader(db_path)
            try:
                return [
                    item.model_dump(mode="json")
                    for item in reader.replay(task_id, after_seq=after_seq, limit=limit)
                ]
            finally:
                reader.close()
        store = EvidenceStore(db_path)
        try:
            return [
                item.model_dump(mode="json")
                for item in store.list_events(task_id, after_seq=after_seq, limit=limit)
            ]
        finally:
            store.close()

    def runtime_snapshot(self, task_id: str) -> dict[str, Any]:
        """Return the bounded Phase-9 task-level projection.

        Version-5 databases use the dedicated read-only reader.  Version-6
        projections are assembled from repositories without loading complete
        event history or Artifact bytes.
        """
        db_path = self.task_root(task_id) / "evidence.db"
        if not db_path.is_file():
            raise KeyError(f"task not found: {task_id}")
        if self._database_schema(db_path) == 5:
            reader = LegacyV5TaskReader(db_path)
            try:
                payload = reader.snapshot(task_id)
            finally:
                reader.close()
            payload["schema_version"] = 5
            payload.setdefault("solvers", [])
            payload.setdefault("events", payload.get("agent_events") or [])
            payload["latest_seq"] = max(
                (int(item.get("seq") or 0) for item in payload["events"]),
                default=0,
            )
            return payload

        self._require_executable_database(task_id)
        store = EvidenceStore(db_path)
        try:
            repositories = PersistenceBundle(store)
            task = repositories.tasks.get_task(task_id)
            session = store.get_session(task_id)
            if task is None or session is None:
                raise KeyError(f"runtime session not found: {task_id}")
            state = repositories.orchestration.get_state(task_id)
            solvers = repositories.solvers.list_solvers(task_id)
            solver_payloads = [
                _solver_projection(repositories, item) for item in solvers[:100]
            ]
            active_statuses = {
                "created", "queued", "ready", "running", "waiting",
                "awaiting_approval",
            }
            active_count = sum(str(item.status) in active_statuses for item in solvers)
            supervisor_id = (
                state.supervisor_solver_id if state is not None else next(
                    (
                        item.id for item in solvers
                        if item.orchestration_role == "supervisor"
                    ),
                    None,
                )
            )
            max_workers = state.max_active_workers if state is not None else max(
                1, min(2, int(task.execution_budget.get("max_active_workers", 1)))
            )
            plan = repositories.plans.get_global_plan(task_id)
            intents = list(plan.intents if plan else ())
            worker_records = repositories.solvers.list_worker_result_records(task_id)
            knowledge = repositories.knowledge.list_knowledge(task_id)
            _, artifacts = repositories.evidence.page_artifacts(
                task_id, offset=0, limit=100
            )
            _, claims = repositories.evidence.page_evidence_claims(
                task_id, offset=0, limit=100
            )
            _, findings = repositories.evidence.page_findings(
                task_id, offset=0, limit=100
            )
            actions = store.list_actions(task_id)[-100:]
            approval_payload = self._approval_page_open(
                store, task_id, offset=0, limit=100, status="pending"
            )["items"]
            retrieval_runs = repositories.retrieval.list_runs(
                task_id=task_id, limit=100
            )
            latest_seq = store.latest_agent_event_seq(task_id)
            event_after = max(0, latest_seq - 100)
            event_page = self._event_page_open(
                store, task_id, after_seq=event_after, limit=100
            )
            challenge = store.get_challenge(task_id)
            artifact_indexes = store.list_artifact_indexes(task_id)[-100:]
            events = event_page["events"]
            http_sessions: dict[str, dict[str, Any]] = {}
            observer_directives: list[dict[str, Any]] = []
            for event in events:
                if event["type"] == "HTTP_SESSION_STATUS":
                    http_sessions[str(event.get("solver_id") or "main")] = event["payload"]
                elif event["type"] == "OBSERVER_DIRECTIVE":
                    observer_directives.append({
                        "seq": event["seq"],
                        "created_at": event["created_at"],
                        **event["payload"],
                    })
            flags = [
                dict(row)
                for row in store.conn.execute(
                    "SELECT value,evidence_artifact_id,created_at FROM flags "
                    "WHERE task_id=? ORDER BY created_at DESC LIMIT 100",
                    (task_id,),
                ).fetchall()
            ]
            context_metrics = [
                {
                    key: value for key, value in item.model_dump(mode="json").items()
                    if value is not None
                }
                for item in store.list_context_metrics(task_id)[-100:]
            ]
            task_common_skills = repositories.tasks.get_task_common_skill_snapshot(task_id)
            task_payload = task.model_dump(mode="json")
            task_common_skill_projection = (
                {
                    "selector": task_common_skills.selector,
                    "count": len(task_common_skills.skills),
                    "total_chars": task_common_skills.total_chars,
                    "created_at": task_common_skills.created_at,
                    "legacy_import": task_common_skills.legacy_import,
                    "skills": [
                        {
                            "name": skill.name,
                            "version": skill.version,
                            "content_sha256": skill.content_sha256,
                            "origin": skill.origin,
                            "selection_reasons": list(skill.selection_reasons),
                            "required_capabilities": list(skill.required_capabilities),
                        }
                        for skill in task_common_skills.skills
                    ],
                }
                if task_common_skills is not None else None
            )
            legacy_memory = [
                item.model_dump(mode="json")
                for item in store.list_memory(task_id)[-100:]
            ]
            strategy_cards = [
                item.model_dump(mode="json")
                for item in store.list_strategy_cards(task_id)[-50:]
            ]
            return {
                "schema_version": 6,
                "task": task_payload,
                "task_common_skill_snapshot": task_common_skill_projection,
                "session": {
                    "status": session.status,
                    "supervisor_solver_id": supervisor_id,
                    "active_solver_count": active_count,
                    "max_active_workers": max_workers,
                    "task_budget_usage": _task_budget_usage(store, task_id),
                    "stop_reason": session.stop_reason or None,
                    "timestamps": {
                        "started_at": session.started_at,
                        "finished_at": session.finished_at,
                        "updated_at": state.updated_at if state is not None else None,
                    },
                    "turn_count": session.turn_count,
                    "max_turns": session.max_turns,
                },
                "team": {
                    "task_id": task_id,
                    "status": state.status if state is not None else session.status,
                    "supervisor_solver_id": supervisor_id,
                    "max_active_workers": max_workers,
                    "max_total_solvers": (
                        state.max_total_solvers if state is not None else max(
                            1, int(task.execution_budget.get("max_total_solvers", 1))
                        )
                    ),
                    "active_solver_count": active_count,
                    "solver_ids": [item.id for item in solvers],
                    "version": state.version if state is not None else 1,
                    "timestamps": {
                        "created_at": state.created_at if state is not None else None,
                        "updated_at": state.updated_at if state is not None else None,
                    },
                },
                "solvers": solver_payloads,
                "intents": [_intent_projection(item) for item in intents[:100]],
                "worker_results": [
                    _worker_result_projection(result_id, result)
                    for result_id, result in worker_records[-100:]
                ],
                "global_plan": _global_plan_projection(plan),
                "knowledge": [_knowledge_projection(item) for item in knowledge[-100:]],
                "artifacts": [_artifact_projection(item) for item in artifacts],
                "evidence_claims": [
                    _claim_projection(item) for item in claims
                ],
                "findings": [_finding_projection(item) for item in findings],
                "actions": [_action_projection(item) for item in actions],
                "approvals": approval_payload,
                "retrieval_runs": [
                    _retrieval_run_projection(
                        item, len(repositories.retrieval.list_hits(item.id))
                    )
                    for item in retrieval_runs
                ],
                "events": events,
                "events_page": {
                    "after_seq": event_page["after_seq"],
                    "next_after_seq": event_page["next_after_seq"],
                    "has_more": event_after > 0 or event_page["has_more"],
                },
                "latest_seq": latest_seq,
                "challenge": challenge.model_dump(mode="json") if challenge else {},
                "runtime": {
                    "memory": legacy_memory,
                    "strategy_cards": strategy_cards,
                },
                "flags": flags,
                "artifact_indexes": [
                    {
                        "artifact_id": item.artifact_id,
                        "document_type": item.document_type,
                        "extraction_status": item.extraction_status,
                        "summary": item.summary,
                        "segment_count": len(item.segments),
                        "source_refs": [segment.ref for segment in item.segments[:16]],
                    }
                    for item in artifact_indexes
                ],
                "http_sessions": list(http_sessions.values()),
                "observer": {"directives": observer_directives[-20:]},
                "context_metrics": context_metrics,
            }
        finally:
            store.close()

    def task_schema_version(self, task_id: str) -> int:
        db_path = self.task_root(task_id) / "evidence.db"
        if not db_path.is_file():
            raise KeyError(f"task not found: {task_id}")
        schema_version = self._database_schema(db_path)
        if schema_version not in {5, 6}:
            raise UnsupportedTaskSchemaError(schema_version)
        return schema_version

    def team_projection(self, task_id: str) -> dict[str, Any]:
        snapshot = self.runtime_snapshot(task_id)
        self._require_v6_projection(snapshot)
        return {
            "schema_version": 6,
            "task_id": task_id,
            "team": snapshot["team"],
            "solvers": snapshot["solvers"],
        }

    def solver_projection(self, task_id: str, solver_id: str) -> dict[str, Any]:
        snapshot = self.runtime_snapshot(task_id)
        self._require_v6_projection(snapshot)
        solver = next(
            (item for item in snapshot["solvers"] if item["solver_id"] == solver_id),
            None,
        )
        if solver is None:
            raise KeyError(f"solver does not belong to task: {solver_id}")
        return {
            "schema_version": 6, "task_id": task_id, "solver": solver,
        }

    def intent_page(
        self, task_id: str, *, offset: int = 0, limit: int = 50
    ) -> dict[str, Any]:
        self._require_executable_database(task_id)
        store = EvidenceStore(self.task_root(task_id) / "evidence.db")
        try:
            plan = PersistenceBundle(store).plans.get_global_plan(task_id)
            values = [_intent_projection(item) for item in (plan.intents if plan else [])]
            return _page(values, task_id=task_id, offset=offset, limit=limit)
        finally:
            store.close()

    def evidence_page(
        self, task_id: str, *, offset: int = 0, limit: int = 50
    ) -> dict[str, Any]:
        self._require_executable_database(task_id)
        store = EvidenceStore(self.task_root(task_id) / "evidence.db")
        try:
            evidence = PersistenceBundle(store).evidence
            bounded_offset, bounded_limit = _bounds(offset, limit)
            artifact_total, artifacts = evidence.page_artifacts(
                task_id, offset=bounded_offset, limit=bounded_limit
            )
            claim_total, claims = evidence.page_evidence_claims(
                task_id, offset=bounded_offset, limit=bounded_limit
            )
            finding_total, findings = evidence.page_findings(
                task_id, offset=bounded_offset, limit=bounded_limit
            )
            return {
                "schema_version": 6,
                "task_id": task_id,
                "artifacts": _page_records(
                    [_artifact_projection(item) for item in artifacts],
                    total=artifact_total, offset=bounded_offset, limit=bounded_limit,
                ),
                "evidence_claims": _page_records(
                    [_claim_projection(item) for item in claims],
                    total=claim_total, offset=bounded_offset, limit=bounded_limit,
                ),
                "findings": _page_records(
                    [_finding_projection(item) for item in findings],
                    total=finding_total, offset=bounded_offset, limit=bounded_limit,
                ),
            }
        finally:
            store.close()

    def approval_page(
        self, task_id: str, *, offset: int = 0, limit: int = 50,
        status: str | None = None,
    ) -> dict[str, Any]:
        self._require_executable_database(task_id)
        store = EvidenceStore(self.task_root(task_id) / "evidence.db")
        try:
            return self._approval_page_open(
                store, task_id, offset=offset, limit=limit, status=status
            )
        finally:
            store.close()

    def _approval_page_open(
        self, store: EvidenceStore, task_id: str, *, offset: int, limit: int,
        status: str | None,
    ) -> dict[str, Any]:
        bounded_offset, bounded_limit = _bounds(offset, limit)
        where = "task_id=?"
        parameters: list[Any] = [task_id]
        if status:
            where += " AND status=?"
            parameters.append(status)
        total = int(store.conn.execute(
            f"SELECT COUNT(*) FROM approvals WHERE {where}", parameters
        ).fetchone()[0])
        rows = store.conn.execute(
            f"SELECT * FROM approvals WHERE {where} ORDER BY created_at,id LIMIT ? OFFSET ?",
            (*parameters, bounded_limit, bounded_offset),
        ).fetchall()
        values = []
        for row in rows:
            payload = json.loads(row["payload_json"])
            action = store.get_action(task_id, str(row["action_id"])) or {}
            values.append(_approval_projection(payload, action, status=str(row["status"])))
        return {
            "schema_version": 6,
            "task_id": task_id,
            "offset": bounded_offset,
            "limit": bounded_limit,
            "total": total,
            "next_offset": (
                bounded_offset + len(values)
                if bounded_offset + len(values) < total else None
            ),
            "items": values,
        }

    def event_page(
        self, task_id: str, *, after_seq: int = 0, limit: int = 200
    ) -> dict[str, Any]:
        db_path = self.task_root(task_id) / "evidence.db"
        if not db_path.is_file():
            raise KeyError(f"task not found: {task_id}")
        if self._database_schema(db_path) == 5:
            events = self.events(task_id, after_seq=after_seq, limit=limit)
            next_seq = int(events[-1]["seq"]) if events else max(0, after_seq)
            reader = LegacyV5TaskReader(db_path)
            try:
                latest_seq = reader.latest_event_seq(task_id)
            finally:
                reader.close()
            return {
                "schema_version": 5,
                "task_id": task_id,
                "after_seq": max(0, after_seq),
                "next_after_seq": next_seq,
                "latest_seq": latest_seq,
                "has_more": next_seq < latest_seq,
                "events": [_public_event(item) for item in events],
            }
        self._require_executable_database(task_id)
        store = EvidenceStore(db_path)
        try:
            return self._event_page_open(
                store, task_id, after_seq=after_seq, limit=limit
            )
        finally:
            store.close()

    @staticmethod
    def _event_page_open(
        store: EvidenceStore, task_id: str, *, after_seq: int, limit: int
    ) -> dict[str, Any]:
        cursor = max(0, int(after_seq))
        bounded = max(1, min(int(limit), 200))
        values = [
            _public_event(item.model_dump(mode="json"))
            for item in store.list_events(task_id, after_seq=cursor, limit=bounded)
        ]
        next_seq = values[-1]["seq"] if values else cursor
        latest = store.latest_agent_event_seq(task_id)
        return {
            "schema_version": 6,
            "task_id": task_id,
            "after_seq": cursor,
            "next_after_seq": next_seq,
            "latest_seq": latest,
            "has_more": next_seq < latest,
            "events": values,
        }

    def artifact_index(self, task_id: str, artifact_id: str):
        self._require_executable_database(task_id)
        store = EvidenceStore(self.task_root(task_id) / "evidence.db")
        try:
            artifact = PersistenceBundle(store).evidence.get_artifact(artifact_id)
            if artifact is None or artifact.task_id != task_id:
                raise KeyError(f"artifact does not belong to task: {artifact_id}")
            return store.get_artifact_index(artifact_id)
        finally:
            store.close()

    def artifact_record(self, task_id: str, artifact_id: str) -> dict[str, Any]:
        self._require_executable_database(task_id)
        store = EvidenceStore(self.task_root(task_id) / "evidence.db")
        try:
            artifact = PersistenceBundle(store).evidence.get_artifact(artifact_id)
            if artifact is None or artifact.task_id != task_id:
                raise KeyError(f"artifact does not belong to task: {artifact_id}")
            return artifact.model_dump(mode="json")
        finally:
            store.close()

    async def wait_for_events(
        self, task_id: str, *, after_seq: int, timeout: float = 15.0
    ) -> bool:
        from tga.infrastructure.events import runtime_event_bus

        return bool(await runtime_event_bus.wait(
            task_id, after_seq=max(0, after_seq), timeout=timeout
        ))

    @staticmethod
    def _require_v6_projection(payload: dict[str, Any]) -> None:
        if int(payload.get("schema_version") or 0) != 6:
            raise UnsupportedTaskSchemaError(int(payload.get("schema_version") or 0))

    def list_tasks(self) -> list[dict[str, Any]]:
        if not self.run_root.exists():
            return []
        values: list[dict[str, Any]] = []
        for child in sorted(self.run_root.iterdir(), key=lambda path: path.stat().st_mtime, reverse=True):
            if not child.is_dir() or child.name.startswith(".") or not (child / "evidence.db").is_file():
                continue
            try:
                snapshot = self.runtime_snapshot(child.name)
            except (KeyError, OSError, ValueError):
                continue
            task = snapshot["task"]
            session = snapshot["session"]
            session_input = task.get("session_input") or {}
            input_files = session_input.get("files") or []
            prompt = str(session_input.get("prompt") or "").strip()
            events = snapshot.get("events") or snapshot.get("agent_events") or []
            solvers = snapshot.get("solvers") or []
            latest = events[-1] if events else None
            values.append({
                "schema_version": RUNTIME_SCHEMA_VERSION,
                "task_id": child.name,
                "name": task.get("name") or child.name,
                "mode": task.get("mode") or "ctf",
                "task_entry_url": task.get("task_entry_url"),
                "target_summary": ", ".join(
                    str(item.get("original_name") or item.get("originalName") or item.get("label") or item.get("id"))
                    for item in input_files[:3]
                ),
                "target_count": len(input_files),
                "hint_count": int(bool(prompt)),
                "created_at": events[0].get("created_at", "") if events else "",
                "updated_at": latest.get("created_at", "") if latest else "",
                "status": session.get("status") or "created",
                "turn_count": int(session.get("turn_count") or 0),
                "max_turns": int(session.get("max_turns") or 0),
                "active_solvers": sum(1 for item in solvers if item.get("status") in {"starting", "running", "waiting"}),
                "latest_event": {"seq": latest.get("seq"), "type": latest.get("type")} if latest else None,
                "flags": len(snapshot.get("flags") or []),
                "findings": len(snapshot.get("findings") or []),
                "artifacts": len(snapshot.get("artifacts") or []),
            })
        return values

    def delete_task(self, task_id: str) -> None:
        root = self.task_root(task_id)
        if (root / "evidence.db").is_file():
            snapshot = self.snapshot(task_id)
            if snapshot["session"]["status"] == "running":
                raise ValueError("running session cannot be deleted")
        if root.exists():
            shutil.rmtree(root)

    def write_report(self, task_id: str, *, output: str | Path | None = None) -> Path:
        self._require_executable_database(task_id)
        snapshot = self.snapshot(task_id)
        path = Path(output) if output else self.task_root(task_id) / "reports" / "report.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_markdown_report(snapshot), encoding="utf-8")
        store = EvidenceStore(self.task_root(task_id) / "evidence.db")
        try:
            store.append_agent_event(
                task_id=task_id,
                type="REPORT_EXPORTED",
                payload={"path": path.name, "format": "markdown"},
            )
        finally:
            store.close()
        return path

    def render_report(self, task_id: str) -> str:
        """Pure report query used by GET endpoints."""
        return render_markdown_report(self.snapshot(task_id))

    def _manager(self):
        if self._injected_manager is not None:
            return self._injected_manager
        from tga.runtime.manager import get_manager

        return get_manager()

    def _require_executable_database(self, task_id: str) -> None:
        db_path = self.task_root(task_id) / "evidence.db"
        if not db_path.is_file():
            raise KeyError(f"task not found: {task_id}")
        schema_version = self._database_schema(db_path)
        if schema_version != 6:
            raise UnsupportedTaskSchemaError(schema_version)

    @staticmethod
    def _database_schema(db_path: Path) -> int:
        connection = sqlite3.connect(f"file:{db_path.resolve().as_posix()}?mode=ro", uri=True)
        try:
            row = connection.execute("SELECT payload_json FROM tasks LIMIT 1").fetchone()
        finally:
            connection.close()
        if row is None:
            raise KeyError("task database has no task")
        try:
            return int(json.loads(row[0]).get("schema_version") or 0)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise UnsupportedTaskSchemaError(0) from exc


def _bounds(offset: int, limit: int) -> tuple[int, int]:
    return max(0, int(offset)), max(1, min(int(limit), 200))


def _page_body(values: list[dict[str, Any]], *, offset: int, limit: int) -> dict[str, Any]:
    bounded_offset, bounded_limit = _bounds(offset, limit)
    selected = values[bounded_offset:bounded_offset + bounded_limit]
    return {
        "offset": bounded_offset,
        "limit": bounded_limit,
        "total": len(values),
        "next_offset": (
            bounded_offset + len(selected)
            if bounded_offset + len(selected) < len(values) else None
        ),
        "items": selected,
    }


def _page_records(
    values: list[dict[str, Any]], *, total: int, offset: int, limit: int
) -> dict[str, Any]:
    return {
        "offset": offset,
        "limit": limit,
        "total": total,
        "next_offset": offset + len(values) if offset + len(values) < total else None,
        "items": values,
    }


def _page(
    values: list[dict[str, Any]], *, task_id: str, offset: int, limit: int
) -> dict[str, Any]:
    return {
        "schema_version": 6,
        "task_id": task_id,
        **_page_body(values, offset=offset, limit=limit),
    }


def _task_budget_usage(store: EvidenceStore, task_id: str) -> dict[str, int]:
    totals = {
        "turns": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "tool_calls": 0,
        "artifacts": 0,
    }
    rows = store.conn.execute(
        "SELECT usage_json FROM solver_budgets WHERE task_id=?", (task_id,)
    ).fetchall()
    for row in rows:
        usage = json.loads(row["usage_json"] or "{}")
        for key in totals:
            totals[key] += max(0, int(usage.get(key) or 0))
    return totals


def _solver_projection(repositories: PersistenceBundle, solver) -> dict[str, Any]:
    usage_row = repositories.database.conn.execute(
        "SELECT usage_json FROM solver_budgets WHERE solver_id=?", (solver.id,)
    ).fetchone()
    usage = json.loads(usage_row["usage_json"] or "{}") if usage_row else {}
    results = [
        item for item in repositories.solvers.list_worker_results(solver.task_id)
        if item.solver_id == solver.id
    ]
    current_summary = results[-1].summary[:1_000] if results else ""
    skill = solver.skill_bundle_snapshot
    return {
        "task_id": solver.task_id,
        "solver_id": solver.id,
        "definition_id": solver.definition_id,
        "orchestration_role": str(solver.orchestration_role),
        "specialties": list(solver.specialties),
        "parent_solver_id": solver.parent_solver_id,
        "assigned_intent_id": solver.assigned_intent_id,
        "status": str(solver.status),
        "current_summary": current_summary,
        "model_snapshot": {
            "provider": solver.model_snapshot.provider,
            "model": solver.model_snapshot.model,
            "capability_fingerprint": solver.model_snapshot.capability_fingerprint,
            "verification_id": solver.model_snapshot.verification_id,
            "verified_at": solver.model_snapshot.verified_at,
        },
        "skill_snapshot": ({
            "selector": skill.selector,
            "count": len(skill.skills),
            "names": [item.name for item in skill.skills],
            "total_chars": skill.total_chars,
            "created_at": skill.created_at,
        } if skill is not None else {}),
        "tool_policy": {
            "profile": solver.tool_policy_snapshot.profile,
            "allowed_tool_groups": list(
                solver.tool_policy_snapshot.allowed_tool_groups
            ),
            "allowed_capabilities": list(
                solver.tool_policy_snapshot.allowed_capabilities
            ),
            "content_sha256": solver.tool_policy_snapshot.content_sha256,
        },
        "budget_usage": {
            key: max(0, int(usage.get(key) or 0))
            for key in (
                "turns", "input_tokens", "output_tokens", "tool_calls", "artifacts"
            )
        },
        "timestamps": solver.timestamps.model_dump(mode="json"),
    }


def _intent_projection(item) -> dict[str, Any]:
    return {
        "task_id": item.task_id,
        "intent_id": item.id,
        "kind": item.kind,
        "title": item.title,
        "objective": item.objective,
        "status": item.status,
        "assigned_solver_id": item.assigned_solver_id,
        "dependencies": [dependency.intent_id for dependency in item.dependencies],
        "priority": item.priority,
        "budget": item.budget,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


def _worker_result_projection(result_id: str, item) -> dict[str, Any]:
    return {
        "result_id": result_id,
        "solver_id": item.solver_id,
        "intent_id": item.intent_id,
        "status": str(item.status),
        "summary": item.summary[:2_000],
        "artifact_ids": list(item.artifact_ids),
        "evidence_claim_ids": list(item.candidate_evidence_claim_ids),
        "knowledge_ids": list(item.candidate_knowledge_ids),
        "finding_ids": list(item.finding_ids),
        "limitations": list(item.limitations),
        "budget_usage": item.budget_usage.model_dump(mode="json"),
    }


def _global_plan_projection(plan) -> dict[str, Any] | None:
    if plan is None:
        return None
    return {
        "id": plan.id,
        "task_id": plan.task_id,
        "version": plan.version,
        "status": plan.status,
        "intent_ids": [item.id for item in plan.intents],
        "created_by_solver_id": plan.created_by_solver_id,
        "created_at": plan.created_at,
        "updated_at": plan.updated_at,
    }


def _knowledge_projection(item) -> dict[str, Any]:
    content = item.content
    return {
        "knowledge_id": item.id,
        "scope": item.scope,
        "target_id": item.target_id,
        "status": item.status,
        "kind": item.kind,
        "content_preview": content[:500],
        "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "created_by_solver_id": item.created_by_solver_id,
        "created_at": item.created_at,
    }


def _artifact_projection(item) -> dict[str, Any]:
    return {
        "artifact_id": item.id,
        "intent_id": item.intent_id,
        "kind": item.kind,
        "media_type": item.media_type,
        "tool": item.tool,
        "target": item.target,
        "sha256": item.sha256,
        "created_at": item.created_at,
    }


def _claim_projection(item) -> dict[str, Any]:
    return {
        "claim_id": item.id,
        "statement_preview": item.statement[:1_000],
        "artifact_id": item.artifact_id,
        "locator": item.locator.model_dump(mode="json"),
        "status": item.status,
        "created_by_solver_id": item.created_by_solver_id,
        "reviewed_by_solver_id": item.reviewed_by_solver_id,
        "created_at": item.created_at,
        "reviewed_at": item.reviewed_at,
    }


def _finding_projection(item) -> dict[str, Any]:
    return {
        "finding_id": item.id,
        "title": item.title,
        "description_preview": item.description[:1_000],
        "target": item.target,
        "severity": item.severity,
        "status": item.status,
        "evidence_claim_ids": item.evidence_claim_ids,
        "created_by_solver_id": item.created_by_solver_id,
        "created_at": item.created_at,
        "reviewed_at": item.reviewed_at,
    }


def _action_projection(item: dict[str, Any]) -> dict[str, Any]:
    result = item.get("result") if isinstance(item.get("result"), dict) else {}
    return {
        "id": str(item["id"]),
        "action_id": str(item["id"]),
        "solver_id": str(item.get("solver_id") or ""),
        "intent_id": item.get("intent_id"),
        "capability": str(item.get("capability") or ""),
        "target": str(item.get("target") or ""),
        "risk": str(item.get("risk") or "passive"),
        "effect": item.get("effect") or {},
        "arguments": _redact_event_value(item.get("arguments") or {}),
        "status": str(item.get("status") or ""),
        "summary": str(result.get("summary") or item.get("summary") or "")[:1_000],
        "artifact_ids": list(result.get("artifact_ids") or ()),
        "created_at": str(item.get("created_at") or ""),
        "updated_at": str(item.get("updated_at") or ""),
    }


def _approval_projection(
    payload: dict[str, Any], action: dict[str, Any], *, status: str
) -> dict[str, Any]:
    return {
        "approval_id": str(payload["id"]),
        "solver_id": str(payload.get("solver_id") or ""),
        "intent_id": payload.get("intent_id"),
        "action_id": str(payload["action_id"]),
        "action": {
            "id": str(action.get("id") or payload["action_id"]),
            "capability": str(action.get("capability") or ""),
            "target": str(action.get("target") or ""),
            "status": str(action.get("status") or status),
        },
        "risk": str(payload.get("risk") or action.get("risk") or "active"),
        "effect": payload.get("effect") or action.get("effect") or {},
        "reason": str(payload.get("reason") or ""),
        "alternatives": list(payload.get("alternatives") or ()),
        "deadline": str(payload.get("expires_at") or ""),
        "status": status,
        "created_at": str(payload.get("created_at") or ""),
        "updated_at": str(payload.get("updated_at") or ""),
    }


def _retrieval_run_projection(item, hit_count: int) -> dict[str, Any]:
    return {
        "retrieval_run_id": item.id,
        "owner_scope": item.owner.scope,
        "workspace_id": item.owner.workspace_id,
        "task_id": item.task_id,
        "solver_id": item.solver_id,
        "intent_id": item.intent_id,
        "index_snapshot_id": item.index_snapshot_id,
        "method": item.method,
        "query_preview": item.query[:500],
        "hit_count": hit_count,
        "created_at": item.created_at,
    }


def _public_event(item: dict[str, Any]) -> dict[str, Any]:
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    public_payload = _redact_event_value(payload)
    public_payload.setdefault("schema_version", 1)
    return {
        "schema_version": int(item.get("schema_version") or 6),
        "id": str(item.get("id") or ""),
        "task_id": str(item.get("task_id") or ""),
        "seq": max(1, int(item.get("seq") or 1)),
        "type": str(item.get("type") or "UNKNOWN"),
        "solver_id": item.get("solver_id"),
        "intent_id": item.get("intent_id") or payload.get("intent_id"),
        "payload": public_payload,
        "created_at": str(item.get("created_at") or ""),
    }


def _redact_event_value(value: Any, *, key: str = "") -> Any:
    normalized = "".join(character for character in key.casefold() if character.isalnum())
    if any(part in normalized for part in (
        "authorization", "cookie", "token", "secret", "password", "passwd", "apikey",
    )):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {
            str(name): _redact_event_value(item, key=str(name))
            for name, item in list(value.items())[:128]
            if item is not None
        }
    if isinstance(value, list):
        return [_redact_event_value(item) for item in value[:1_024]]
    return value
