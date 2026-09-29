CREATE TABLE mf_app.source_revisions (
    source_revision uuid PRIMARY KEY,
    project_id uuid NOT NULL REFERENCES mf_app.projects(project_id) ON DELETE RESTRICT,
    name varchar(256) NOT NULL CHECK (char_length(name) BETWEEN 1 AND 256),
    retained_text text NOT NULL CHECK (char_length(retained_text) > 0),
    text_sha256 char(64) NOT NULL CHECK (text_sha256 ~ '^[0-9a-f]{64}$'),
    byte_length integer NOT NULL CHECK (byte_length BETWEEN 1 AND 1048576),
    codepoint_length integer NOT NULL CHECK (codepoint_length > 0),
    recorded_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (project_id, source_revision)
);
CREATE INDEX source_revisions_project_list ON mf_app.source_revisions
    (project_id, recorded_at DESC, source_revision);

CREATE TABLE mf_app.passage_evidence (
    evidence_id uuid PRIMARY KEY,
    project_id uuid NOT NULL,
    source_revision uuid NOT NULL,
    ordinal integer NOT NULL CHECK (ordinal BETWEEN 0 AND 99),
    start_offset integer NOT NULL CHECK (start_offset >= 0),
    end_offset integer NOT NULL CHECK (end_offset > start_offset),
    page integer CHECK (page > 0),
    excerpt text NOT NULL CHECK (char_length(excerpt) > 0),
    excerpt_sha256 char(64) NOT NULL CHECK (excerpt_sha256 ~ '^[0-9a-f]{64}$'),
    FOREIGN KEY (project_id, source_revision)
        REFERENCES mf_app.source_revisions(project_id, source_revision) ON DELETE RESTRICT,
    UNIQUE (project_id, source_revision, ordinal)
);
