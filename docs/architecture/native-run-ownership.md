# Native run ownership: first durable control seam

This increment records who may start a prepared native simulation. A trusted host
computes artifact and runtime SHA256 values from authorized inputs, pins a stored
project revision, supplies a principal and a driver, and explicitly enables
dispatch. The SHA256 values are declarations from that host; the store does not
read or verify filesystem artifacts. NativeSimulationSession's exclusive
`.native_prepared_start_claim` remains required inside the eventual production
driver. This seam does not replace it.

## Durable sequence

1. `migrate_native_runs(connection)` explicitly installs or checks its own
   `mf_native_execution` catalog. It requires existing `mf_app` project storage.
   It neither applies the budget migration nor runs automatically on import.
2. `NativeRunStore.register(request)` validates a bounded immutable request and
   calls `ProjectStore.get(principal, project_id, revision)` at that exact
   revision. PostgreSQL uniquely reserves both the run UUID and the
   `(project_id, simulation_id)` pair. Repeating the same request is idempotent;
   a changed request cannot reset the simulation.
3. `claim_start` atomically changes `declared` to `starting`, saves one attempt
   UUID, binds an owner-instance UUID, and sets a DB-clock lease. Only that
   successful call grants launch permission. Before invoking the trusted driver,
   the coordinator reconciles DB-clock expiry and confirms starting state,
   attempt, owner, and cancellation intent. The driver runs after the transaction
   commits. A repeated claim cannot launch again. This check and the external
   process call are not atomic: lease expiry or cancellation may still race in
   the intervening interval, and such a launch is fenced as uncertain.
4. The driver returns a bounded child identity. `attach` binds its opaque
   instance UUID, process ID, and process fingerprint to the same attempt and
   owner, then changes `starting` to `running`. A process ID alone is never an
   ownership token. This layer does not enumerate, adopt, or kill saved PIDs.
5. Heartbeat and settlement check state, owner, attempt and DB-clock expiry.
   Terminal evidence must match the run, attempt, child and request fingerprint.
   Matching terminal receipt persistence is idempotent. A fresh coordinator can
   read a recorded terminal receipt without invoking the driver.

PostgreSQL constraints and a trigger preserve identity and legal state changes.
Short transactions set finite statement, lock and idle-in-transaction timeouts;
driver calls occur outside transactions. The migration uses an independent SQL
checksum and catalog shape checksum, including its trigger and function. It
does not change `mf_execution` or its one-version budget catalog guard.

## Cancellation and uncertainty

Cancellation intent is a separate durable boolean. The current owner may ask
its injected driver to cancel an attached child, but only an exact terminal
observation produces a `cancelled` receipt. A request while starting is retained
for the active owner; the coordinator services it after child attachment. On
subsequent observations, the active owner routes durable intent to the driver's
cancel operation, including when another coordinator recorded the intent. A
driver that still observes `running` extends only the same attempt's lease and
retains the intent. Intent is not termination proof. If a launch result, child
observation, or DB acknowledgement is ambiguous, the coordinator attempts to
mark the row `uncertain`. Even if that acknowledgement fails, the existing
`starting`/`running` lease remains a fence; explicit reconciliation marks an
expired lease `uncertain`. There is no takeover, relaunch, reset or automatic
adoption. An unknown or absent process observation is not a successful result.

The synchronous coordinator keeps blocking database and driver calls out of an
async event loop. A future async host must offload it; it must not call the
synchronous methods directly from its event loop. Dispatch defaults closed even
when a driver is injected. The test-only driver may launch a child it owns; no
production executable selection or user-controlled process launch is supplied.

This control seam does not yet provide long-running heartbeat scheduling, OS or
OASIS supervision, checkpoint recovery, safe automatic retries, exact process
identity verification after host death, native output validation, or model
usage/invoice accounting. It does not release or settle budget reservations.
Whole U05 remains open. Main qualifies the SQL migration, concurrency, process
test, and operational assumptions before accepting this increment.
