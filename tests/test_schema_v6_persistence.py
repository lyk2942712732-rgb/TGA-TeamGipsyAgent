from __future__ import annotations

import hashlib
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tga.domain.evidence.artifacts import Artifact
from tga.domain.evidence.claims import EvidenceClaim
from tga.domain.evidence.locators import EvidenceLocator
from tga.domain.planning.global_plan import GlobalPlan
from tga.domain.planning.intents import Intent, IntentDependency
from tga.domain.knowledge.items import KnowledgeItem
from tga.domain.solver.budgets import SolverBudget
from tga.domain.solver.instances import SolverInstance, SolverTimestamps, ToolPolicySnapshot
from tga.domain.solver.runs import SolverRun
from tga.domain.task.hints import TaskHint
from tga.domain.task.interventions import UserIntervention
from tga.domain.task.models import ModelSnapshot, TGATask
from tga.application.projections import TaskProjectionQueries
from tga.infrastructure.persistence import (
    ArtifactImmutableError,
    IntentClaimConflict,
    OwnershipError,
    PersistenceBundle,
    PersistenceConflict,
    PlanVersionConflict,
)


NOW = "2026-07-30T00:00:00Z"


def _task(task_id: str = "task_1") -> TGATask:
    return TGATask(id=task_id, name="Task", mode="ctf", goal="Solve it", schema_version=6)


def _intent(intent_id: str = "intent_1", *, task_id: str = "task_1") -> Intent:
    return Intent(
        id=intent_id,
        task_id=task_id,
        title="Inspect",
        objective="Inspect the supplied target",
        status="pending",
        created_at=NOW,
        updated_at=NOW,
    )


def _plan(version: int = 1, *, task_id: str = "task_1") -> GlobalPlan:
    return GlobalPlan(
        id=f"plan_{task_id}",
        task_id=task_id,
        version=version,
        status="active",
        intents=[_intent(task_id=task_id)],
        created_at=NOW,
        updated_at=NOW,
    )


def _artifact(artifact_id: str = "artifact_1", *, task_id: str = "task_1") -> Artifact:
    return Artifact(
        id=artifact_id,
        task_id=task_id,
        kind="tool_output",
        path="artifacts/result.txt",
        sha256="a" * 64,
        created_at=NOW,
    )


def _solver() -> SolverInstance:
    return SolverInstance(
        id="solver_1",
        task_id="task_1",
        definition_id="ctf-supervisor",
        definition_version="1",
        definition_content_sha256="b" * 64,
        orchestration_role="supervisor",
        specialties=("planning",),
        model_snapshot=ModelSnapshot(
            model="test-model",
            capability_fingerprint="c" * 64,
            verification_id="verify_1",
            verified_at=NOW,
            max_output_tokens=1024,
            timeout_seconds=30,
            temperature=0,
        ),
        tool_policy_snapshot=ToolPolicySnapshot(
            profile="supervisor",
            allowed_tool_groups=("control",),
            content_sha256="d" * 64,
        ),
        budget=SolverBudget(
            max_turns=10,
            max_input_tokens=10_000,
            max_output_tokens=5_000,
            max_tool_calls=20,
            max_artifacts=20,
        ),
        completion_authority="task",
        transcript_ref="transcripts/solver_1",
        private_workspace_ref="solvers/solver_1",
        timestamps=SolverTimestamps(created_at=NOW, updated_at=NOW),
    )


@pytest.fixture
def persistence(tmp_path):
    bundle = PersistenceBundle.open(tmp_path / "evidence.db")
    bundle.tasks.create_task(_task())
    yield bundle
    bundle.close()


def test_new_database_has_schema_v6_tables_and_indexes(tmp_path) -> None:
    bundle = PersistenceBundle.open(tmp_path / "evidence.db")
    try:
        names = {
            row[0]
            for row in bundle.database.conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','index')"
            )
        }
        assert {
            "task_specs", "task_hints", "user_interventions", "global_plans",
            "intent_dependencies", "local_plans", "local_plan_steps",
            "solver_definitions_snapshot", "solver_instances", "solver_budgets",
            "solver_leases", "solver_runs", "worker_results", "knowledge_items",
            "solver_assignments", "worker_result_merges", "review_results",
            "report_results", "task_orchestrator_states",
            "knowledge_evidence_links", "evidence_claims", "finding_evidence_links",
            "task_common_skill_snapshots", "solver_skill_snapshots",
            "transcript_metadata", "transcript_messages", "approvals",
            "task_orchestrator_leases", "runtime_budget_usage",
            "network_budget_permits", "db_write_lock_metrics",
            "knowledge_bases", "corpus_sources", "corpus_documents",
            "document_revisions", "document_chunks", "index_snapshots",
            "index_bindings", "retrieval_runs", "retrieval_hits",
            "idx_v6_agent_events_task_seq",
            "uq_v6_active_solver_run_per_intent",
        }.issubset(names)
        assert not names.intersection({
            "solvers", "memory_entries", "actions", "strategy_cards",
            "events", "action_results",
        })
    finally:
        bundle.close()


