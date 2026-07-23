CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS intents (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    intent_id TEXT,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    intent_id TEXT,
    type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS findings (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL,
    evidence_artifact_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS flags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    value TEXT NOT NULL,
    evidence_artifact_id TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- Durable session runtime tables.
CREATE TABLE IF NOT EXISTS sessions (
    task_id TEXT PRIMARY KEY,
    schema_version INTEGER NOT NULL DEFAULT 2,
    status TEXT NOT NULL,
    active_solver_id TEXT,
    turn_count INTEGER NOT NULL DEFAULT 0,
    max_turns INTEGER NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    stop_reason TEXT NOT NULL DEFAULT '',
    workspace_path TEXT NOT NULL DEFAULT '',
    mcp_catalog_version TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS solvers (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    role TEXT NOT NULL,
    status TEXT NOT NULL,
    model_name TEXT NOT NULL DEFAULT '',
    parent_solver_id TEXT,
    started_at TEXT,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS hypotheses (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    statement TEXT NOT NULL,
    attack_class TEXT NOT NULL,
    entry_point TEXT NOT NULL,
    rationale TEXT NOT NULL,
    next_test TEXT NOT NULL,
    status TEXT NOT NULL,
    confidence REAL NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    evidence_json TEXT NOT NULL DEFAULT '[]',
    last_result TEXT NOT NULL DEFAULT '',
    owner_solver_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_entries (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    artifact_ids_json TEXT NOT NULL DEFAULT '[]',
    source TEXT NOT NULL,
    supersedes_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS actions (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    solver_id TEXT NOT NULL,
    hypothesis_id TEXT,
    kind TEXT NOT NULL,
    capability TEXT NOT NULL,
    target TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    rationale TEXT NOT NULL,
    risk TEXT NOT NULL,
    strategy_card_id TEXT,
    strategy_step_id TEXT,
    expected_outcome TEXT NOT NULL DEFAULT '',
    retry_reason TEXT NOT NULL DEFAULT '',
    alternative_analysis TEXT NOT NULL DEFAULT '',
    expected_side_effects TEXT NOT NULL DEFAULT '',
    input_id TEXT,
    target_ref TEXT,
    actual_target TEXT,
    authorization_json TEXT NOT NULL DEFAULT '{}',
    provenance_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS strategy_cards (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS artifact_indexes (
    artifact_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS context_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    solver_id TEXT NOT NULL,
    turn INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS action_results (
    action_id TEXT PRIMARY KEY,
    summary TEXT NOT NULL,
    artifact_ids_json TEXT NOT NULL DEFAULT '[]',
    facts_json TEXT NOT NULL DEFAULT '[]',
    leads_json TEXT NOT NULL DEFAULT '[]',
    flags_json TEXT NOT NULL DEFAULT '[]',
    findings_json TEXT NOT NULL DEFAULT '[]',
    error_json TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_events (
    id TEXT PRIMARY KEY,
    schema_version INTEGER NOT NULL DEFAULT 2,
    task_id TEXT NOT NULL,
    solver_id TEXT,
    seq INTEGER NOT NULL,
    type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(task_id, seq)
);

CREATE TABLE IF NOT EXISTS agent_event_sequences (
    task_id TEXT PRIMARY KEY,
    next_seq INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS challenge_contracts (
    task_id TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS subagent_requests (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    solver_id TEXT NOT NULL UNIQUE,
    fingerprint TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL,
    output_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_agent_events_task_seq ON agent_events(task_id, seq);
CREATE INDEX IF NOT EXISTS idx_hypotheses_task_status ON hypotheses(task_id, status);
CREATE INDEX IF NOT EXISTS idx_memory_entries_task_created ON memory_entries(task_id, created_at);
CREATE INDEX IF NOT EXISTS idx_actions_task_created ON actions(task_id, created_at);
CREATE INDEX IF NOT EXISTS idx_subagent_requests_task_status ON subagent_requests(task_id, status);
CREATE INDEX IF NOT EXISTS idx_strategy_cards_task_updated ON strategy_cards(task_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_artifact_indexes_task_created ON artifact_indexes(task_id, created_at);
CREATE INDEX IF NOT EXISTS idx_context_metrics_task_turn ON context_metrics(task_id, solver_id, turn);
