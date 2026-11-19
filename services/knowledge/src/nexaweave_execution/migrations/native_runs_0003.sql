ALTER TABLE mf_native_execution.schema_migrations
    DROP CONSTRAINT schema_migrations_version_check,
    ADD CONSTRAINT schema_migrations_version_check CHECK (version IN (1, 2, 3));

ALTER TABLE mf_native_execution.runs
    ADD COLUMN intervention jsonb,
    ADD CONSTRAINT runs_fork_intervention_check CHECK (intervention IS NULL OR COALESCE(
        source_run_id IS NOT NULL AND platforms = ARRAY['twitter']::text[] AND max_rounds = 1
        AND intervention = jsonb_build_object(
            'kind', 'swap_active_actor_v1',
            'manifest_sha256', intervention->>'manifest_sha256',
            'runner_state_sha256', intervention->>'runner_state_sha256')
        AND (intervention->>'manifest_sha256') ~ '^[0-9a-f]{64}$'
        AND (intervention->>'runner_state_sha256') ~ '^[0-9a-f]{64}$', false));

DROP INDEX mf_native_execution.runs_continuation_source_idx;
CREATE UNIQUE INDEX runs_continuation_source_idx ON mf_native_execution.runs (source_run_id)
    WHERE source_run_id IS NOT NULL AND intervention IS NULL;
CREATE UNIQUE INDEX runs_fork_source_idx ON mf_native_execution.runs (source_run_id)
    WHERE intervention IS NOT NULL;

CREATE FUNCTION mf_native_execution.guard_fork_intervention() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.intervention IS DISTINCT FROM NEW.intervention THEN
        RAISE check_violation USING MESSAGE = 'native_run_intervention_immutable';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER runs_fork_intervention_guard BEFORE UPDATE ON mf_native_execution.runs
FOR EACH ROW EXECUTE FUNCTION mf_native_execution.guard_fork_intervention();