def test_reopen_refreshes_schema_v6_content_hash_after_additive_schema_change(tmp_path) -> None:
    database_path = tmp_path / "evidence.db"
    PersistenceBundle.open(database_path).close()
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE schema_metadata SET content_sha256='stale' WHERE version=6"
        )

    bundle = PersistenceBundle.open(database_path)
    try:
        stored = bundle.database.conn.execute(
            "SELECT content_sha256 FROM schema_metadata WHERE version=6"
        ).fetchone()[0]
        schema_path = (
            Path(__file__).parents[1]
            / "tga"
            / "infrastructure"
            / "persistence"
            / "schema_v6.sql"
        )
        assert stored == hashlib.sha256(schema_path.read_bytes()).hexdigest()
    finally:
        bundle.close()


def test_global_plan_compare_and_swap_rejects_stale_writer(persistence) -> None:
    persistence.plans.save_global_plan(_plan())
    updated = _plan(version=2)
    persistence.plans.compare_and_swap_global_plan(updated, expected_version=1)

    with pytest.raises(PlanVersionConflict):
        persistence.plans.compare_and_swap_global_plan(
            _plan(version=2).model_copy(update={"updated_at": "later"}),
            expected_version=1,
        )


def test_global_plan_dependency_order_does_not_weaken_foreign_keys(persistence) -> None:
    dependent = _intent("intent_a").model_copy(
        update={"dependencies": [IntentDependency(intent_id="intent_b")]}
    )
    prerequisite = _intent("intent_b")
    plan = GlobalPlan(
        id="plan_dag", task_id="task_1", version=1, status="active",
        intents=[dependent, prerequisite], created_at=NOW, updated_at=NOW,
    )

    persistence.plans.save_global_plan(plan)

    loaded = persistence.plans.get_global_plan("task_1")
    assert loaded is not None
    assert loaded.intents[0].dependencies[0].intent_id == "intent_b"


def test_intent_claim_is_atomic_and_single_owner(persistence) -> None:
    persistence.plans.save_global_plan(_plan())
    claimed = persistence.plans.claim_intent(
        "intent_1", solver_id="solver_a", expected_version=1
    )
    assert claimed.assigned_solver_id == "solver_a"
    assert claimed.status == "running"

    with pytest.raises(IntentClaimConflict):
        persistence.plans.claim_intent(
            "intent_1", solver_id="solver_b", expected_version=1
        )


def test_solver_lease_expiry_allows_recovery(persistence) -> None:
    persistence.solvers.add_solver(_solver())
    start = datetime(2026, 7, 30, tzinfo=UTC)
    assert persistence.solvers.acquire_lease(
        "task_1", "solver_1", "owner_a", ttl_seconds=10, now=start
    )
    assert not persistence.solvers.acquire_lease(
        "task_1", "solver_1", "owner_b", ttl_seconds=10, now=start
    )
    assert persistence.solvers.acquire_lease(
        "task_1", "solver_1", "owner_b", ttl_seconds=10,
        now=start + timedelta(seconds=11),
    )
    assert not persistence.solvers.renew_lease(
        "task_1", "solver_1", "owner_a", ttl_seconds=10,
        now=start + timedelta(seconds=12),
    )


def test_solver_run_lifecycle_is_fenced_and_auditable(persistence) -> None:
    persistence.solvers.add_solver(_solver())
    run = SolverRun(
        id="run_1", task_id="task_1", solver_id="solver_1",
        orchestration_role="supervisor", created_at=NOW, updated_at=NOW,
    )
    persistence.orchestration.create_solver_run(run)
    start = datetime(2026, 7, 30, tzinfo=UTC)

    leased = persistence.orchestration.claim_solver_run(
        run.id, "runner_a", ttl_seconds=30, expected_version=1, now=start
    )
    assert leased is not None and leased.state == "leased"
    assert leased.fencing_token == 1
    running = persistence.orchestration.start_solver_run(
        run.id, "runner_a", leased.fencing_token, now=start + timedelta(seconds=1)
    )
    assert running.state == "running" and running.started_at is not None
    renewed = persistence.orchestration.renew_solver_run(
        run.id, "runner_a", leased.fencing_token,
        ttl_seconds=30, now=start + timedelta(seconds=2),
    )
    assert renewed is not None and renewed.version == running.version + 1
    completed = persistence.orchestration.finish_solver_run(
        run.id, "runner_a", leased.fencing_token, state="completed",
        result_id="result_1", now=start + timedelta(seconds=3),
    )
    assert completed.state == "completed" and completed.result_id == "result_1"
    assert persistence.orchestration.get_solver_run(run.id) == completed

    with pytest.raises(PersistenceConflict, match="late SolverRun"):
        persistence.orchestration.finish_solver_run(
            run.id, "runner_a", leased.fencing_token - 1, state="failed",
            now=start + timedelta(seconds=4),
        )


