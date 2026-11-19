CREATE TABLE mf_app.legacy_reports (
    project_id uuid NOT NULL REFERENCES mf_app.projects(project_id) ON DELETE RESTRICT,
    legacy_report_id varchar(128) COLLATE "C" NOT NULL
        CHECK (legacy_report_id ~ '^[A-Za-z0-9_-]{1,128}$'),
    meta_bytes bytea NOT NULL CHECK (octet_length(meta_bytes) BETWEEN 1 AND 65536),
    meta_sha256 char(64) NOT NULL CHECK (meta_sha256 ~ '^[0-9a-f]{64}$'),
    markdown_bytes bytea NOT NULL CHECK (octet_length(markdown_bytes) BETWEEN 1 AND 2097152),
    markdown_sha256 char(64) NOT NULL CHECK (markdown_sha256 ~ '^[0-9a-f]{64}$'),
    PRIMARY KEY (project_id, legacy_report_id)
);
