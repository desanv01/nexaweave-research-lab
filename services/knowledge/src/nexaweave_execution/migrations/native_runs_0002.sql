ALTER TABLE mf_native_execution.schema_migrations
    DROP CONSTRAINT schema_migrations_version_check,
    ADD CONSTRAINT schema_migrations_version_check CHECK (version IN (1, 2));

-- The nullable pair distinguishes existing v1 rows from v2 continuations.
ALTER TABLE mf_native_execution.runs
    ADD COLUMN source_run_id uuid,
    ADD COLUMN checkpoint_sha256 char(64),
    ADD CONSTRAINT runs_source_run_id_fkey FOREIGN KEY (source_run_id)
        REFERENCES mf_native_execution.runs(run_id) ON DELETE RESTRICT,
    ADD CONSTRAINT runs_checkpoint_pair_check CHECK (
        (source_run_id IS NULL AND checkpoint_sha256 IS NULL)
        OR (source_run_id IS NOT NULL AND checkpoint_sha256 IS NOT NULL
            AND source_run_id <> run_id AND checkpoint_sha256 ~ '^[0-9a-f]{64}$')
    ),
    DROP CONSTRAINT runs_project_id_simulation_id_key;

CREATE UNIQUE INDEX runs_fresh_project_simulation_idx ON mf_native_execution.runs
    (project_id, simulation_id) WHERE source_run_id IS NULL;
CREATE UNIQUE INDEX runs_continuation_source_idx ON mf_native_execution.runs
    (source_run_id) WHERE source_run_id IS NOT NULL;

CREATE OR REPLACE FUNCTION mf_native_execution.guard_run_identity() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF (OLD.run_id, OLD.principal, OLD.project_id, OLD.project_revision,
        OLD.simulation_id, OLD.artifact_sha256, OLD.runtime_sha256,
        OLD.platforms, OLD.seed, OLD.max_rounds, OLD.request_fingerprint,
        OLD.source_run_id, OLD.checkpoint_sha256)
       IS DISTINCT FROM
       (NEW.run_id, NEW.principal, NEW.project_id, NEW.project_revision,
        NEW.simulation_id, NEW.artifact_sha256, NEW.runtime_sha256,
        NEW.platforms, NEW.seed, NEW.max_rounds, NEW.request_fingerprint,
        NEW.source_run_id, NEW.checkpoint_sha256) THEN
        RAISE check_violation USING MESSAGE = 'native_run_identity_immutable';
    END IF;
    IF OLD.state <> 'declared' AND
       (OLD.attempt_id, OLD.owner_id) IS DISTINCT FROM (NEW.attempt_id, NEW.owner_id) THEN
        RAISE check_violation USING MESSAGE = 'native_run_attempt_immutable';
    END IF;
    IF OLD.child_instance_id IS NOT NULL AND
       (OLD.child_instance_id, OLD.process_id, OLD.process_fingerprint)
       IS DISTINCT FROM (NEW.child_instance_id, NEW.process_id, NEW.process_fingerprint) THEN
        RAISE check_violation USING MESSAGE = 'native_run_child_immutable';
    END IF;
    IF (OLD.cancel_requested AND NOT NEW.cancel_requested)
       OR (OLD.receipt IS NOT NULL AND OLD.receipt IS DISTINCT FROM NEW.receipt)
       OR (OLD.state = 'declared' AND NEW.state NOT IN ('declared','starting'))
       OR (OLD.state = 'starting' AND NEW.state NOT IN ('starting','running','uncertain'))
       OR (OLD.state = 'running' AND NEW.state NOT IN
           ('running','completed','failed','cancelled','uncertain'))
       OR (OLD.state IN ('completed','failed','cancelled','uncertain') AND NEW.state <> OLD.state) THEN
        RAISE check_violation USING MESSAGE = 'native_run_transition_immutable';
    END IF;
    RETURN NEW;
END;
$$;
