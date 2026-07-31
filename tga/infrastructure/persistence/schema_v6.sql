CREATE TABLE IF NOT EXISTS schema_metadata (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL,
    content_sha256 TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task_specs (
    task_id TEXT PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE,
    version INTEGER NOT NULL CHECK(version >= 1),
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task_hints (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    scope TEXT NOT NULL,
    target_id TEXT,
    status TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1),
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_interventions (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    scope TEXT NOT NULL,
    target_id TEXT,
    kind TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1),
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS global_plans (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL UNIQUE REFERENCES tasks(id) ON DELETE CASCADE,
    version INTEGER NOT NULL CHECK(version >= 1),
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS intent_dependencies (
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    intent_id TEXT NOT NULL REFERENCES intents(id) ON DELETE CASCADE,
    depends_on_intent_id TEXT NOT NULL REFERENCES intents(id) ON DELETE RESTRICT,
    required_status TEXT NOT NULL,
    condition TEXT NOT NULL DEFAULT '',
    version INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1),
    PRIMARY KEY (intent_id, depends_on_intent_id),
    CHECK(intent_id <> depends_on_intent_id)
);

CREATE TABLE IF NOT EXISTS local_plans (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    solver_id TEXT NOT NULL,
    intent_id TEXT NOT NULL REFERENCES intents(id) ON DELETE CASCADE,
    version INTEGER NOT NULL CHECK(version >= 1),
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(solver_id, intent_id)
);

CREATE TABLE IF NOT EXISTS local_plan_steps (
    id TEXT PRIMARY KEY,
    local_plan_id TEXT NOT NULL REFERENCES local_plans(id) ON DELETE CASCADE,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    solver_id TEXT NOT NULL,
    intent_id TEXT NOT NULL REFERENCES intents(id) ON DELETE CASCADE,
    step_order INTEGER NOT NULL CHECK(step_order >= 0),
    status TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1),
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS solver_definitions_snapshot (
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    definition_id TEXT NOT NULL,
    definition_version TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(task_id, definition_id, definition_version)
);

CREATE TABLE IF NOT EXISTS solver_instances (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    definition_id TEXT NOT NULL,
    assigned_intent_id TEXT REFERENCES intents(id) ON DELETE RESTRICT,
    parent_solver_id TEXT REFERENCES solver_instances(id) ON DELETE RESTRICT,
    status TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1),
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS solver_budgets (
    solver_id TEXT PRIMARY KEY REFERENCES solver_instances(id) ON DELETE CASCADE,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    version INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1),
    budget_json TEXT NOT NULL,
    usage_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS solver_leases (
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    solver_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    fencing_token INTEGER NOT NULL DEFAULT 1 CHECK(fencing_token >= 1),
    expires_at TEXT NOT NULL,
    renewed_at TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1),
    updated_at TEXT NOT NULL,
    PRIMARY KEY(task_id, solver_id)
);

CREATE TABLE IF NOT EXISTS task_orchestrator_leases (
    task_id TEXT PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE,
    owner_id TEXT NOT NULL,
    fencing_token INTEGER NOT NULL DEFAULT 1 CHECK(fencing_token >= 1),
    expires_at TEXT NOT NULL,
    renewed_at TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1),
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS worker_results (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    solver_id TEXT NOT NULL,
    intent_id TEXT NOT NULL REFERENCES intents(id) ON DELETE RESTRICT,
    status TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1),
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(task_id, solver_id, intent_id, version)
);

CREATE TABLE IF NOT EXISTS solver_assignments (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    solver_id TEXT NOT NULL REFERENCES solver_instances(id) ON DELETE CASCADE,
    intent_id TEXT NOT NULL REFERENCES intents(id) ON DELETE RESTRICT,
    supervisor_solver_id TEXT NOT NULL REFERENCES solver_instances(id) ON DELETE RESTRICT,
    attempt INTEGER NOT NULL CHECK(attempt >= 1),
    status TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1),
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(task_id, intent_id, attempt)
);

