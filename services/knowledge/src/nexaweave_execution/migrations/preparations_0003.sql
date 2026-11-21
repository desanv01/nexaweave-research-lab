CREATE TABLE mf_preparation.seed_ensembles (
    ensemble_id uuid PRIMARY KEY,
    principal text NOT NULL,
    idempotency_key uuid NOT NULL,
    account_id uuid NOT NULL,
    donor_operation_id uuid NOT NULL UNIQUE REFERENCES mf_preparation.plans(operation_id),
    request_sha256 text NOT NULL CHECK (request_sha256 ~ '^[0-9a-f]{64}$'),
    receipt jsonb NOT NULL,
    UNIQUE (principal, idempotency_key)
);

ALTER TABLE mf_preparation.plans ADD COLUMN seed_ensemble_id uuid
    REFERENCES mf_preparation.seed_ensembles(ensemble_id);

CREATE TABLE mf_preparation.seed_ensemble_members (
    ensemble_id uuid NOT NULL REFERENCES mf_preparation.seed_ensembles(ensemble_id),
    ordinal smallint NOT NULL CHECK (ordinal IN (1, 2)),
    operation_id uuid NOT NULL UNIQUE REFERENCES mf_preparation.plans(operation_id),
    launch_id uuid NOT NULL UNIQUE,
    seed bigint NOT NULL CHECK (seed BETWEEN 0 AND 4294967295),
    PRIMARY KEY (ensemble_id, ordinal),
    UNIQUE (ensemble_id, seed),
    UNIQUE (operation_id, ensemble_id)
);

ALTER TABLE mf_preparation.plans ADD CONSTRAINT plans_seed_ensemble_member_fk
    FOREIGN KEY (operation_id, seed_ensemble_id)
    REFERENCES mf_preparation.seed_ensemble_members(operation_id, ensemble_id)
    DEFERRABLE INITIALLY DEFERRED;

DO $migration$
DECLARE
    ready_check name;
BEGIN
    SELECT c.conname INTO STRICT ready_check
    FROM pg_catalog.pg_constraint AS c
    WHERE c.conrelid = 'mf_preparation.plans'::regclass
      AND c.contype = 'c'
      AND cardinality(c.conkey) = 5
      AND ARRAY(
          SELECT a.attnum FROM pg_catalog.pg_attribute AS a
          WHERE a.attrelid = c.conrelid
            AND a.attname IN ('state', 'completed', 'stage', 'error_code', 'model_calls_started')
      ) <@ c.conkey;
    EXECUTE format('ALTER TABLE mf_preparation.plans DROP CONSTRAINT %I', ready_check);
END;
$migration$;

ALTER TABLE mf_preparation.plans
    ADD CONSTRAINT plans_ready_provenance CHECK (
        state <> 'ready' OR (
            completed = 100 AND stage = 'ready' AND error_code IS NULL
            AND (model_calls_started OR seed_ensemble_id IS NOT NULL)
        )
    ),
    ADD CONSTRAINT plans_seed_ensemble_cold CHECK (
        seed_ensemble_id IS NULL OR (
            state = 'ready' AND attempt_id IS NULL AND budget_attempt_id IS NULL
            AND NOT model_calls_started
        )
    );
