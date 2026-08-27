CREATE TABLE task_runs (
    id UUID PRIMARY KEY,
    title TEXT NOT NULL,
    scene_id TEXT NOT NULL,
    state TEXT NOT NULL,
    blackboard_seq BIGINT NOT NULL DEFAULT 0,
    dialogue_seq BIGINT NOT NULL DEFAULT 0,
    final_snapshot_seq BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE agent_runs (
    task_id UUID NOT NULL REFERENCES task_runs(id) ON DELETE CASCADE,
    agent_id TEXT NOT NULL,
    sdk TEXT NOT NULL,
    desired_state TEXT NOT NULL,
    actual_state TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    model_id TEXT NOT NULL,
    protocol TEXT NOT NULL,
    container_id TEXT,
    session_id TEXT,
    last_error TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    connected_at TIMESTAMPTZ,
    last_heartbeat TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (task_id, agent_id)
);

CREATE TABLE input_files (
    id UUID PRIMARY KEY,
    task_id UUID NOT NULL REFERENCES task_runs(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    storage_path TEXT NOT NULL,
    media_type TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size_bytes BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE artifacts (
    id UUID PRIMARY KEY,
    task_id UUID NOT NULL REFERENCES task_runs(id) ON DELETE CASCADE,
    created_by JSONB NOT NULL,
    name TEXT NOT NULL,
    storage_path TEXT NOT NULL,
    media_type TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size_bytes BIGINT NOT NULL,
    available BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX artifacts_task_idx ON artifacts(task_id);

CREATE TABLE blackboard_entries (
    id UUID PRIMARY KEY,
    task_id UUID NOT NULL REFERENCES task_runs(id) ON DELETE CASCADE,
    seq BIGINT NOT NULL,
    actor JSONB NOT NULL,
    actor_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    topic TEXT NOT NULL,
    body JSONB NOT NULL,
    idempotency_key TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (task_id, seq),
    UNIQUE (task_id, actor_id, idempotency_key)
);

CREATE INDEX blackboard_task_seq_idx ON blackboard_entries(task_id, seq);

CREATE TABLE blackboard_artifact_links (
    entry_id UUID NOT NULL REFERENCES blackboard_entries(id) ON DELETE CASCADE,
    artifact_id UUID NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
    locator TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (entry_id, artifact_id, locator)
);

CREATE TABLE dialogue_messages (
    id UUID PRIMARY KEY,
    task_id UUID NOT NULL REFERENCES task_runs(id) ON DELETE CASCADE,
    seq BIGINT NOT NULL,
    channel_agent_id TEXT NOT NULL,
    actor JSONB NOT NULL,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (task_id, seq)
);

CREATE INDEX dialogue_task_seq_idx ON dialogue_messages(task_id, seq);

CREATE TABLE pending_questions (
    id UUID PRIMARY KEY,
    task_id UUID NOT NULL REFERENCES task_runs(id) ON DELETE CASCADE,
    question TEXT NOT NULL,
    origin JSONB NOT NULL,
    asked_by JSONB NOT NULL,
    state TEXT NOT NULL DEFAULT 'waiting',
    answer TEXT,
    answer_attachment_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    answered_at TIMESTAMPTZ
);

CREATE INDEX pending_questions_waiting_idx
    ON pending_questions(task_id, state);

CREATE TABLE writeups (
    id UUID PRIMARY KEY,
    task_id UUID NOT NULL REFERENCES task_runs(id) ON DELETE CASCADE,
    snapshot_seq BIGINT NOT NULL,
    storage_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
