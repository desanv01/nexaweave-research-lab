CREATE TABLE mf_app.source_binaries (
    project_id uuid NOT NULL,
    source_revision uuid NOT NULL,
    contract_version integer NOT NULL CHECK (contract_version = 1),
    media_type varchar(32) NOT NULL CHECK (media_type = 'application/pdf'),
    byte_length integer NOT NULL CHECK (byte_length BETWEEN 1 AND 2097152),
    sha256 char(64) NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    original_bytes bytea NOT NULL,
    PRIMARY KEY (project_id, source_revision),
    FOREIGN KEY (project_id, source_revision)
        REFERENCES mf_app.source_revisions(project_id, source_revision) ON DELETE RESTRICT,
    CHECK (octet_length(original_bytes) = byte_length)
);
