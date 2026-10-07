# Budget admission for retained source ingestion

This opt-in host service reserves integer USD microunits before it permits the
accepted `SourceIngestionBridge` to dispatch. It is an authorization ledger. It
does not measure provider usage or impose a provider invoice limit.

## Host sequence

1. Explicitly migrate the existing project and knowledge stores, then call
   `nexaweave_execution.migrate(connection)` on the migration-owner connection.
   This task does not run any production migration.
2. A trusted host creates a single immutable account per owned project with
   `BudgetLedger(factory).create_account(principal, project_id, account_id,
   cap_microusd)`. Identity is canonical UUID; the cap is a positive integer no
   larger than signed 64-bit maximum. The same exact request is idempotent.
3. The host supplies an explicit conservative `ceiling_microusd` for **each**
   operation and calls `BudgetedIngestion(bridge, budget_ledger,
   coordinator).ingest(..., account_id=..., ceiling_microusd=...)`.
   No account or no ceiling denies admission. The host must also enforce a
   corresponding maximum model, tokens and retries before enabling paid calls.
4. Query `status(principal, account_id)` after restart. `remaining_microusd`
   equals cap minus accounted ceilings minus all unsettled reservations. The
   `reserved_microusd` number includes uncertain reservations;
   `uncertain_microusd` is its visible subset. `actual_usage_microusd` is always
   unknown in this slice.

The service first plans from the owned project, binding and retained source. It
reserves against that plan fingerprint and wins `started` under the account row
lock. The bridge re-plans and the host-only callback accepts only the exact
original plan. No database lock covers network or model work. A validated
completion receipt moves the full reserved ceiling to `accounted`. A duplicate
settled call returns the saved validated receipt without redispatch. A changed
fingerprint or ceiling conflicts; a started or uncertain operation is never
retried automatically.

The `reserved → released` transition is available only via
`release_undispatched` with the original attempt ID. Its caller must prove the
attempt was never dispatched. Once started, timeout, failure, cancellation,
restart and lease age cannot release funds. A failed uncertainty write still
leaves `started` money unavailable. Error rows contain fixed codes and receipt
identifiers, not credentials or source content. No expiry, reset, override,
reconciliation route, paid client, API switch or production deploy is included.

The included disposable PostgreSQL and fake-provider tests are source only
until the main orchestration task runs them. Packaging includes this package's
SQL migration as package data; dependency/lock metadata is unchanged.
