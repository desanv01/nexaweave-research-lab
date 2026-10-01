# Trusted Temporal host for prepared native execution

`TemporalPreparedHost` is an explicit opt-in backend service wrapper. It connects
the accepted `TemporalNativeHost` activity to a fresh `NativePreparedHost`
supervisor for each admitted exact `NativeRunRequest`. Importing the wrapper
module alone does not import Temporal, PostgreSQL or the native engine, and no
ordinary app bootstrap or HTTP route constructs it.

The trusted caller supplies an authenticated principal, a guarded PostgreSQL
connection factory, a Temporal client and queue, and a mapping from immutable
validated native requests to preconfigured `NativeOwnedSessionFactory` values.
The wrapper copies this mapping into an immutable host-owned table. It checks
all request scalar fields and the runtime digest against each factory without
reading the prepared directory. The local path, graph ID and picklable model
factory remain trusted host configuration; no request wire field selects a
module, provider, file or executable. The request artifact digest binds to the
exact table key and is checked against prepared bytes by the accepted factory
inside the native child.

Temporal start admission and native coordinator dispatch are separate checks,
both off by default. `allow_dispatch()` must return exactly `True` to schedule
the Temporal workflow. `native_dispatch_allowed(request)` must return exactly
`True` before PostgreSQL can claim native launch. Exceptions deny dispatch.
Within the activity supervisor factory, `NativeRunStore.register(request)`
checks principal and exact project revision *before* the binding selector runs.
Thus a foreign or missing revision cannot disclose local artifact existence.
The accepted native child later validates the manifest, runtime digest and
prepared state before announcing readiness and again before execution.

`worker()` returns the accepted Temporal worker. `start`, `status`, `result`,
`local_status` and `retry_cleanup` delegate to `TemporalNativeHost`. `cancel`
first uses the accepted host's request/reference validation and the immutable
binding table, then registers the exact request and persists PostgreSQL
`cancel_requested` through bounded off-thread store calls before delegating
Temporal/local cancellation. This closes the gap when the activity is still
selecting a binding and has no supervisor to cancel yet. A missing local entry
may still cause Temporal to receive cancellation, while durable native intent
prevents a later native claim. These APIs retain the accepted workflow identity,
status/error and qualified receipt contracts. Temporal status describes workflow scheduling,
not authoritative native state. Read `NativeRunStore` for the durable native
row. The accepted activity registry retains one supervisor per run for worker
lifetime, spends admission once, and retains failed cleanup for
`retry_cleanup`. The caller must drain retained owner cleanup before worker
process exit. A fresh wrapper can fetch an exact completed Temporal result
without constructing a native supervisor or touching prepared files.

The wrapper passes only trusted `native_options` to `NativePreparedHost`.
Supported bounds are lease, poll, call budget, handshake, observation, grace,
join and go timeout. The prepared host requires a finite call budget covering
the configured worst driver call and the accepted supervisor requires lease
margin for polling and database work. The combined cold-native fixture uses a
240-second lease, 10-second handshake, 3-second join, 20-second call budget
and a 180-second result deadline. Native engine loading and round execution
occur after child ready/go. No database transaction is held across binding,
artifact reads, child work or caller waits.

The durable `starting` claim is one-shot. A lost acknowledgement, expired
lease, uncertain previous attempt or worker restart cannot authorize another
fresh native execution or adopt an old process. A Temporal cancellation before
launch persists `cancel_requested` and leaves attempt/receipt absent; it is
intent, not a qualified `cancelled` receipt. Even when a child
is terminated, cancellation does not roll back native effects or release a
budget. This boundary does not implement HTTP/frontend flows, checkpointing,
report IPC, paid provider calls or end-user completion of all capabilities.

The opt-in combined suite requires disposable PostgreSQL at
`127.0.0.1:15432` (`mirofish_operations_test`, `mirofish_fixture`), a disposable
Temporal server at `127.0.0.1:17233`, locked native/Temporal dependencies,
offline fake models and the accepted child external-socket guard. The suite
asserts durable starting and running attachment before native first `go`, both
platforms' SQLite effects, exact stored receipt, frozen prepared inputs, the
persistent fresh-start marker, duplicate/replay/fresh-result behavior,
prelaunch cancellation and an uncertain attempt fence.
