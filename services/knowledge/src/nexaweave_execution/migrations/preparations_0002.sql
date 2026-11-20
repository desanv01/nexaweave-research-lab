CREATE TABLE mf_preparation.seed_pairs (
    principal text NOT NULL,
    idempotency_key uuid NOT NULL,
    donor_operation_id uuid NOT NULL UNIQUE REFERENCES mf_preparation.plans(operation_id),
    alternate_operation_id uuid NOT NULL UNIQUE,
    request_sha256 text NOT NULL CHECK (request_sha256 ~ '^[0-9a-f]{64}$'),
    alternate_frozen jsonb NOT NULL,
    receipt jsonb NOT NULL,
    PRIMARY KEY (principal, idempotency_key),
    CHECK (alternate_operation_id <> donor_operation_id)
);
