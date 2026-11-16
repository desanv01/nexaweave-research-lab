ALTER TABLE mf_native_launch.plans
    ADD COLUMN source_run_id uuid,
    ADD CONSTRAINT native_launch_source_run_fkey FOREIGN KEY (source_run_id)
        REFERENCES mf_native_launch.plans(run_id) ON DELETE RESTRICT,
    ADD CONSTRAINT native_launch_checkpoint_identity_check CHECK (COALESCE(
        (
            source_run_id IS NULL
            AND frozen #> '{identity,request,schema_version}' = '1'::jsonb
            AND frozen #> '{identity,request,source_run_id}' IS NULL
            AND frozen #> '{identity,request,checkpoint_sha256}' IS NULL
            AND (
                (frozen #> '{identity,schema_version}' = '1'::jsonb
                 AND frozen #> '{identity,checkpoint}' IS NULL)
                OR
                (frozen #> '{identity,schema_version}' = '2'::jsonb
                 AND frozen #> '{identity,checkpoint}' = '{"mode":"capture"}'::jsonb)
            )
        )
        OR (
            source_run_id IS NOT NULL
            AND source_run_id <> run_id
            AND frozen #> '{identity,schema_version}' = '2'::jsonb
            AND frozen #> '{identity,request,schema_version}' = '2'::jsonb
            AND frozen #> '{identity,request,source_run_id}' = to_jsonb(source_run_id::text)
            AND jsonb_typeof(frozen #> '{identity,request,checkpoint_sha256}') = 'string'
            AND (frozen #>> '{identity,request,checkpoint_sha256}') ~ '^[0-9a-f]{64}$'
            AND frozen #> '{identity,checkpoint}' = jsonb_build_object(
                'mode', 'continue',
                'source_run_id', source_run_id::text,
                'checkpoint_sha256', frozen #>> '{identity,request,checkpoint_sha256}'
            )
        ), false));

DROP INDEX mf_native_launch.native_launch_one_dispatch;
CREATE UNIQUE INDEX native_launch_one_dispatch
    ON mf_native_launch.plans(principal,preparation_id)
    WHERE dispatch_claimed AND source_run_id IS NULL;
CREATE UNIQUE INDEX native_launch_one_continuation_dispatch
    ON mf_native_launch.plans(source_run_id)
    WHERE dispatch_claimed AND source_run_id IS NOT NULL;
