# Temporal admission for retained source ingestion

This slice supplies a real Temporal workflow, async activity and trusted host
adapter for the accepted `BudgetedIngestion` service. It starts only when a
caller supplies a connected Temporal client, an explicit task queue, a
revision-bound ontology resolver, the accepted bridge/service and a trusted
dispatch policy. The dispatch policy defaults closed. No client is created
from environment variables and no paid provider is constructed here.

The workflow payload contains only schema version, principal and display graph
identifiers, source/operation/account/ontology UUIDs, a positive integer USD
microunit ceiling and the exact bridge request fingerprint. The deterministic
workflow ID combines account, operation and a semantic digest. Source text,
ontology payload, credentials, DSNs, paths and callbacks stay outside Temporal
history. The activity revalidates the payload, checks its configured principal,
resolves the specified ontology revision, re-plans retained owned source data
and compares the fingerprint before calling `BudgetedIngestion`.
The SDK-facing workflow and activity arguments use `Any` so Temporal's payload
converter does not reject malformed values before the explicit bounded DTO
validator can return a terminal fixed error. Their receipts use JSON-compatible
dictionary hints and are validated again before workflow completion.
The execution package initializer resolves budget exports lazily so a Temporal
workflow sandbox import loads only the pure wire contract and SDK. Direct SDK
starts with invalid input and malformed activity receipts end with fixed,
nonretryable failure codes.

One activity is scheduled with finite start, schedule and heartbeat timeouts,
and `maximum_attempts=1`. Workflow retries and automatic recovery dispatch are
not configured. The activity emits fixed heartbeat fields and fixed outward
failure codes. Cancellation is propagated into the budgeted service; it is
never proof of no external effect or permission to release a reservation.
Temporal records workflow state; PostgreSQL remains authoritative for budget
reservations and uncertainty after worker loss or a lost Temporal response.

The host exposes start, status, result and cancel for a request plus pinned
workflow/run identity. Start rejects duplicate workflow IDs. The result is a
validated identifier-only receipt. Status describes Temporal state and a
workflow-known stage; it does not report provider progress or a fabricated
percentage. An explicit policy callback is required for dispatch, and a
real deployment must enforce model, token and retry ceilings aligned with each
submitted monetary ceiling before enabling paid providers.

`tools/run_temporal_execution_tests.py` runs pure tests in a scrubbed child.
`--integration` additionally requires an approved disposable Temporal server
at `127.0.0.1:17233` and the existing guarded PostgreSQL fixture at port
15432. It does not download a test server or read application credentials.
The integration source uses fake providers. This slice does not supervise
native OASIS processes, guarantee provider billing, implement a reconciliation
route, or prove crash checkpoint recovery.

Main qualification on2026-09-29 used TemporalCLI1.9.1/Server1.32.0 and
Python SDK1.33.0 with real disposable PostgreSQL:17tests passed including two
real-service cases. The dedicated temporal-execution CI job uses a checksum-pinned
Linux CLI archive and always cleans up its disposable services. Local test
databases/logs are retained and stopped between qualification runs. A graceful
shutdown timeout is not a hard kill guarantee for an uncooperative provider.

The new optional execution lock adds temporalio1.33.0 (MIT), nexus-rpc1.4.0 (MIT),
protobuf7.36.2 (3-Clause BSD metadata), and types-protobuf7.35.1.20260906
(Apache-2.0). These are recorded from installed distribution metadata by Main;
this is not a complete transitive SBOM or a replacement for inherited notices.
Existing dependency pins were preserved. Temporal is not required by default
knowledge/budget consumers; execution package exports stay lazy for sandbox safety.
