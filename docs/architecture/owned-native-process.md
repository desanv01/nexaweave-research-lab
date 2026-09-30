# Owned spawned native process (U03o candidate)

`NativeProcessDriver` is a trusted synchronous driver for the accepted
`NativeRunCoordinator`. One instance owns at most one `multiprocessing` spawn
`Process` and private pipe. A saved PID never grants adoption or signalling.
`launch` waits briefly for a bounded ready message; the child cannot start
native work until the owner's first `observe` sends one `go` command. A cancel
before go exits without native execution. The coordinator must attach the
identity durably before that first observation.

All pipe messages are ASCII JSON, at most 4096 bytes, with exact keys and
duplicate-key rejection. The parent uses `recv_bytes(maxlength)` and verifies
run, attempt, instance, request fingerprint and the actual created PID. A
single daemon reader per owned pipe contains a partially received frame; caller
methods wait on its bounded event queue and never block in `recv_bytes`.
Termination plus pipe close must unblock the reader before handle release;
failed cleanup retains the owned handle for a later bounded `close()` retry.
A fully qualified terminal observation is cached for repeat observe/cancel.
A duplicate or conflicting frame, including one queued after child exit,
invalidates terminal evidence.
A terminal receipt requires an exact terminal message and exit code zero from
that same owned `Process`; an exit alone, malformed/extra message, lost ready,
or missing terminal remains unknown. A failed native session sends only a
fixed digest, never its exception. Cancellation waits briefly, then terminates
and if needed kills only the stored process handle; a cancelled receipt proves
termination, not rollback of native/provider/database effects. `close()` is
idempotent and cleans only that handle. A parent crash cannot guarantee child
termination, and an unobserved child waits for go only for a bounded period.

The child scrubs inherited provider-key prefixes, proxy settings and Python
startup hooks before calling the trusted session factory, then sets
`PYTHON_DOTENV_DISABLED=1` so the inherited backend `app.config` import does
not refill secrets from a local `.env`. With the exact trusted test flag
`MIROFISH_NATIVE_TEST_OFFLINE=1`, the child also sets the Hugging Face and
Transformers offline flags and rejects non-loopback socket connections, DNS
lookups and datagrams. That guard is test-only; it is not a production
network policy or paid-call authorization. This does not create
a paid-client authorization path. A future live host needs its own secret and
spend-cap policy. `PATH`, operating-system directories, temporary directories
and the installed runtime remain available. The host must make both
`mirofish_execution` and backend `app` importable in the spawned interpreter;
the driver has no module-path or executable selection from request data. The
pickled binding class lives in a standard-library-only execution module so
unpickling does not import the backend app before environment scrubbing.
Trusted model-factory classes must likewise have import-safe modules.

`NativeOwnedSessionFactory` binds the resolved prepared directory, graph and
simulation IDs, principal/project/revision, platform list, seed/round limits,
and a host-selected non-secret runtime SHA256. The request's runtime hash is
checked against that trusted binding; the hash does not authenticate settings
by itself. Before ready, and again before session construction, the factory
checks the prepared input manifest and READY/platform identity. It rejects
symlinks/reparse points on the file and ancestor path, path escape and
oversized inputs. It opens files in binary mode on Windows, compares file
identity, size and timestamps before/after reading, and hashes exact bytes.
The fixed input
order is `state.json`, `simulation_config.json`, `source_grounding.json`, then
`twitter_profiles.csv` if enabled, then `reddit_profiles.json` if enabled.
Each input is limited to 2 MiB. The canonical manifest bytes are UTF-8/ASCII
JSON of `{"files":[{"name":...,"sha256":...,"size":...},...],"schema_version":1}`
with keys sorted, compact separators, ASCII escaping and that fixed file
order. `artifact_sha256` is SHA256 of those exact bytes. The completion
evidence uses the same encoding over each enabled platform's SQLite DB then
`<platform>/actions.jsonl`, with a 64 MiB bound per output. No artifact path,
model response or record content travels in control messages.

The manifest validates a trusted local directory at specific reads; it does
not provide an immutable filesystem snapshot. A separate writer with access
could change a prepared file after validation and before
`NativeSimulationSession` reopens it. The host must isolate the prepared
directory from concurrent writers during the attempt.

The child creates the actual `NativeSimulationSession`, runs bounded native
rounds, and closes it in `finally`. The session's exclusive persistent
fresh-start marker remains in the directory after completion or uncertain
failure; the driver never removes it. The caller must poll `observe` within
the durable lease. This increment has no native interview command channel,
checkpoint continuation, Temporal scheduler or live provider accounting.
