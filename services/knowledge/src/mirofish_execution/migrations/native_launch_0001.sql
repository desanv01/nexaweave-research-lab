CREATE SCHEMA mf_native_launch;
CREATE TABLE mf_native_launch.schema_migrations (
    version integer PRIMARY KEY,
    checksum text NOT NULL,
    schema_checksum text NOT NULL
);
CREATE TABLE mf_native_launch.plans (
    run_id uuid PRIMARY KEY,
    principal text NOT NULL,
    preparation_id uuid NOT NULL,
    simulation_id text NOT NULL,
    project_id uuid NOT NULL,
    project_revision integer NOT NULL CHECK (project_revision > 0),
    request_sha256 text NOT NULL CHECK (request_sha256 ~ '^[0-9a-f]{64}$'),
    launch_sha256 text NOT NULL CHECK (launch_sha256 ~ '^[0-9a-f]{64}$'),
    frozen jsonb NOT NULL,
    state text NOT NULL CHECK (state IN ('planned','queued','starting','running','completed','failed','cancelled','uncertain')),
    budget_attempt_id uuid,
    workflow jsonb,
    receipt jsonb,
    cancel_requested boolean NOT NULL DEFAULT false,
    error_code text,
    dispatch_claimed boolean NOT NULL DEFAULT false,
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((dispatch_claimed AND budget_attempt_id IS NOT NULL AND state<>'planned') OR
           (NOT dispatch_claimed AND budget_attempt_id IS NULL AND
            ((state='planned' AND NOT cancel_requested) OR (state='cancelled' AND cancel_requested)))),
    CHECK ((dispatch_claimed AND state IN ('completed','failed','cancelled')) = (receipt IS NOT NULL)),
    CHECK ((state='uncertain' AND error_code IS NOT NULL AND error_code='native_launch_uncertain') OR
           (state<>'uncertain' AND error_code IS NULL)),
    CHECK (workflow IS NULL OR dispatch_claimed),
    CHECK (jsonb_typeof(frozen)='object' AND frozen ? 'identity' AND frozen ? 'declaration' AND frozen ? 'configuration'
           AND (frozen - ARRAY['identity','declaration','configuration'])='{}'::jsonb),
    CHECK (jsonb_typeof(frozen->'configuration')='object' AND
           frozen->'configuration' ? 'account_id' AND frozen->'configuration' ? 'factory_sha256'
           AND ((frozen->'configuration') - ARRAY['account_id','factory_sha256'])='{}'::jsonb
           AND jsonb_typeof(frozen->'configuration'->'factory_sha256')='string'
           AND (frozen->'configuration'->>'factory_sha256') ~ '^[0-9a-f]{64}$'),
    CHECK (NOT dispatch_claimed OR
           (jsonb_typeof(frozen->'configuration'->'account_id')='string'
            AND jsonb_typeof(frozen->'identity'->'ceiling_microusd')='string'))
);
CREATE UNIQUE INDEX native_launch_one_dispatch
    ON mf_native_launch.plans(principal,preparation_id) WHERE dispatch_claimed;
