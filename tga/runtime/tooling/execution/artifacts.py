"""Single Artifact registration boundary for all execution backends."""

from __future__ import annotations

import json
from pathlib import Path

from tga.domain.evidence import ArtifactRecord
from tga.evidence.artifacts import ArtifactStore
from tga.inputs import task_artifact_root
from tga.runtime.tooling.execution.models import AuthorizedExecutionRequest, ExecutionResult


class ArtifactIngestionService:
    def __init__(self, *, task, store, run_root: str | Path, execution_context=None) -> None:
        self.task = task
        self.store = store
        self.root = task_artifact_root(Path(run_root) / task.id, task)
        self.artifacts = ArtifactStore(self.root, execution_context=execution_context)

    def ingest(
        self,
        request: AuthorizedExecutionRequest,
        result: ExecutionResult,
    ) -> ExecutionResult:
        artifact_ids = list(dict.fromkeys(result.artifact_ids))
        for produced in result.produced_files:
            path = Path(produced.path).resolve()
            workspace_root = (Path(self.root).parent / "solvers" / request.solver_id).resolve()
            try:
                path.relative_to(workspace_root)
            except ValueError:
                continue
            if not path.is_file():
                continue
            artifact = self.artifacts.save_bytes(
                task_id=request.task_id,
                intent_id=request.intent_id,
                kind="file",
                data=path.read_bytes(),
                tool=request.capability,
                target=str(path),
                suffix=path.suffix or ".bin",
                identity_context=f"{request.action_id}:{path.relative_to(workspace_root).as_posix()}",
            )
            self._register(artifact, request)
            artifact_ids.append(artifact.id)

        if request.backend in {"sandbox", "remote_mcp"}:
            envelope = {
                "schema_version": 1,
                "action_id": request.action_id,
                "capability": request.capability,
                "backend": request.backend,
                "status": result.status,
                "exit_code": result.exit_code,
                "stdout": result.stdout_preview,
                "stderr": result.stderr_preview,
                "started_at": result.started_at,
                "finished_at": result.finished_at,
                "resource_usage": result.resource_usage,
                "structured_result": result.structured_result,
                "execution_metadata": result.execution_metadata,
                "produced_file_artifact_ids": artifact_ids,
                "error": result.error.model_dump(mode="json") if result.error else None,
            }
            artifact = self.artifacts.save_bytes(
                task_id=request.task_id,
                intent_id=request.intent_id,
                kind="tool_output",
                data=json.dumps(envelope, ensure_ascii=False, indent=2).encode("utf-8"),
                tool=request.capability,
                target=request.resolved_target,
                suffix=".json",
                identity_context=request.action_id,
            )
            self._register(artifact, request)
            artifact_ids.insert(0, artifact.id)

        artifact_ids = list(dict.fromkeys(artifact_ids))
        if artifact_ids:
            self.store.append_agent_event(
                request.task_id,
                "ARTIFACTS_INGESTED",
                {
                    "action_id": request.action_id,
                    "capability": request.capability,
                    "backend": request.backend,
                    "artifact_ids": artifact_ids,
                },
                solver_id=request.solver_id,
                intent_id=request.intent_id,
            )
        self.store.append_agent_event(
            request.task_id,
            "EXECUTION_BACKEND_COMPLETED",
            {
                "action_id": request.action_id,
                "capability": request.capability,
                "backend": request.backend,
                "status": result.status,
                "exit_code": result.exit_code,
                "artifact_ids": artifact_ids,
                "execution_metadata": result.execution_metadata,
            },
            solver_id=request.solver_id,
            intent_id=request.intent_id,
        )
        return result.model_copy(update={"artifact_ids": artifact_ids})

    def _register(self, artifact, request: AuthorizedExecutionRequest) -> None:
        record = ArtifactRecord.model_validate({
            **artifact.model_dump(mode="json"),
            "provenance": {
                "governed_action_id": request.action_id,
                "execution_backend": request.backend,
                "execution_profile_id": request.execution_profile_id,
                "sandbox_config_digest": request.sandbox_config_digest,
                "solver_run_id": request.solver_run_id,
                "fencing_token": request.fencing_token,
            },
        })
        existing = self.store.get_artifact(record.id)
        if existing is None:
            self.store.add_artifact(record)


__all__ = ["ArtifactIngestionService"]
