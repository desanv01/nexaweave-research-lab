CREATE SCHEMA mf_native_execution;

CREATE TABLE mf_native_execution.schema_migrations (
    version integer PRIMARY KEY CHECK (version = 1),
    sql_sha256 char(64) NOT NULL CHECK (sql_sha256 ~ '^[0-9a-f]{64}$'),
    catalog_sha256 char(64) NOT NULL CHECK (catalog_sha256 ~ '^[0-9a-f]{64}$')
);

CREATE TABLE mf_native_execution.runs (
    run_id uuid PRIMARY KEY,
    principal varchar(128) COLLATE "C" NOT NULL
        CHECK (principal ~ '^[ -~]{1,128}$' AND btrim(principal) <> ''),
    project_id uuid NOT NULL,
    project_revision integer NOT NULL CHECK (project_revision > 0),
    simulation_id varchar(128) COLLATE "C" NOT NULL
        CHECK (simulation_id ~ '^[A-Za-z0-9_-]{1,128}$'),
    artifact_sha256 char(64) NOT NULL CHECK (artifact_sha256 ~ '^[0-9a-f]{64}$'),
    runtime_sha256 char(64) NOT NULL CHECK (runtime_sha256 ~ '^[0-9a-f]{64}$'),
    platforms text[] NOT NULL CHECK (platforms IN
        (ARRAY['twitter']::text[], ARRAY['reddit']::text[], ARRAY['twitter','reddit']::text[])),
    seed bigint NOT NULL,
    max_rounds smallint NOT NULL CHECK (max_rounds BETWEEN 1 AND 24),
    request_fingerprint char(64) NOT NULL CHECK (request_fingerprint ~ '^[0-9a-f]{64}$'),
    state text NOT NULL DEFAULT 'declared' CHECK (state IN
        ('declared','starting','running','completed','failed','cancelled','uncertain')),
    cancel_requested boolean NOT NULL DEFAULT false,
    attempt_id uuid,
    owner_id uuid,
    lease_until timestamptz,
    child_instance_id uuid,
    process_id integer CHECK (process_id > 0),
    process_fingerprint char(64) CHECK (process_fingerprint ~ '^[0-9a-f]{64}$'),
    receipt jsonb,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (project_id, simulation_id),
    FOREIGN KEY (project_id, project_revision)
        REFERENCES mf_app.project_revisions(project_id, revision) ON DELETE RESTRICT,
    CHECK ((state = 'declared') = (attempt_id IS NULL)),
    CHECK ((attempt_id IS NULL) = (owner_id IS NULL)),
    CHECK ((state IN ('starting','running')) = (lease_until IS NOT NULL)),
    CHECK ((child_instance_id IS NULL) = (process_id IS NULL)),
    CHECK ((child_instance_id IS NULL) = (process_fingerprint IS NULL)),
    CHECK (state NOT IN ('declared','starting') OR child_instance_id IS NULL),
    CHECK (state NOT IN ('running','completed','failed','cancelled') OR child_instance_id IS NOT NULL),
    CHECK ((state IN ('completed','failed','cancelled')) = (receipt IS NOT NULL)),
    CHECK (receipt IS NULL OR COALESCE(
        (jsonb_typeof(receipt) = 'object' AND receipt ?& ARRAY[
            'run_id','attempt_id','instance_id','request_fingerprint','outcome','evidence_sha256']
         AND receipt - ARRAY[
             'run_id','attempt_id','instance_id','request_fingerprint','outcome','evidence_sha256']
             = '{}'::jsonb
         AND jsonb_typeof(receipt->'run_id') = 'string'
         AND jsonb_typeof(receipt->'attempt_id') = 'string'
         AND jsonb_typeof(receipt->'instance_id') = 'string'
         AND jsonb_typeof(receipt->'request_fingerprint') = 'string'
         AND jsonb_typeof(receipt->'outcome') = 'string'
         AND jsonb_typeof(receipt->'evidence_sha256') = 'string'
         AND receipt->>'run_id' = run_id::text
         AND receipt->>'attempt_id' = attempt_id::text
         AND receipt->>'instance_id' = child_instance_id::text
         AND receipt->>'request_fingerprint' = request_fingerprint
         AND receipt->>'outcome' = state
         AND receipt->>'evidence_sha256' ~ '^[0-9a-f]{64}$'), false))
);

CREATE INDEX runs_principal_project_idx ON mf_native_execution.runs
    (principal, project_id, run_id);
CREATE INDEX runs_expiry_idx ON mf_native_execution.runs
    (lease_until, run_id) WHERE state IN ('starting','running');

CREATE FUNCTION mf_native_execution.guard_run_identity() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF (OLD.run_id, OLD.principal, OLD.project_id, OLD.project_revision,
        OLD.simulation_id, OLD.artifact_sha256, OLD.runtime_sha256,
        OLD.platforms, OLD.seed, OLD.max_rounds, OLD.request_fingerprint)
       IS DISTINCT FROM
       (NEW.run_id, NEW.principal, NEW.project_id, NEW.project_revision,
        NEW.simulation_id, NEW.artifact_sha256, NEW.runtime_sha256,
        NEW.platforms, NEW.seed, NEW.max_rounds, NEW.request_fingerprint) THEN
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

CREATE TRIGGER runs_identity_guard BEFORE UPDATE ON mf_native_execution.runs
FOR EACH ROW EXECUTE FUNCTION mf_native_execution.guard_run_identity();
