CREATE SCHEMA mf_execution;
CREATE TABLE mf_execution.schema_migrations (
    version integer PRIMARY KEY,
    checksum text NOT NULL CHECK (checksum ~ '^[0-9a-f]{64}$'),
    schema_checksum text NOT NULL CHECK (schema_checksum ~ '^[0-9a-f]{64}$'),
    applied_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE mf_execution.accounts (
    account_id uuid PRIMARY KEY,
    principal text NOT NULL,
    project_id uuid NOT NULL UNIQUE,
    cap_microusd bigint NOT NULL CHECK (cap_microusd > 0),
    currency text NOT NULL DEFAULT 'USD' CHECK (currency = 'USD'),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE mf_execution.reservations (
    account_id uuid NOT NULL REFERENCES mf_execution.accounts(account_id),
    operation_id uuid NOT NULL,
    fingerprint text NOT NULL CHECK (fingerprint ~ '^[0-9a-f]{64}$'),
    ceiling_microusd bigint NOT NULL CHECK (ceiling_microusd > 0),
    state text NOT NULL CHECK (state IN ('reserved','started','settled','uncertain','released')),
    attempt_id uuid NOT NULL,
    scope_group_id text NOT NULL CHECK (scope_group_id ~ '^mf1_[0-9a-f]{64}$'),
    episode_id uuid NOT NULL,
    evidence_ids jsonb NOT NULL,
    receipt jsonb,
    error_code text CHECK (error_code ~ '^[a-z][a-z0-9_]{0,63}$'),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (account_id,operation_id),
    CHECK ((state = 'settled') = (receipt IS NOT NULL))
);
CREATE INDEX reservations_account_state_idx ON mf_execution.reservations(account_id,state);
