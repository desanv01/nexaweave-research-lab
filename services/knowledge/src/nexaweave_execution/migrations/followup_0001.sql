CREATE SCHEMA mf_followup;
CREATE TABLE mf_followup.schema_migrations (
    version integer PRIMARY KEY CHECK(version=1),
    checksum text NOT NULL CHECK(checksum ~ '^[0-9a-f]{64}$'),
    schema_checksum text NOT NULL CHECK(schema_checksum ~ '^[0-9a-f]{64}$')
);
CREATE TABLE mf_followup.histories (
    report_id uuid PRIMARY KEY,
    principal text NOT NULL CHECK(length(principal) BETWEEN 1 AND 128),
    report_plan_sha256 text NOT NULL CHECK(report_plan_sha256 ~ '^[0-9a-f]{64}$'),
    head_sha256 text NOT NULL CHECK(head_sha256 ~ '^[0-9a-f]{64}$'),
    total_completed integer NOT NULL DEFAULT 0 CHECK(total_completed BETWEEN 0 AND 1000),
    active_turn_id uuid,
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE mf_followup.turns (
    turn_id uuid PRIMARY KEY,
    principal text NOT NULL CHECK(length(principal) BETWEEN 1 AND 128),
    report_id uuid NOT NULL REFERENCES mf_followup.histories(report_id),
    plan_sha256 text NOT NULL CHECK(plan_sha256 ~ '^[0-9a-f]{64}$'),
    frozen jsonb NOT NULL CHECK(jsonb_typeof(frozen)='object' AND octet_length(frozen::text)<=3145728),
    state text NOT NULL CHECK(state IN ('planned','queued','generating','completed','failed','cancelled','uncertain')),
    attempt_id uuid,
    workflow jsonb,
    receipt jsonb,
    manifest jsonb,
    answer_sha256 text CHECK(answer_sha256 ~ '^[0-9a-f]{64}$'),
    answer_prefix text,
    answer_characters integer CHECK(answer_characters BETWEEN 1 AND 16384),
    published_head_sha256 text CHECK(published_head_sha256 ~ '^[0-9a-f]{64}$'),
    progress jsonb NOT NULL,
    cleanup jsonb NOT NULL,
    cancel_requested boolean NOT NULL DEFAULT false,
    dispatch_claimed boolean NOT NULL DEFAULT false,
    owner_claimed boolean NOT NULL DEFAULT false,
    first_possible_request boolean NOT NULL DEFAULT false,
    error_code text,
    owner_deadline timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK(dispatch_claimed=(attempt_id IS NOT NULL)),
    CHECK(NOT owner_claimed OR dispatch_claimed),
    CHECK(NOT first_possible_request OR owner_claimed),
    CHECK((state='completed')=(receipt IS NOT NULL AND manifest IS NOT NULL AND answer_sha256 IS NOT NULL AND answer_prefix IS NOT NULL AND answer_characters IS NOT NULL AND published_head_sha256 IS NOT NULL)),
    CHECK(state<>'planned' OR NOT dispatch_claimed)
);
CREATE UNIQUE INDEX followup_completed_ordinal ON mf_followup.turns
    (report_id, ((receipt->>'ordinal')::integer)) WHERE state='completed';
CREATE INDEX followup_principal_report ON mf_followup.turns(principal,report_id,turn_id);