def test_expired_solver_run_rejects_late_completion(persistence) -> None:
    persistence.solvers.add_solver(_solver())
    run = SolverRun(
        id="run_expired", task_id="task_1", solver_id="solver_1",
        orchestration_role="supervisor", created_at=NOW, updated_at=NOW,
    )
    persistence.orchestration.create_solver_run(run)
    start = datetime(2026, 7, 30, tzinfo=UTC)
    leased = persistence.orchestration.claim_solver_run(
        run.id, "runner_a", ttl_seconds=10, expected_version=1, now=start
    )
    assert leased is not None
    persistence.orchestration.start_solver_run(
        run.id, "runner_a", leased.fencing_token, now=start + timedelta(seconds=1)
    )

    expired = persistence.orchestration.expire_solver_runs(
        now=start + timedelta(seconds=11)
    )
    assert [item.id for item in expired] == [run.id]
    assert expired[0].error_code == "SOLVER_RUN_LEASE_EXPIRED"
    with pytest.raises(PersistenceConflict, match="late SolverRun"):
        persistence.orchestration.finish_solver_run(
            run.id, "runner_a", leased.fencing_token, state="completed",
            now=start + timedelta(seconds=12),
        )


def test_artifacts_are_append_only_and_claim_ownership_is_enforced(persistence) -> None:
    artifact = _artifact()
    persistence.evidence.add_artifact(artifact)
    with pytest.raises(ArtifactImmutableError):
        persistence.evidence.add_artifact(artifact.model_copy(update={"path": "changed"}))

    claim = EvidenceClaim(
        id="claim_1",
        task_id="task_1",
        statement="Observed output",
        artifact_id=artifact.id,
        locator=EvidenceLocator(kind="text_range", char_start=0, char_end=8),
        created_at=NOW,
    )
    persistence.evidence.add_evidence_claim(claim)
    with pytest.raises(OwnershipError):
        persistence.evidence.add_evidence_claim(
            claim.model_copy(update={"id": "claim_bad", "task_id": "other_task"})
        )


def test_transaction_rolls_back_all_repository_writes(persistence) -> None:
    with pytest.raises(RuntimeError):
        with persistence.transaction():
            persistence.evidence.add_artifact(_artifact("artifact_rollback"))
            persistence.events.append_agent_event("task_1", "TEST", {"ok": True})
            raise RuntimeError("abort")

    assert persistence.evidence.get_artifact("artifact_rollback") is None
    assert persistence.events.list_agent_events("task_1") == []


def test_event_pagination_uses_stable_task_local_sequence(persistence) -> None:
    with persistence.transaction():
        for number in range(1_205):
            persistence.events.append_agent_event("task_1", "STEP", {"number": number})

    first = persistence.events.list_agent_events("task_1", after_seq=0, limit=1_000)
    second = persistence.events.list_agent_events("task_1", after_seq=first[-1].seq, limit=1_000)
    assert len(first) == 1_000 and len(second) == 205
    assert second[0].seq == 1_001 and second[-1].payload["number"] == 1_204


def test_foreign_keys_reject_cross_task_plan_ownership(tmp_path) -> None:
    bundle = PersistenceBundle.open(tmp_path / "evidence.db")
    try:
        bundle.tasks.create_task(_task("task_a"))
        bundle.tasks.create_task(_task("task_b"))
        bundle.plans.save_global_plan(_plan(task_id="task_a"))
        with pytest.raises(OwnershipError):
            bundle.plans.save_global_plan(
                _plan(task_id="task_b").model_copy(
                    update={"intents": [_intent(task_id="task_a")]}
                )
            )
    finally:
        bundle.close()


def test_remaining_repository_ports_and_projections_round_trip(persistence) -> None:
    hint = TaskHint(
        id="hint_1", task_id="task_1", content="Try the documented endpoint",
        source="user", created_at=NOW,
    )
    intervention = UserIntervention(
        id="intervention_1", task_id="task_1", kind="instruction",
        content="Do not change server state", created_at=NOW,
    )
    persistence.tasks.save_hint(hint)
    persistence.tasks.add_intervention(intervention)
    persistence.solvers.add_solver(_solver())
    persistence.transcripts.append_message(
        "task_1", "solver_1", {"role": "assistant", "content": "Starting analysis"}
    )
    knowledge = KnowledgeItem(
        id="knowledge_1", task_id="task_1", scope="task", status="candidate",
        kind="hypothesis", content="The endpoint may require authentication",
        created_by_solver_id="solver_1", created_at=NOW,
    )
    persistence.knowledge.add_knowledge(knowledge)
    persistence.plans.save_global_plan(_plan())

    assert persistence.tasks.list_hints("task_1") == [hint]
    assert persistence.tasks.list_interventions("task_1") == [intervention]
    assert persistence.solvers.get_solver("solver_1") == _solver()
    assert persistence.transcripts.list_messages("task_1", "solver_1")[0]["content"] == "Starting analysis"
    assert persistence.knowledge.list_knowledge("task_1") == [knowledge]
    queries = TaskProjectionQueries(persistence)
    assert queries.task_summary("task_1").status == "active"
    assert queries.solvers("task_1")[0].solver_id == "solver_1"
    assert queries.intents("task_1")[0].intent_id == "intent_1"
    assert queries.knowledge("task_1")[0].knowledge_id == "knowledge_1"
