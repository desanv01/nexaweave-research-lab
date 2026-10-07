-- Explicit operator rollback after owners are stopped; leaves accepted schemas intact.
DROP TABLE mf_report.plans;
DROP TABLE mf_report.schema_migrations;
DROP SCHEMA mf_report;
