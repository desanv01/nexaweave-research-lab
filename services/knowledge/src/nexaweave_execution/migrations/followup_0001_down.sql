-- Explicit operator rollback only after every owned turn is terminal.
DROP TABLE mf_followup.turns;
DROP TABLE mf_followup.histories;
DROP TABLE mf_followup.schema_migrations;
DROP SCHEMA mf_followup;
