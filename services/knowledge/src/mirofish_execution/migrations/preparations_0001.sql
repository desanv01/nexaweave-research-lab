CREATE SCHEMA mf_preparation;
CREATE TABLE mf_preparation.schema_migrations (
    version integer PRIMARY KEY,
    checksum text NOT NULL CHECK (checksum ~ '^[0-9a-f]{64}$'),
    schema_checksum text NOT NULL CHECK (schema_checksum ~ '^[0-9a-f]{64}$')
);
CREATE TABLE mf_preparation.plans (
    operation_id uuid PRIMARY KEY,
    principal text NOT NULL,
    project_id uuid NOT NULL,
    project_revision bigint NOT NULL CHECK (project_revision BETWEEN 1 AND 9007199254740991),
    display_graph_id text NOT NULL,
    request_sha256 text NOT NULL CHECK (request_sha256 ~ '^[0-9a-f]{64}$'),
    plan_sha256 text NOT NULL CHECK (plan_sha256 ~ '^[0-9a-f]{64}$'),
    frozen jsonb NOT NULL,
    state text NOT NULL CHECK (state IN ('planned','queued','preparing','ready','failed','cancelled','uncertain')),
    stage text NOT NULL,
    completed integer NOT NULL CHECK (completed BETWEEN 0 AND 100),
    attempt_id uuid,
    budget_attempt_id uuid,
    model_calls_started boolean NOT NULL DEFAULT false,
    receipt jsonb,
    error_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((state = 'ready') = (receipt IS NOT NULL)),
    CHECK (state <> 'ready' OR (completed = 100 AND stage = 'ready' AND error_code IS NULL AND model_calls_started)),
    CHECK (state <> 'planned' OR (attempt_id IS NULL AND NOT model_calls_started AND completed = 0))
);
CREATE INDEX plans_owned_idx ON mf_preparation.plans(principal,project_id);
