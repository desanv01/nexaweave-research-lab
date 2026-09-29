CREATE SCHEMA mf_app;

CREATE TABLE mf_app.schema_migrations (
    version integer PRIMARY KEY CHECK (version > 0),
    sql_sha256 char(64) NOT NULL CHECK (sql_sha256 ~ '^[0-9a-f]{64}$'),
    catalog_sha256 char(64) NOT NULL CHECK (catalog_sha256 ~ '^[0-9a-f]{64}$')
);

CREATE TABLE mf_app.projects (
    project_id uuid PRIMARY KEY,
    principal varchar(128) COLLATE "C" NOT NULL CHECK (principal ~ '^[ -~]{1,128}$' AND btrim(principal) <> ''),
    workspace_id uuid NOT NULL,
    display_id varchar(128) COLLATE "C" NOT NULL CHECK (display_id ~ '^[A-Za-z0-9_-]{1,128}$'),
    current_revision integer NOT NULL CHECK (current_revision >= 1),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (principal, workspace_id, display_id)
);
CREATE INDEX projects_owner_workspace_list ON mf_app.projects
    (principal, workspace_id, created_at DESC, project_id);

CREATE TABLE mf_app.project_revisions (
    project_id uuid NOT NULL REFERENCES mf_app.projects(project_id) ON DELETE RESTRICT,
    revision integer NOT NULL CHECK (revision >= 1),
    snapshot jsonb NOT NULL CHECK (jsonb_typeof(snapshot) = 'object'),
    evidence jsonb NOT NULL CHECK (jsonb_typeof(evidence) = 'array'),
    digest char(64) NOT NULL CHECK (digest ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, revision)
);

ALTER TABLE mf_app.projects ADD CONSTRAINT projects_current_revision_fkey
    FOREIGN KEY (project_id, current_revision)
    REFERENCES mf_app.project_revisions(project_id, revision)
    DEFERRABLE INITIALLY DEFERRED;
