CREATE SCHEMA mf_report;
CREATE TABLE mf_report.schema_migrations (
    version integer PRIMARY KEY CHECK(version=1),
    checksum text NOT NULL CHECK(checksum ~ '^[0-9a-f]{64}$'),
    schema_checksum text NOT NULL CHECK(schema_checksum ~ '^[0-9a-f]{64}$')
);
CREATE TABLE mf_report.plans (
    report_id uuid PRIMARY KEY,
    principal text NOT NULL CHECK(length(principal) BETWEEN 1 AND 128),
    plan_sha256 text NOT NULL CHECK(plan_sha256 ~ '^[0-9a-f]{64}$'),
    declaration_sha256 text NOT NULL CHECK(declaration_sha256 ~ '^[0-9a-f]{64}$'),
    frozen jsonb NOT NULL CHECK(jsonb_typeof(frozen)='object' AND octet_length(frozen::text)<=3145728),
    state text NOT NULL CHECK(state IN ('planned','queued','generating','completed','failed','cancelled','uncertain')),
    attempt_id uuid,
    workflow jsonb,
    receipt jsonb,
    manifest jsonb,
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
    CHECK((state='completed')=(receipt IS NOT NULL AND manifest IS NOT NULL)),
    CHECK(state<>'planned' OR NOT dispatch_claimed)
);
CREATE INDEX report_principal ON mf_report.plans(principal,report_id);
