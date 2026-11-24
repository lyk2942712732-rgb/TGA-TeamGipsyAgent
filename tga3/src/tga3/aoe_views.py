"""AOE read-side view materialization: question.json, collection-state.json, and API snapshots."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from copy import deepcopy
from pathlib import Path
from uuid import UUID, uuid4

from .aoe_attachments import AOEAttachmentManager
from .aoe_models import active_attempts, attempt_budgets, overview_order, scout_ready
from .config import TGA3Config
from .domain import TaskRun
from .errors import ContractError
from .runtime_contract import COLLECTION_STATE_FILENAME
from .storage import Storage
from .submissions import Submissions
from .target_instances import TargetInstances
from .task_metadata import AOEQuestionMeta


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class AOEViewStore:
    """Materializes derived disk snapshots and API views for AOE batches and questions."""

    def __init__(
        self,
        config: TGA3Config,
        storage: Storage,
        attachments: AOEAttachmentManager,
        targets: TargetInstances,
        submissions: Submissions,
        *,
        write_json: Callable[[Path, dict], None] | None = None,
        build_question_view: Callable[[TaskRun], Awaitable[dict]] | None = None,
    ) -> None:
        self.config = config
        self.storage = storage
        self.attachments = attachments
        self.targets = targets
        self.submissions = submissions
        self._write_json = write_json
        self._build_question_view = build_question_view
        self._manifest_digests: dict[UUID, str] = {}
        self._collection_digests: dict[UUID, str] = {}
        self._manifest_sources: dict[UUID, tuple] = {}

    def invalidate_question(self, question_id: UUID) -> None:
        self._manifest_sources.pop(question_id, None)

    def clear_parent(self, parent_id: UUID, question_ids: list[UUID]) -> None:
        self._collection_digests.pop(parent_id, None)
        for question_id in question_ids:
            self._manifest_sources.pop(question_id, None)
            self._manifest_digests.pop(question_id, None)

    def clear_all(self) -> None:
        self._manifest_sources.clear()
        self._manifest_digests.clear()
        self._collection_digests.clear()

    def _question_source(self, question: TaskRun) -> tuple:
        fields = (
            "description",
            "source_key",
            "difficulty",
            "category",
            "tags",
            "connection_info",
            "submission_instructions",
            "flag_format",
            "collection_notes",
            "pre_solved",
            "status",
            "registered_at",
            "solved_at",
            "scout_ready",
            "scout_phase",
            "attempts",
            "flag",
            "rescue",
            "reinforcement",
            "finish_reason",
            "swarm",
            "solver_agent_id",
        )
        return (
            question.title,
            question.state,
            question.created_at,
            question.blackboard_seq,
            *(question.metadata.get(k) for k in fields),
            self.targets.spec(question).model_dump(mode="json"),
            self.targets.state(question.id),
        )

    async def question_view(self, question: TaskRun) -> dict:
        meta = AOEQuestionMeta.from_task(question)
        attempts = question.metadata.get("attempts", [])
        files = await self.attachments.list_for(question.id)
        return {
            "question_id": str(question.id),
            "parent_task_id": str(question.parent_task_id),
            "title": question.title,
            "description": meta.description,
            "source_key": meta.source_key,
            "difficulty": meta.difficulty,
            "category": meta.category,
            "tags": list(meta.tags),
            "connection_info": meta.connection_info,
            "submission_instructions": meta.submission_instructions,
            "flag_format": meta.flag_format,
            "collection_notes": meta.collection_notes,
            "pre_solved": meta.pre_solved,
            "remote_attachments_count": len(meta.remote_attachments),
            "status": meta.status,
            "registered_at": meta.registered_at if meta.registered_at is not None else question.created_at.timestamp(),
            "solved_at": meta.solved_at,
            "scout_ready": scout_ready(question),
            "scout_phase": question.metadata.get("scout_phase", "completed" if scout_ready(question) else "queued"),
            "attempted_by": sorted({a["agent_id"] for a in attempts}),
            "current_agents": [attempt["agent_id"] for attempt in active_attempts(question)],
            "attempts": attempts,
            "flag": meta.flag,
            "rescue": question.metadata.get("rescue"),
            "reinforcement": question.metadata.get("reinforcement"),
            "finish_reason": meta.finish_reason,
            "swarm": question.metadata.get("swarm"),
            "solver_agent_id": meta.solver_agent_id,
            "report_state": meta.report_state(question.state),
            "blackboard_seq": question.blackboard_seq,
            "attachments": [
                {
                    **{key: value for key, value in item.model_dump(mode="json").items() if key != "storage_path"},
                    "path": Path(item.storage_path).relative_to(self.config.task_path(question.id)).as_posix(),
                }
                for item in files
            ],
        }

    async def snapshot(self, parent_id: UUID) -> dict:
        parent = await self.storage.get_task(parent_id)
        if parent.scene_id != "aoe" or parent.parent_task_id is not None:
            raise ContractError("AOE overview requires its parent task")
        questions = await self.storage.list_questions(parent_id)
        views = [await self.question_view(question) for question in sorted(questions, key=overview_order)]
        return {
            "task_id": str(parent_id),
            "phase": parent.metadata.get("aoe_phase", "collecting"),
            "attempt_seconds": attempt_budgets(parent.metadata)["first"],
            "attempt_budgets": attempt_budgets(parent.metadata),
            "scout_mode": parent.metadata.get("aoe_scout_mode", "legacy_recon"),
            "collection_complete": parent.metadata.get("collection_complete", False),
            "collection_status": parent.metadata.get(
                "collection_status",
                "complete" if parent.metadata.get("collection_complete") else "collecting",
            ),
            "collection_warnings": list(parent.metadata.get("collection_warnings", [])),
            "questions": views,
        }

    async def save_question(self, question: TaskRun) -> None:
        source = self._question_source(question)
        path = self.config.task_path(question.id) / "question.json"
        if self._manifest_sources.get(question.id) == source and path.is_file():
            return
        builder = self._build_question_view or self.question_view
        writer = self._write_json or atomic_json
        value = await builder(question)
        value["target"] = self.targets.spec(question).model_dump(mode="json")
        submission = self.submissions.spec(question)
        value["submission"] = submission.model_dump(mode="json") if submission else None
        value["target_runtime"] = self.targets.state(question.id)
        if (path.parent / "source.json").is_file():
            value["source_path"] = "source.json"
        digest = hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
        if self._manifest_digests.get(question.id) != digest:
            writer(path, value)
            self._manifest_digests[question.id] = digest
        elif not path.is_file():
            writer(path, value)
        self._manifest_sources[question.id] = deepcopy(source)

    async def write_collection_state(
        self,
        parent_id: UUID,
        *,
        summary: dict,
        parent: TaskRun | None = None,
        questions: list[TaskRun] | None = None,
    ) -> dict:
        """Minimal control-plane signal file for Scout container; carries no question content."""
        parent = parent if parent is not None else await self.storage.get_task(parent_id)
        questions = questions if questions is not None else await self.storage.list_questions(parent_id)
        complete = bool(parent.metadata.get("collection_complete", False))
        value = {
            "task_id": str(parent_id),
            "collection_complete": complete,
            "status": parent.metadata.get("collection_status", "complete" if complete else "collecting"),
            "import_seq": summary["import_seq"],
            "imported_count": len(questions),
            "imported_keys": sorted(q.metadata["source_key"] for q in questions),
            "rejected_count": summary["rejected_count"],
            "last_batch": summary["last_batch"],
            "recent_errors": summary["recent_errors"],
            "warnings": list(parent.metadata.get("collection_warnings", [])),
        }
        path = self.config.task_path(parent_id) / COLLECTION_STATE_FILENAME
        digest = hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
        writer = self._write_json or atomic_json
        if self._collection_digests.get(parent_id) != digest or not path.is_file():
            writer(path, value)
            self._collection_digests[parent_id] = digest
        return value


__all__ = ["AOEViewStore", "atomic_json"]
