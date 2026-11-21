ALTER TABLE mf_native_launch.plans
    DROP CONSTRAINT native_launch_checkpoint_identity_check,
    ADD CONSTRAINT native_launch_checkpoint_identity_check CHECK (COALESCE(
        (
            source_run_id IS NULL AND intervention IS NULL
            AND frozen #> '{identity,request,schema_version}' = '1'::jsonb
            AND frozen #> '{identity,request,source_run_id}' IS NULL
            AND frozen #> '{identity,request,checkpoint_sha256}' IS NULL
            AND frozen #> '{identity,request,intervention}' IS NULL
            AND (
                (frozen #> '{identity,schema_version}' = '1'::jsonb
                 AND frozen #> '{identity,checkpoint}' IS NULL)
                OR
                (frozen #> '{identity,schema_version}' = '2'::jsonb
                 AND frozen #> '{identity,checkpoint}' = '{"mode":"capture"}'::jsonb)
            )
        )
        OR (
            source_run_id IS NOT NULL AND source_run_id <> run_id
            AND frozen #> '{identity,request,source_run_id}' = to_jsonb(source_run_id::text)
            AND jsonb_typeof(frozen #> '{identity,request,checkpoint_sha256}') = 'string'
            AND (frozen #>> '{identity,request,checkpoint_sha256}') ~ '^[0-9a-f]{64}$'
            AND (
                (intervention IS NULL
                 AND frozen #> '{identity,schema_version}' = '2'::jsonb
                 AND frozen #> '{identity,request,schema_version}' = '2'::jsonb
                 AND frozen #> '{identity,request,intervention}' IS NULL
                 AND frozen #> '{identity,checkpoint}' = jsonb_build_object(
                     'mode', 'continue', 'source_run_id', source_run_id::text,
                     'checkpoint_sha256', frozen #>> '{identity,request,checkpoint_sha256}'))
                OR
                (intervention IS NOT NULL
                 AND intervention = jsonb_build_object('kind', 'swap_active_actor_v1',
                     'manifest_sha256', intervention->>'manifest_sha256',
                     'runner_state_sha256', intervention->>'runner_state_sha256')
                 AND (intervention->>'manifest_sha256') ~ '^[0-9a-f]{64}$'
                 AND (intervention->>'runner_state_sha256') ~ '^[0-9a-f]{64}$'
                 AND frozen #> '{identity,schema_version}' = '3'::jsonb
                 AND frozen #> '{identity,request,schema_version}' = '3'::jsonb
                 AND frozen #> '{identity,request,platforms}' = '["twitter"]'::jsonb
                 AND frozen #> '{identity,request,max_rounds}' = '1'::jsonb
                 AND frozen #> '{identity,request,intervention}' = intervention
                 AND frozen #> '{identity,checkpoint}' = jsonb_build_object(
                     'mode', 'fork', 'source_run_id', source_run_id::text,
                     'checkpoint_sha256', frozen #>> '{identity,request,checkpoint_sha256}',
                     'intervention', intervention)
                 AND frozen #> '{declaration,schema_version}' = '3'::jsonb
                 AND frozen #> '{declaration,checkpoint}' = frozen #> '{identity,checkpoint}')
            )
        )
        OR (
            intervention IS NULL
            AND frozen #> '{identity,schema_version}' = '4'::jsonb
            AND frozen #> '{declaration,schema_version}' = '4'::jsonb
            AND frozen #> '{identity,request,platforms}' = '["reddit"]'::jsonb
            AND frozen #> '{identity,request,max_rounds}' = '1'::jsonb
            AND frozen #> '{identity,request,intervention}' IS NULL
            AND frozen #> '{declaration,checkpoint}' = frozen #> '{identity,checkpoint}'
            AND (
                (source_run_id IS NULL
                 AND frozen #> '{identity,request,schema_version}' = '1'::jsonb
                 AND frozen #> '{identity,request,source_run_id}' IS NULL
                 AND frozen #> '{identity,request,checkpoint_sha256}' IS NULL
                 AND frozen #> '{identity,checkpoint}' = '{"mode":"capture"}'::jsonb)
                OR
                (source_run_id IS NOT NULL AND source_run_id <> run_id
                 AND frozen #> '{identity,request,schema_version}' = '4'::jsonb
                 AND frozen #> '{identity,request,source_run_id}' = to_jsonb(source_run_id::text)
                 AND jsonb_typeof(frozen #> '{identity,request,checkpoint_sha256}') = 'string'
                 AND (frozen #>> '{identity,request,checkpoint_sha256}') ~ '^[0-9a-f]{64}$'
                 AND frozen #> '{identity,checkpoint}' = jsonb_build_object(
                     'mode', 'continue', 'source_run_id', source_run_id::text,
                     'checkpoint_sha256', frozen #>> '{identity,request,checkpoint_sha256}'))
            )
        ), false));

CREATE FUNCTION mf_native_launch.guard_reddit_identity() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF ((OLD.frozen #> '{identity,schema_version}') = '4'::jsonb OR (NEW.frozen #> '{identity,schema_version}') = '4'::jsonb) AND (
        (OLD.run_id, OLD.principal, OLD.preparation_id, OLD.simulation_id, OLD.project_id,
         OLD.project_revision, OLD.request_sha256, OLD.launch_sha256, OLD.frozen,
         OLD.source_run_id, OLD.intervention) IS DISTINCT FROM
        (NEW.run_id, NEW.principal, NEW.preparation_id, NEW.simulation_id, NEW.project_id,
         NEW.project_revision, NEW.request_sha256, NEW.launch_sha256, NEW.frozen,
         NEW.source_run_id, NEW.intervention)
        OR (OLD.cancel_requested AND NOT NEW.cancel_requested)
        OR (OLD.dispatch_claimed AND (NOT NEW.dispatch_claimed
            OR OLD.budget_attempt_id IS DISTINCT FROM NEW.budget_attempt_id))) THEN
        RAISE check_violation USING MESSAGE = 'native_launch_reddit_identity_immutable';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER native_launch_reddit_identity_guard BEFORE UPDATE ON mf_native_launch.plans
FOR EACH ROW EXECUTE FUNCTION mf_native_launch.guard_reddit_identity();
