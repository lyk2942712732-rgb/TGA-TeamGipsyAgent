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
    stop_reason TEXT NOT NULL DEFAULT ''
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
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
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

CREATE INDEX IF NOT EXISTS idx_agent_events_task_seq ON agent_events(task_id, seq);
CREATE INDEX IF NOT EXISTS idx_hypotheses_task_status ON hypotheses(task_id, status);
CREATE INDEX IF NOT EXISTS idx_memory_entries_task_created ON memory_entries(task_id, created_at);
CREATE INDEX IF NOT EXISTS idx_actions_task_created ON actions(task_id, created_at);
