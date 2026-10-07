CREATE TABLE mf_knowledge.scope_bindings (
    principal text COLLATE "C" NOT NULL CHECK (
        char_length(principal) BETWEEN 1 AND 128
        AND principal COLLATE "C" ~ '^[ -~]+$'
        AND principal COLLATE "C" ~ '[^ ]'
    ),
    display_graph_id text COLLATE "C" NOT NULL CHECK (
        char_length(display_graph_id) BETWEEN 1 AND 128
        AND display_graph_id COLLATE "C" ~ '^[A-Za-z0-9_-]+$'
    ),
    group_id text NOT NULL REFERENCES mf_knowledge.scopes(group_id),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (principal, display_graph_id),
    UNIQUE (group_id)
);
