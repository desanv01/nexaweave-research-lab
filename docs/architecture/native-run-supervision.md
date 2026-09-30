# Native run owner supervision

`NativeRunSupervisor` binds one validated request, one `NativeRunCoordinator`
owner token, and one trusted driver for its lifetime. Its single non-daemon owner
thread calls coordinator `start` and `observe` and later calls that same driver's
`close`. A caller thread never invokes a driver method. The coordinator remains
the durable authority for the atomic one-time launch claim, exact attempt and
child identity, DB-clock lease, receipts, and permanent uncertainty fence.

## API and sequence

- `start(wait_seconds)` spends the supervisor's one local start permission and
  starts the owner thread. It waits only for the first local outcome. If the
  finite wait expires, it raises `native_supervisor_wait_timeout`; the owner
  thread may still be launching or running. A second `start` never launches.
- `wait(wait_seconds)` waits for a terminal or uncertain local outcome. It does
  not prove child cleanup. Both bounded waits return the owner thread's recorded
  snapshot without adding a database call after the wait.
- `status()` performs an explicit ownership-scoped durable read/reconciliation
  through the accepted store and reports a fixed-shape `SupervisorStatus` with
  local phase, durable run state when available, cancellation intent, receipt,
  cleanup pending, thread liveness, and a safe error code.
- `request_cancel()` records local intent and, when registration exists, also
  writes durable cancellation intent through the store. The owner thread will
  consume local intent before claiming a launch or on its next observation.
  Cancellation intent alone never creates a terminal receipt.
- `close(wait_seconds)` signals stop and a cleanup retry, then joins only for
  the finite caller wait. `False` means the owner thread or its cleanup still
  exists; the same supervisor retains the owned driver handle and a later
  `close` can retry. It never signals a saved process ID. A close before any
  `start` creates a cleanup-only owner and permanently forbids start.

Thread construction/start failure is a fixed `native_supervisor_thread_unavailable`
outcome. The local start permission stays spent. A gate prevents even a thread
that began just before `start()` raised from entering the native launch path.
If no owner thread began, a later bounded `close` may create a cleanup-only
owner; it never retries the run. A failed cleanup-only thread creation makes
`close` return `False` and leaves the same retry path available.

The owner registers the bounded request before any launch. If stop was already
requested, it persists cancellation intent and leaves the run declared with no
child or receipt. Otherwise the accepted coordinator performs the unique
`declared → starting` claim and launches the injected driver outside the DB
transaction. The owner loop calls `observe` at a validated polling interval;
the coordinator heartbeats the same attempt using DB time, services persisted
cancellation intent, and accepts only an exact terminal receipt. Another
coordinator may persist intent without invoking this driver's `cancel` method.

## Timing and failure limits

Poll, call-budget, wait and close values reject bool, NaN, infinity, zero and
unbounded magnitudes. This seam requires a coordinator lease of at least 20
seconds, polls at most once per lease fifth, and reserves a conservative margin
for two bounded DB operations plus the driver's declared longest call. The
`call_budget_seconds` argument is a trusted host promise; Python cannot preempt
an arbitrary blocking driver. Store connection setup, driver launch/observe,
cleanup and thread scheduling must fit that budget in the actual deployment.
If they do not, DB-clock expiry fences the run uncertain and this layer cannot
claim a safe automatic restart. Main must qualify the chosen bounds in the
target runtime.

On unknown/absent observation, expired lease, ambiguous launch/acknowledgement,
or DB failure, the owner attempts a conservative uncertainty fence and cleans
only its local driver handle. If the DB is unavailable, the finite preexisting
lease remains the fence until explicit reconciliation. `close` never fabricates
success, failure or cancellation from a process exit or cleanup result. A
cleanup failure remains visible as `cleanup_pending`; the owner thread stays
alive to retry only after a later `close`. The flag is also set while the
driver's `close` call is in flight and clears only after that call proves
cleanup. A terminal DB receipt remains readable
even while cleanup is pending. A fresh supervisor can recover that exact
terminal receipt without launching another child.

This is a trusted host seam. It does not install signal handlers, atexit hooks,
an event loop, a public endpoint, a Temporal retry policy, native checkpoint
recovery, PID adoption, provider construction, invoice accounting or budget
release. Its opt-in PostgreSQL tests use an offline spawned stdlib gate session;
the combined full native/PG runtime remains a later Main qualification.
