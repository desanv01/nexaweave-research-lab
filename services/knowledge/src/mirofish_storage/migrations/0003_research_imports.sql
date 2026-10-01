CREATE TABLE mf_app.research_imports (
    target_project_id uuid NOT NULL REFERENCES mf_app.projects(project_id) ON DELETE RESTRICT,
    artifact_sha256 char(64) NOT NULL CHECK (artifact_sha256 ~ '^[0-9a-f]{64}$'),
    payload_sha256 char(64) NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    origin_project_id uuid NOT NULL,
    origin_revision integer NOT NULL CHECK (origin_revision > 0),
    target_revision integer NOT NULL CHECK (target_revision > 0),
    imported_at timestamptz NOT NULL CHECK (isfinite(imported_at)),
    provenance jsonb NOT NULL CHECK (
        jsonb_typeof(provenance) = 'object' AND
        octet_length(provenance::text) <= 2097152 AND
        provenance ?& ARRAY['schema_version', 'origin_project', 'sources'] AND
        jsonb_typeof(provenance->'schema_version') = 'number' AND
        provenance->'schema_version' = '1'::jsonb AND
        jsonb_typeof(provenance->'origin_project') = 'object' AND
        jsonb_typeof(provenance->'sources') = 'array'
    ),
    provenance_sha256 char(64) NOT NULL CHECK (provenance_sha256 ~ '^[0-9a-f]{64}$'),
    PRIMARY KEY (target_project_id, artifact_sha256),
    FOREIGN KEY (target_project_id, target_revision)
        REFERENCES mf_app.project_revisions(project_id, revision) ON DELETE RESTRICT
);
