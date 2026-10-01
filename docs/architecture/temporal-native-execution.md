# Temporal scheduling for durable native ownership

This seam schedules one native run through Temporal while PostgreSQL remains
the authority for the one-time launch claim, owner lease, child identity,
cancellation intent, uncertainty fence and terminal receipt. The workflow sees
only the bounded `NativeRunRequest` wire and an exact `NativeRunReceipt`. Its ID
contains the canonical run UUID and full semantic request fingerprint. It has
one activity with finite timeouts and `maximum_attempts=1`; workflow replay
performs no database, process, artifact or provider work.

`TemporalNativeHost` requires an already connected trusted client, explicit
queue, bound principal and injected supervisor factory. Dispatch is OFF unless
both the host/activity gate and the accepted coordinator's independent gate
allow it. The factory maps a validated request to an already trusted
`NativeRunSupervisor`; it must never derive a path, process command or provider
from wire data. A backend caller can supply its accepted prepared-host
supervisor. The knowledge package does not import backend bootstrap or native
SDKs in the workflow.

## Retained activity ownership

The activity keeps a bounded process-lifetime registry keyed by native run UUID
and exact fingerprint. It reserves an entry before calling the factory, without
holding the registry lock over factory, database, driver or wait work. A failed
factory still spends that entry. An identical concurrent admission is busy; a
changed request is conflict. Entries are never evicted or replaced. A new
worker process has a new registry, but the accepted PostgreSQL authority still
recovers an exact terminal receipt without launching and fences any active or
uncertain prior attempt.

If a trusted factory returns a real supervisor bound to the wrong request or
principal, the registry retains that handle, rejects it with fixed
`invalid_supervisor`, and schedules cleanup-only on the same handle. It never
calls its first start or its cancellation method. The specific binding failure
remains visible even if cleanup itself must be retried. Arbitrary
non-supervisor objects are rejected without
method calls. Detached bounded-call exceptions are consumed into fixed local
diagnostics even after the awaiting activity has been cancelled.
An explicit local cancel on a mismatched handle also stays on the cleanup-only
path; local status does not query that handle as authority for this request.

Factory, supervisor start/wait/status/cancel/close calls run through
`asyncio.to_thread`. The entry retains in-flight tasks and the supervisor even
when Temporal cancels the activity or a bounded caller wait expires. The
activity's first start timeout never calls start again: it waits in finite
slices on the same supervisor. A fixed identifier-only heartbeat runs during
construction and observation. Heartbeat failure, activity cancellation and
activity lifetime timeout request cancellation and bounded close of the
retained owner. An unknown or absent native child is never a successful
receipt. A completed activity validates native run ID, fingerprint, outcome and
terminal status before returning the receipt. Cleanup continues separately;
a false/failed close keeps the entry and same supervisor for explicit retry.
Cleanup requested while factory construction is in flight records stop intent
in the reserved entry. A later returned supervisor is closed before first
start. Local status includes scheduled and in-flight cleanup;
`native_cleanup_pending` clears only after a later close proves cleanup.

The host exposes Temporal start/status/result/cancel plus local retained status
and cleanup retry. Temporal status describes scheduling only; it is not native
termination or budget accounting proof. With an owner retained in this host,
`cancel` writes local native intent and leaves the activity alive to observe an
exact cancelled receipt. Without a local owner, it requests Temporal
cancellation; this can leave native status uncertain until the owning worker
finishes cleanup or the PostgreSQL lease is reconciled. Prelaunch cancellation
may have no child and no cancelled receipt.

The guarded SDK cancellation scenario uses a fresh host with no local registry.
It cancels the exact Temporal workflow, then asks the owning host to complete
retained cleanup. A confirmed native `cancelled` receipt is distinct from an
uncertain row after local child cleanup and lease reconciliation. Temporal's
`canceled` status alone proves neither native outcome.
The qualification first waits for the owning activity's retained stop intent
from SDK cancellation, before any explicit owner cleanup retry, so cleanup by
the test cannot stand in for delivery of the SDK signal.

## Shutdown and limits

The worker caller must inspect retained local status and retry failed cleanup
before safe process exit. A pending non-daemon owner thread can prevent exit.
Do not discard the host or registry merely because a workflow was cancelled,
failed, timed out or completed. An in-flight factory or driver call is bounded
only by its trusted implementation; a thread does not preempt arbitrary
blocking code. A lost worker process cannot adopt a saved PID or safely
relaunch. Its PostgreSQL lease must expire to the conservative uncertain fence.

The guarded qualification source combines a disposable loopback Temporal
server, guarded PostgreSQL fixture and owned offline spawned child. Full
OASIS/CAMEL execution through Temporal, checkpoint recovery, provider billing,
budget release, public deployment and whole U05 remain separate gates.
