# Prepared native host under PostgreSQL ownership

`NativePreparedHost` is an explicit, trusted backend service object for one
prepared simulation request. It connects the accepted
`NativeOwnedSessionFactory`, spawned `NativeProcessDriver`, PostgreSQL
`NativeRunStore` and `NativeRunCoordinator`, and `NativeRunSupervisor`. Importing
the host module alone does not import the native engine or PostgreSQL driver.
The caller must configure the database and native dependencies before creating
the host. No Flask route or app bootstrap constructs one.

The trusted caller supplies a validated identifier-only `NativeRunRequest`, its
authenticated principal, a guarded PostgreSQL connection factory, and a
`NativeOwnedSessionFactory` built from local trusted preparation settings. The
prepared path, graph ID and picklable model factory live only in that factory;
wire data cannot name paths, Python modules, executables or credentials. The
host checks principal and immutable scalar fields without reading files. The
store's `register()` then authorizes the principal and exact project revision
before the driver may validate prepared files or spawn a child. The child
validates the artifact manifest, runtime digest and immutable prepared fields
before announcing readiness, and the owned session validates again before
execution. A digest by itself confers no authority.

Dispatch is off by default. A trusted `dispatch_allowed(request)` callback must
return exactly `True`; an exception denies dispatch. This callback is policy,
not a provider or path selector. The coordinator claims a durable, one-time
`starting` attempt before calling the driver. A persisted `running` attachment
precedes the first `go`. Terminal recovery for the exact request returns the
stored receipt through a fresh host without validating or rewriting the
prepared directory, starting a child or calling models. Lost acknowledgements,
expired leases and unknown child observations are fenced as uncertain; no
automatic retry, adoption or reset is available.

The trusted lifecycle is `start(wait_seconds=5)`, `status()`,
`wait(wait_seconds=30)`, `request_cancel()` and `close(wait_seconds=5)`. `start`
is permanently one-shot for that host even after a wait timeout. `status` and
`wait` return accepted `SupervisorStatus`, including durable state, receipt and
fixed error code. `wait` describes the local owner outcome, not child cleanup.
Call `close` and require `True` before releasing the host; a `False` result
retains its owner and child handle for a later bounded close attempt. Never
construct a replacement host to recover a live or uncertain run. Cancellation
records intent and can prove termination of the owned child; it does not undo
native side effects or release a cost budget.

The default lease is 60 seconds, poll interval 1 second, supervisor call budget
20 seconds, child handshake 10 seconds, join bound 2 seconds and go timeout 20
seconds. The host rejects nonfinite or nonpositive driver bounds and requires
its call budget to cover the largest configured call: handshake plus three
joins for failed launch cleanup, observation plus three joins, or cancellation
grace plus two joins. The accepted supervisor also requires room within the
lease for polling and DB transactions. These are configured upper bounds, not
preemption of arbitrary stalled code or an operating system scheduling
guarantee. The combined cold-native fixture uses a 240-second lease, 10-second
handshake, 3-second joins, 20-second call budget and a 180-second terminal
wait. The native cold import and execution occur after ready/go. PostgreSQL
transactions are bounded by the accepted store and are never held during native
work, file reads or waits. There is no production scheduling or process restart
guarantee beyond the permanent one-shot fence.

Construction does not launch a child or claim durable authority. If later
construction fails, it closes the fresh driver before rethrowing; no failed
host is handed to the caller. Once construction returns, start failures retain
the accepted supervisor and driver on that host for bounded cleanup through
`close()`.

The opt-in combined tests use only disposable PostgreSQL at
`127.0.0.1:15432`, database `mirofish_operations_test`, user
`mirofish_fixture`, with the existing project and separate native migration.
They run the real prepared OASIS/CAMEL session with offline fake models and
the accepted child socket guard. This seam makes no HTTP, Temporal,
checkpoint, report IPC or complete workflow claim. Future paid model dispatch
still requires local secrets and a concrete approved cap.
