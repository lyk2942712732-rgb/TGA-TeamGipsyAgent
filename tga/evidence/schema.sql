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
    updated_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 6
);

CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    intent_id TEXT,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 6
);

CREATE TABLE IF NOT EXISTS findings (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL,
    evidence_artifact_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 6
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

CREATE TABLE IF NOT EXISTS agent_events (
    id TEXT PRIMARY KEY,
    schema_version INTEGER NOT NULL DEFAULT 2,
    task_id TEXT NOT NULL,
    solver_id TEXT,
    intent_id TEXT,
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

CREATE TABLE IF NOT EXISTS runtime_leases (
    task_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    expires_at REAL NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS challenge_contracts (
    task_id TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_agent_events_task_seq ON agent_events(task_id, seq);
CREATE INDEX IF NOT EXISTS idx_artifact_indexes_task_created ON artifact_indexes(task_id, created_at);
CREATE INDEX IF NOT EXISTS idx_context_metrics_task_turn ON context_metrics(task_id, solver_id, turn);
