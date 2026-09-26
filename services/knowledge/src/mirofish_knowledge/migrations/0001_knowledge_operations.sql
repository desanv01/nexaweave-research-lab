CREATE SCHEMA mf_knowledge;

CREATE TABLE mf_knowledge.schema_migrations (
    version integer PRIMARY KEY,
    checksum text NOT NULL CHECK (checksum ~ '^[0-9a-f]{64}$'),
    schema_checksum text NOT NULL CHECK (schema_checksum ~ '^[0-9a-f]{64}$'),
    applied_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE mf_knowledge.scopes (
    group_id text PRIMARY KEY CHECK (group_id ~ '^mf1_[0-9a-f]{64}$'),
    canonical_scope jsonb NOT NULL,
    tombstoned boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE mf_knowledge.operations (
    group_id text NOT NULL REFERENCES mf_knowledge.scopes(group_id),
    operation_id uuid NOT NULL,
    fingerprint text NOT NULL CHECK (fingerprint ~ '^[0-9a-f]{64}$'),
    state text NOT NULL CHECK (state IN ('pending', 'running', 'completed', 'uncertain', 'cancelled', 'failed_no_effect')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    current_attempt uuid,
    receipt jsonb,
    error_code text CHECK (error_code ~ '^[a-z][a-z0-9_]{0,63}$'),
    PRIMARY KEY (group_id, operation_id),
    CHECK ((state = 'completed') = (receipt IS NOT NULL)),
    CHECK (state NOT IN ('running', 'uncertain') OR current_attempt IS NOT NULL),
    CHECK (state NOT IN ('pending', 'cancelled') OR current_attempt IS NULL)
);

CREATE TABLE mf_knowledge.attempts (
    group_id text NOT NULL,
    operation_id uuid NOT NULL,
    attempt_id uuid NOT NULL,
    started_at timestamptz NOT NULL DEFAULT now(),
    heartbeat_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    outcome text CHECK (outcome IN ('completed', 'uncertain', 'failed_no_effect')),
    error_code text CHECK (error_code ~ '^[a-z][a-z0-9_]{0,63}$'),
    PRIMARY KEY (group_id, operation_id, attempt_id),
    FOREIGN KEY (group_id, operation_id) REFERENCES mf_knowledge.operations(group_id, operation_id),
    CHECK ((finished_at IS NULL) = (outcome IS NULL))
);

CREATE TABLE mf_knowledge.scope_admissions (
    group_id text PRIMARY KEY REFERENCES mf_knowledge.scopes(group_id),
    operation_id uuid NOT NULL,
    attempt_id uuid NOT NULL,
    FOREIGN KEY (group_id, operation_id, attempt_id)
        REFERENCES mf_knowledge.attempts(group_id, operation_id, attempt_id)
);

CREATE INDEX scope_admissions_attempt_fk_idx ON mf_knowledge.scope_admissions (group_id, operation_id, attempt_id);
