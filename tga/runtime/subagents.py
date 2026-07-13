"""Structured child-Solver validation and board merge."""

from __future__ import annotations

from tga.contracts import SubagentOutput, SubagentRequest
from tga.evidence.store import EvidenceStore
from tga.runtime.board import BoardStore, HypothesisDraft
from tga.runtime.events import EventStore


def validate_output_ownership(*, store: EvidenceStore, request: SubagentRequest, output: SubagentOutput) -> None:
    if output.request_id != request.id:
        raise ValueError("subagent output request ownership mismatch")
    records = [item for item in store.list_subagents(request.task_id) if item["request"]["id"] == request.id]
    if len(records) != 1 or records[0]["solver_id"] != output.solver_id:
        raise ValueError("subagent output solver ownership mismatch")
    owned = set(request.input_artifact_ids)
    for action in store.list_actions(request.task_id):
        if action.get("solver_id") == output.solver_id:
            owned.update((action.get("result") or {}).get("artifact_ids") or [])
    referenced = set(output.artifact_ids)
    for item in output.facts:
        referenced.update(item.artifact_ids)
    for item in output.failure_boundaries:
        referenced.update(item.artifact_ids)
    for item in output.result_updates:
        referenced.update(item.evidence_artifact_ids)
    unknown = referenced - owned
    if unknown:
        raise ValueError(f"subagent output references unowned artifacts: {sorted(unknown)}")
    for artifact_id in referenced:
        artifact = store.get_artifact(artifact_id)
        if artifact is None or artifact.task_id != request.task_id:
            raise ValueError(f"subagent output references unknown artifact: {artifact_id}")


def merge_output(*, store: EvidenceStore, request: SubagentRequest, output: SubagentOutput) -> None:
    """Apply a validated hand-off; only Manager should call this function."""
    validate_output_ownership(store=store, request=request, output=output)
    board = BoardStore(store)
    events = EventStore(store)
    for raw in output.hypotheses:
        hypothesis = board.create_hypothesis(
            task_id=request.task_id,
            owner_solver_id=output.solver_id,
            draft=HypothesisDraft(**raw.model_dump()),
        )
        events.append(
            request.task_id,
            "HYPOTHESIS_CREATED",
            {"hypothesis_id": hypothesis.id, "statement": hypothesis.statement, "attack_class": hypothesis.attack_class},
            solver_id=output.solver_id,
        )
    for raw in output.result_updates:
        current = store.get_hypothesis(raw.hypothesis_id)
        if current is None or current.task_id != request.task_id or raw.hypothesis_id not in request.hypothesis_ids:
            raise ValueError(f"subagent cannot update hypothesis: {raw.hypothesis_id}")
        updated = board.transition_hypothesis(
            raw.hypothesis_id,
            status=raw.status,
            last_result=raw.last_result,
            evidence_artifact_ids=raw.evidence_artifact_ids,
            proposed_by_solver=raw.decisive,
        )
        events.append(
            request.task_id,
            "HYPOTHESIS_UPDATED",
            {"hypothesis_id": updated.id, "status": updated.status, "last_result": updated.last_result},
            solver_id=output.solver_id,
        )
    for raw in output.facts:
        board.add_memory(
            task_id=request.task_id,
            kind="fact",
            content=raw.content,
            source=f"solver:{output.solver_id}",
            artifact_ids=raw.artifact_ids,
        )
    for raw in output.failure_boundaries:
        board.add_memory(
            task_id=request.task_id,
            kind="failure_boundary",
            content=f"{raw.attack_class} @ {raw.entry_point}: {raw.summary}"[:800],
            source=f"solver:{output.solver_id}",
            artifact_ids=raw.artifact_ids,
        )
    store.finish_subagent_request(output)
    events.append(
        request.task_id,
        "SUBAGENT_FINISHED",
        {
            "request_id": request.id,
            "role": request.role,
            "status": output.status,
            "coverage_gaps": output.coverage_gaps,
            "next_recommendation": output.next_recommendation,
        },
        solver_id=output.solver_id,
    )