CREATE TABLE IF NOT EXISTS solver_runs (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    solver_id TEXT NOT NULL REFERENCES solver_instances(id) ON DELETE CASCADE,
    assignment_id TEXT REFERENCES solver_assignments(id) ON DELETE RESTRICT,
    intent_id TEXT REFERENCES intents(id) ON DELETE RESTRICT,
    orchestration_role TEXT NOT NULL,
    state TEXT NOT NULL,
    attempt INTEGER NOT NULL CHECK(attempt >= 1),
    lease_owner TEXT,
    fencing_token INTEGER NOT NULL DEFAULT 0 CHECK(fencing_token >= 0),
    lease_expires_at TEXT,
    heartbeat_at TEXT,
    result_id TEXT,
    error_code TEXT,
    version INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1),
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(assignment_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_v6_active_solver_run_per_intent
ON solver_runs(intent_id)
WHERE intent_id IS NOT NULL
  AND state IN ('leased','running','waiting_approval');

CREATE INDEX IF NOT EXISTS idx_v6_solver_runs_task_state
ON solver_runs(task_id,state,created_at);

CREATE INDEX IF NOT EXISTS idx_v6_solver_runs_lease_expiry
ON solver_runs(lease_expires_at)
WHERE state IN ('leased','running');

CREATE TABLE IF NOT EXISTS worker_result_merges (
    worker_result_id TEXT PRIMARY KEY REFERENCES worker_results(id) ON DELETE CASCADE,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    intent_id TEXT NOT NULL REFERENCES intents(id) ON DELETE RESTRICT,
    merged_by_solver_id TEXT NOT NULL REFERENCES solver_instances(id) ON DELETE RESTRICT,
    merged_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS review_results (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    solver_id TEXT NOT NULL REFERENCES solver_instances(id) ON DELETE RESTRICT,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS report_results (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    solver_id TEXT NOT NULL REFERENCES solver_instances(id) ON DELETE RESTRICT,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task_orchestrator_states (
    task_id TEXT PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE,
    supervisor_solver_id TEXT REFERENCES solver_instances(id) ON DELETE RESTRICT,
    status TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1),
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS knowledge_items (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    scope TEXT NOT NULL,
    target_id TEXT,
    status TEXT NOT NULL,
    kind TEXT NOT NULL,
    subject TEXT,
    structured_value TEXT,
    version INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1),
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS knowledge_evidence_links (
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    knowledge_id TEXT NOT NULL REFERENCES knowledge_items(id) ON DELETE CASCADE,
    claim_id TEXT NOT NULL REFERENCES evidence_claims(id) ON DELETE RESTRICT,
    PRIMARY KEY(knowledge_id, claim_id)
);

CREATE TABLE IF NOT EXISTS knowledge_conflicts (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1),
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS knowledge_conflict_items (
    conflict_id TEXT NOT NULL REFERENCES knowledge_conflicts(id) ON DELETE CASCADE,
    knowledge_id TEXT NOT NULL REFERENCES knowledge_items(id) ON DELETE RESTRICT,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    PRIMARY KEY(conflict_id, knowledge_id)
);

CREATE TABLE IF NOT EXISTS knowledge_promotions (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    knowledge_id TEXT NOT NULL REFERENCES knowledge_items(id) ON DELETE RESTRICT,
    status TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1),
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    reviewed_at TEXT
);

CREATE TABLE IF NOT EXISTS evidence_claims (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    artifact_id TEXT NOT NULL REFERENCES artifacts(id) ON DELETE RESTRICT,
    status TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1),
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    reviewed_at TEXT
);

CREATE TABLE IF NOT EXISTS finding_evidence_links (
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    finding_id TEXT NOT NULL REFERENCES findings(id) ON DELETE CASCADE,
    claim_id TEXT NOT NULL REFERENCES evidence_claims(id) ON DELETE RESTRICT,
    PRIMARY KEY(finding_id, claim_id)
);

CREATE TABLE IF NOT EXISTS task_common_skill_snapshots (
    task_id TEXT PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE,
    version INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1),
    content_sha256 TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS solver_skill_snapshots (
    solver_id TEXT PRIMARY KEY REFERENCES solver_instances(id) ON DELETE CASCADE,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    version INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1),
    content_sha256 TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS transcript_metadata (
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    solver_id TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1),
    next_seq INTEGER NOT NULL DEFAULT 1 CHECK(next_seq >= 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(task_id, solver_id)
);

CREATE TABLE IF NOT EXISTS transcript_messages (
    task_id TEXT NOT NULL,
    solver_id TEXT NOT NULL,
    seq INTEGER NOT NULL CHECK(seq >= 1),
    role TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(task_id, solver_id, seq),
    FOREIGN KEY(task_id, solver_id) REFERENCES transcript_metadata(task_id, solver_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    solver_id TEXT,
    intent_id TEXT REFERENCES intents(id) ON DELETE RESTRICT,
    action_id TEXT,
    status TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1),
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS governed_actions (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    solver_id TEXT NOT NULL,
    intent_id TEXT,
    tool_call_id TEXT NOT NULL,
    tool_class TEXT NOT NULL,
    capability TEXT NOT NULL,
    execution_profile_id TEXT,
    sandbox_config_digest TEXT,
    attempt INTEGER NOT NULL DEFAULT 1 CHECK(attempt >= 1),
    status TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1),
    semantic_fingerprint TEXT,
    idempotency_key TEXT,
    resource_lock_key TEXT,
    payload_json TEXT NOT NULL,
    result_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(task_id, solver_id, tool_call_id)
);

CREATE TABLE IF NOT EXISTS governed_action_transitions (
    action_id TEXT NOT NULL REFERENCES governed_actions(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL CHECK(seq >= 1),
    from_status TEXT,
    to_status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(action_id, seq)
);

CREATE TABLE IF NOT EXISTS sandbox_instances (
    instance_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    solver_id TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    provider TEXT NOT NULL CHECK(provider IN ('docker_sandbox','sandboxd')),
    config_digest TEXT NOT NULL,
    fencing_token INTEGER NOT NULL CHECK(fencing_token >= 1),
    state TEXT NOT NULL CHECK(state IN (
        'acquiring','ready','released','destroying','destroyed','failed'
    )),
    destroy_after TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tool_idempotency_keys (
    idempotency_key TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    action_id TEXT NOT NULL REFERENCES governed_actions(id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    result_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tool_resource_locks (
    lock_key TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    action_id TEXT NOT NULL REFERENCES governed_actions(id) ON DELETE CASCADE,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tool_budget_reservations (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    solver_id TEXT NOT NULL,
    intent_id TEXT,
    action_id TEXT NOT NULL UNIQUE REFERENCES governed_actions(id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    tool_calls INTEGER NOT NULL DEFAULT 0 CHECK(tool_calls >= 0),
    artifacts INTEGER NOT NULL DEFAULT 0 CHECK(artifacts >= 0),
    version INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runtime_budget_usage (
    idempotency_key TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    solver_id TEXT NOT NULL REFERENCES solver_instances(id) ON DELETE CASCADE,
    intent_id TEXT REFERENCES intents(id) ON DELETE RESTRICT,
    turns INTEGER NOT NULL DEFAULT 0 CHECK(turns >= 0),
    input_tokens INTEGER NOT NULL DEFAULT 0 CHECK(input_tokens >= 0),
    output_tokens INTEGER NOT NULL DEFAULT 0 CHECK(output_tokens >= 0),
    artifact_bytes INTEGER NOT NULL DEFAULT 0 CHECK(artifact_bytes >= 0),
    network_requests INTEGER NOT NULL DEFAULT 0 CHECK(network_requests >= 0),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS network_budget_permits (
    idempotency_key TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    solver_id TEXT NOT NULL REFERENCES solver_instances(id) ON DELETE CASCADE,
    intent_id TEXT REFERENCES intents(id) ON DELETE RESTRICT,
    status TEXT NOT NULL CHECK(status IN ('active','released','expired')),
    acquired_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    released_at TEXT
);

-- Retrieval ownership is intentionally independent from Task persistence.
-- Global/workspace records therefore carry no mandatory tasks foreign key.
CREATE TABLE IF NOT EXISTS knowledge_bases (
    id TEXT PRIMARY KEY,
    owner_scope TEXT NOT NULL CHECK(owner_scope IN ('global','workspace','task','solver')),
    workspace_id TEXT,
    task_id TEXT,
    solver_id TEXT,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS corpus_sources (
    id TEXT PRIMARY KEY,
    knowledge_base_id TEXT NOT NULL REFERENCES knowledge_bases(id) ON DELETE RESTRICT,
    owner_scope TEXT NOT NULL CHECK(owner_scope IN ('global','workspace','task','solver')),
    workspace_id TEXT,
    task_id TEXT,
    solver_id TEXT,
    kind TEXT NOT NULL,
    channel TEXT NOT NULL CHECK(channel IN ('skill','reference','task_artifact')),
    trust_level TEXT NOT NULL CHECK(trust_level IN ('authoritative','trusted','unverified')),
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS corpus_documents (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES corpus_sources(id) ON DELETE RESTRICT,
    knowledge_base_id TEXT NOT NULL REFERENCES knowledge_bases(id) ON DELETE RESTRICT,
    owner_scope TEXT NOT NULL CHECK(owner_scope IN ('global','workspace','task','solver')),
    workspace_id TEXT,
    task_id TEXT,
    solver_id TEXT,
    current_revision_id TEXT,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS document_revisions (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES corpus_documents(id) ON DELETE RESTRICT,
    source_id TEXT NOT NULL REFERENCES corpus_sources(id) ON DELETE RESTRICT,
    owner_scope TEXT NOT NULL CHECK(owner_scope IN ('global','workspace','task','solver')),
    workspace_id TEXT,
    task_id TEXT,
    solver_id TEXT,
    revision INTEGER NOT NULL CHECK(revision >= 1),
    content_sha256 TEXT NOT NULL,
    extraction_status TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(document_id, revision)
);

CREATE TABLE IF NOT EXISTS document_chunks (
    id TEXT PRIMARY KEY,
    knowledge_base_id TEXT NOT NULL REFERENCES knowledge_bases(id) ON DELETE RESTRICT,
    source_id TEXT NOT NULL REFERENCES corpus_sources(id) ON DELETE RESTRICT,
    document_id TEXT NOT NULL REFERENCES corpus_documents(id) ON DELETE RESTRICT,
    revision_id TEXT NOT NULL REFERENCES document_revisions(id) ON DELETE RESTRICT,
    owner_scope TEXT NOT NULL CHECK(owner_scope IN ('global','workspace','task','solver')),
    workspace_id TEXT,
    task_id TEXT,
    solver_id TEXT,
    channel TEXT NOT NULL CHECK(channel IN ('skill','reference','task_artifact')),
    trust_level TEXT NOT NULL CHECK(trust_level IN ('authoritative','trusted','unverified')),
    content TEXT NOT NULL,
    token_count INTEGER NOT NULL CHECK(token_count >= 1),
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS index_snapshots (
    id TEXT PRIMARY KEY,
    owner_scope TEXT NOT NULL CHECK(owner_scope IN ('global','workspace','task','solver')),
    workspace_id TEXT,
    task_id TEXT,
    solver_id TEXT,
    index_version INTEGER NOT NULL CHECK(index_version >= 1),
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS index_bindings (
    id TEXT PRIMARY KEY,
    owner_scope TEXT NOT NULL CHECK(owner_scope IN ('global','workspace','task','solver')),
    workspace_id TEXT,
    task_id TEXT,
    solver_id TEXT,
    purpose TEXT NOT NULL,
    index_snapshot_id TEXT NOT NULL REFERENCES index_snapshots(id) ON DELETE RESTRICT,
    version INTEGER NOT NULL CHECK(version >= 1),
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(owner_scope, workspace_id, task_id, solver_id, purpose)
);

CREATE TABLE IF NOT EXISTS retrieval_runs (
    id TEXT PRIMARY KEY,
    owner_scope TEXT NOT NULL CHECK(owner_scope IN ('global','workspace','task','solver')),
    workspace_id TEXT,
    task_id TEXT,
    solver_id TEXT,
    intent_id TEXT,
    index_snapshot_id TEXT NOT NULL REFERENCES index_snapshots(id) ON DELETE RESTRICT,
    requested_method TEXT NOT NULL,
    method TEXT NOT NULL,
    query TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS retrieval_hits (
    id TEXT PRIMARY KEY,
    retrieval_run_id TEXT NOT NULL REFERENCES retrieval_runs(id) ON DELETE CASCADE,
    owner_scope TEXT NOT NULL CHECK(owner_scope IN ('global','workspace','task','solver')),
    workspace_id TEXT,
    task_id TEXT,
    solver_id TEXT,
    chunk_id TEXT NOT NULL REFERENCES document_chunks(id) ON DELETE RESTRICT,
    rank INTEGER NOT NULL CHECK(rank >= 1),
    selected_for_context INTEGER NOT NULL DEFAULT 0 CHECK(selected_for_context IN (0,1)),
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(retrieval_run_id, chunk_id)
);

CREATE TABLE IF NOT EXISTS db_write_lock_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    wait_ms REAL NOT NULL CHECK(wait_ms >= 0),
    retry_count INTEGER NOT NULL CHECK(retry_count >= 0),
    recorded_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_v6_hints_task_status ON task_hints(task_id, status, created_at);
CREATE INDEX IF NOT EXISTS idx_v6_interventions_task_created ON user_interventions(task_id, created_at);
CREATE INDEX IF NOT EXISTS idx_v6_intents_task_status ON intents(task_id, status, priority, created_at);
CREATE INDEX IF NOT EXISTS idx_v6_local_plans_task_solver ON local_plans(task_id, solver_id);
CREATE INDEX IF NOT EXISTS idx_v6_solver_instances_task_status ON solver_instances(task_id, status);
CREATE INDEX IF NOT EXISTS idx_v6_solver_leases_expiry ON solver_leases(expires_at);
CREATE INDEX IF NOT EXISTS idx_v6_task_orchestrator_leases_expiry ON task_orchestrator_leases(expires_at);
CREATE INDEX IF NOT EXISTS idx_v6_worker_results_intent ON worker_results(task_id, intent_id, version);
CREATE INDEX IF NOT EXISTS idx_v6_assignments_task_status ON solver_assignments(task_id, status, created_at);
CREATE INDEX IF NOT EXISTS idx_v6_assignments_solver ON solver_assignments(solver_id, status);
CREATE INDEX IF NOT EXISTS idx_v6_review_results_task ON review_results(task_id, created_at);
CREATE INDEX IF NOT EXISTS idx_v6_report_results_task ON report_results(task_id, created_at);
CREATE INDEX IF NOT EXISTS idx_v6_knowledge_task_scope ON knowledge_items(task_id, scope, target_id, status);
CREATE INDEX IF NOT EXISTS idx_v6_knowledge_subject ON knowledge_items(task_id, subject, status);
CREATE INDEX IF NOT EXISTS idx_v6_knowledge_conflicts_task_status ON knowledge_conflicts(task_id, status, created_at);
CREATE INDEX IF NOT EXISTS idx_v6_knowledge_promotions_task_status ON knowledge_promotions(task_id, status, created_at);
CREATE INDEX IF NOT EXISTS idx_v6_artifacts_task_created ON artifacts(task_id, created_at, id);
CREATE INDEX IF NOT EXISTS idx_v6_claims_task_status ON evidence_claims(task_id, status, created_at);
CREATE INDEX IF NOT EXISTS idx_v6_findings_task_status ON findings(task_id, status, created_at);
CREATE INDEX IF NOT EXISTS idx_v6_agent_events_task_seq ON agent_events(task_id, seq);
CREATE INDEX IF NOT EXISTS idx_v6_approvals_task_status ON approvals(task_id, status, created_at);
CREATE INDEX IF NOT EXISTS idx_v6_governed_actions_owner ON governed_actions(task_id, solver_id, intent_id, status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_v6_sandbox_active_task
ON sandbox_instances(task_id)
WHERE state IN ('acquiring','ready','released','destroying');
CREATE INDEX IF NOT EXISTS idx_v6_sandbox_cleanup
ON sandbox_instances(state,destroy_after);
CREATE INDEX IF NOT EXISTS idx_v6_governed_actions_semantic ON governed_actions(task_id, solver_id, semantic_fingerprint, status);
CREATE INDEX IF NOT EXISTS idx_v6_governed_actions_profile ON governed_actions(task_id, execution_profile_id, status);
CREATE INDEX IF NOT EXISTS idx_v6_governed_actions_idempotency ON governed_actions(idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_v6_budget_reservations_owner ON tool_budget_reservations(task_id, solver_id, intent_id, status);
CREATE INDEX IF NOT EXISTS idx_v6_runtime_budget_usage_task ON runtime_budget_usage(task_id, created_at);
CREATE INDEX IF NOT EXISTS idx_v6_runtime_budget_usage_solver ON runtime_budget_usage(solver_id, created_at);
CREATE INDEX IF NOT EXISTS idx_v6_network_permits_task_status ON network_budget_permits(task_id, status, expires_at);
CREATE INDEX IF NOT EXISTS idx_v6_knowledge_bases_owner ON knowledge_bases(owner_scope, workspace_id, task_id, solver_id, status);
CREATE INDEX IF NOT EXISTS idx_v6_corpus_sources_owner ON corpus_sources(owner_scope, workspace_id, task_id, solver_id, channel, status);
CREATE INDEX IF NOT EXISTS idx_v6_corpus_sources_kb ON corpus_sources(knowledge_base_id, status);
CREATE INDEX IF NOT EXISTS idx_v6_corpus_documents_source ON corpus_documents(source_id, status);
CREATE INDEX IF NOT EXISTS idx_v6_document_revisions_document ON document_revisions(document_id, revision);
CREATE INDEX IF NOT EXISTS idx_v6_document_chunks_snapshot_filter ON document_chunks(channel, trust_level, owner_scope, source_id);
CREATE INDEX IF NOT EXISTS idx_v6_document_chunks_document ON document_chunks(document_id, revision_id);
CREATE INDEX IF NOT EXISTS idx_v6_index_snapshots_owner ON index_snapshots(owner_scope, workspace_id, task_id, solver_id, created_at);
CREATE INDEX IF NOT EXISTS idx_v6_index_bindings_owner ON index_bindings(owner_scope, workspace_id, task_id, solver_id, purpose);
CREATE INDEX IF NOT EXISTS idx_v6_retrieval_runs_principal ON retrieval_runs(task_id, solver_id, intent_id, created_at);
CREATE INDEX IF NOT EXISTS idx_v6_retrieval_hits_run_rank ON retrieval_hits(retrieval_run_id, rank);
