# U12b stable implementation handoff — UNVERIFIED

## Correction6 — stable complete UNVERIFIED

Main acknowledged the documented information class 3 correction (its earlier 8
was BasicAndIoAccounting). Main also supplied targeted unchanged short-deadline
comparison evidence under its identical owned sanitized harness: accepted parser
18 passes in 8.13s; correction5 candidate 18 passes in 9.89s. No deadline/assertion
changes; this is Main's targeted evidence, not full gate or correction6 acceptance.

Main reported correction5 Windows gate 86 passes/four failures/one exact POSIX
skip in 135.02s: all directory/new parser/knowledge/pure cleanup cases passed;
two unchanged 0.7-second parser protocol cases timed out without waiver. Both
real helper cases still observed WaitForSingleObject(observer,0)==258 despite
active-zero/closed owner. Main's one-child diagnostic confirmed exact-grandchild
membership/five active processes before cleanup, then a later kernel signal in
a submillisecond interval. Those immediate real kernel assertions are unchanged.

Added bounded complete exact-job process-ID capture before termination, using
documented JobObjectBasicProcessIdList information class 3 (not BasicAndIoAccounting
class 8), eight-byte/two-DWORD header and pointer-sized ULONG_PTR entries. Buffer
growth/65,536-entry cap and all capture/open/membership/wait steps consume the
same original three-second owner cleanup deadline. OpenProcess requests only
SYNCHRONIZE|PROCESS_QUERY_LIMITED_INFORMATION, noninheritable. Every obtained
handle is pinned and exact-job IsProcessInJob must succeed before waiting;
foreign/reused/unproved handles are closed and cleanup fails without waiting or
individually killing them. Only INVALID_PARAMETER plus confirmed absence in a
fresh exact-job list permits skipping an unopenable exited PID.

TerminateJobObject still runs on capture failure and exited root. Verified captured
handles must kernel-signal within the remaining deadline, then active-zero is
required. Every obtained process handle closes on partial failure before job
closure. Typed OpenProcess/IsProcessInJob/WaitForSingleObject signatures and typed
query result-length pointer added. No independent grace deadline, operation retry,
caller deadline/import/environment/payload changes or unowned PID kill introduced.

Extended pure API doubles and authored sources for bounded complete list growth,
changing sizes/entry overflow, foreign or membership/open failure without unverified
waits, partial handle closure, exact-job vanished PID requery, capture expiry with
unconditional termination, async kernel wait expiry despite active-zero and
pointer/header layout. Every real/inherited assertion remains unchanged. Docs
honestly retain the capture-to-termination race: later-created/unopenable exited
objects may not have pinned kernel proof; fixed caller directory absence and
root/pipe/thread closure still require qualification. No universal sandbox/crash
or durable-orphan proof is claimed.

Correction6 edits four allowed paths: helper, backend helper test file, architecture
doc and this handoff. No imports/tests/WinAPI/runtime/network/Git/status/hash/check
or audit execution performed; only scoped source reads/writes. Supplied primary
references were retained as guidance without fetching them. Main owns actual
execution/CI/acceptance and separate unchanged deadline diagnosis. Stable complete
UNVERIFIED correction6; worker idle retaining exclusive u03 ownership. A future
human pause supersedes all work authorization.

## Correction5 — stable complete UNVERIFIED

Main's follow-up requested explicit parser mapping coverage. The correction5
parser outer handler already catches `(OSError, OwnedProcessError)` and retains
KeyboardInterrupt/SystemExit. Added six authored parser cases (two cleanup error
types times ordinary call/two interrupt types): inject the error after actual
bound-directory removal, assert fixed `parser_failed` with no private detail for
ordinary calls, preserve the original interrupt, assert absence and owned cleanup.
No inherited deadline/assertion changed or execution performed. Correction5 now
also edits the allowed parser test file, seven allowed paths total; original
knowledge test file remains unchanged. Stable complete UNVERIFIED; worker idle.

Main reported the complete untraced correction4 Windows gate: 61 passes, ten
failures and one exact POSIX skip in 142.88s; no acceptance. Three knowledge
lifecycle cases reached active-zero/closed owner/jobNone and closed root pipes/
handle but retained their private directory. Parser removal failed too, including
the original 5,000-byte case. Four other failures remain original 0.7-second
operation deadlines; those deadlines/cases/assertions were not changed.

Implemented shared bounded Windows private-directory cleanup. Each caller binds
its exact newly created TemporaryDirectory, and `owner.start` captures the fixed
owned CWD. Cleanup validates object identity, original name and matching captured
CWD before invoking only that object's cleanup; foreign targets are never removed.
The first owner.stop records the original three-second cleanup deadline, reused
by any partial-startup/finally stop and directory retry. Initial directory cleanup
is attempted. Only WinError 5/32 retries after successful closed-owner/active-zero
proof or handled no-process startup, within the remaining same original budget.
Expiry/nontransient/unproved cleanup maps through existing fixed caller errors;
Windows success additionally requires directory absence. No independent grace,
operation/POST/parser retry, arbitrary recursive deletion or POSIX retry added.
Interrupt, unknown-write-outcome and lock-release handling remain in the callers.

Authored meaningful pure sources in the existing backend test file for transient
success, exhausted same-budget retries, nontransient/no-unproved-owner rejection,
foreign object/name/CWD rejection without deletion, handled no-process startup,
repeated-stop deadline preservation and unchanged POSIX single cleanup attempt.
All earlier actual/inherited assertions remain unchanged. Docs explicitly explain
that active-zero/root/pipe closure does not qualify filesystem release, actual
directory absence is required, and Main observed a cleanup race without proving
every NTFS lock's cause or a durable orphan. No OS sandbox/termination guarantee
is claimed from the retry or supplied Microsoft TerminateProcess reference.

Correction5 edits six allowed paths: helper, parser client, knowledge client,
backend helper test source, architecture doc and this handoff. No other source,
tests/deadlines, runner/CI/dependency/API/SQL/native/registry paths edited. No
tests/imports/WinAPI/execution/runtime/network/Git/status/hash/check/audit commands
performed. Main owns actual Windows/API/PG/inherited/exact-CI qualification and
separate operation-deadline diagnosis. Stable complete UNVERIFIED correction5;
worker now idle retaining exclusive u03 ownership. Future human pause supersedes.

## Correction4 — stable complete UNVERIFIED

Main's actual startup diagnosis reported three owned venv children: enumeration
aborted at 6,689 entries before the owned thread with the initial new 0.5-second
guard; subsequent 6,886/6,951-entry snapshots found exactly one thread and
succeeded (launch 0.438/0.453s, total 2.219/1.375s, treeEmpty/closed true).
Per Main's scoped correction, changed only the new internal startup enumeration
guard to 3.0 seconds. The 65,536-entry maximum, exact owned PID/one-thread
selection, GetProcessIdOfThread/ResumeThread-count checks and fail-closed handle
cleanup remain unchanged. No inherited caller operation/extraction/test deadline,
three-second cleanup budget, skip or retry behavior changed. Main separately
diagnoses the unchanged operation-deadline failures.

Updated architecture documentation to explain the measured reason and new bound.
Only the helper, architecture doc and this handoff were edited for correction4.
No execution/imports/tests/checks performed. Stable complete UNVERIFIED correction4;
worker now idle retaining exclusive u03 ownership. Main-supplied observations do
not qualify this correction. Future human pause takes precedence.

## Correction3 — stable complete UNVERIFIED

Main reported an actual Windows run with 48 passes, 23 failures and one expected
POSIX skip in 100.33s; no acceptance. The first pure failure exposed a derived
join timeout of `0.010000000009313226` exceeding the supplied `0.01` budget.
Changed only the helper's cleanup timeout derivation: all root wait/thread join
remaining timeouts now clamp to `[0, supplied timeout]`, with the existing POSIX
grace wait still additionally capped at 0.5 seconds. The exact `0.01` assertion,
all cases, resume logic and operation deadlines remain unchanged. Main separately
owns diagnosis of the observed Toolhelp enumeration/descendant/parser failures.

Only `backend/app/utils/owned_process.py` and this handoff were edited for
correction3. No tests/imports/execution/checks performed. Stable UNVERIFIED
correction3; worker idle retaining exclusive u03 ownership. Future human pause
takes precedence. Main's reported run is evidence supplied by Main, not a worker
execution or passing qualification of this correction.

## Correction2 — stable complete UNVERIFIED

Added the missing stdlib `import json` and `from uuid import uuid4` to
`backend/tests/test_owned_process.py` for the relocated knowledge request/tests.
All cases/assertions remain as authored. Only that allowed test file and this
handoff were edited. No execution, imports or checks performed. Worker is now
idle retaining exclusive u03 ownership; future human pause takes precedence.

## Correction1 — stable complete UNVERIFIED

Main requested a source-only relocation because the original knowledge test
module collects provider-dependent `commands`, while the native Windows gate
installs backend dependencies. Moved only the seven new real Windows knowledge
cases (five lifecycle modes and two suspended startup seams) from
`services/knowledge/tests/test_stdio_transport.py` to already allowed
`backend/tests/test_owned_process.py`. The backend file now has a trusted fixed
standalone transport fixture and stdlib JSON/UUID/request/child/echo helpers.
Moved assertions, actual Popen/Job Object/grandchild behavior, conservative
uncertainty, twice-repeated lock reuse and cleanup checks are retained. The real
helper grandchild synchronize-only observer test remains intact in that file.

`test_standalone_transport_loads_shared_stdlib_helper` stays in the original
knowledge file. Every inherited knowledge case and deadline remains unchanged.
This relocation does not claim collection/execution passed and does not waive
the original knowledge suite. Updated the architecture doc and this handoff.
Correction1 edits only those four allowed paths; production helper/parser/client
and parser test bytes were not edited during this correction. Main owns runner
platform selection and nonempty actual helper/knowledge/parser group checks.

No execution, imports, tests/checks, WinAPI, status/hash/Git, runtime, network,
browser or audit commands were run; only text source reads and scoped source
writes. Ownership remains exclusive u03. Stable complete UNVERIFIED correction1
handoff; worker now idle retaining ownership pending a bounded Main correction.
A later human pause takes precedence. Earlier handoff below is retained, with
its test-location descriptions updated to the current correction placement.

Worker has finished the bounded source changes in assigned released u03 checkout
`task/u12b-windows-owned-process`, assigned accepted base
`528a1681247e3a72c2cdbf67efbd1dfadfeedfa9`. These are assignment facts from Main,
not worker-verified Git/status/hash facts. U14 is reference-only after explicit
release. This worker is now idle; no overlapping writer or delegation was created.

Exactly eight worker paths authored/edited:

1. `backend/app/utils/owned_process.py` — new stdlib Job Object owner, ctypes
   signatures/fixed DWORD and pointer-size layouts, noninheritable kill-on-close
   private handle, suspended/no-window launch, assign actual Popen handle,
   bounded Toolhelp exact-PID/one-thread confirmation, verified ResumeThread,
   partial-startup cleanup, active-zero finite cleanup and finite root/I/O joins.
2. `backend/app/utils/parser_process.py` — existing fixed launch now uses owner;
   bounded shared cleanup, daemon I/O threads, fixed cleanup/temp failure mapping.
3. `backend/app/services/knowledge_transport.py` — trusted __file__-relative
   source-load of the same helper without app/Flask/providers; existing fixed
   Popen hook, framing, environment and validation retained; shared owner cleanup,
   conservative unknown outcome after resume attempt and lock release retained.
4. `backend/tests/test_owned_process.py` — new pure startup/active-tree/thread and
   kernel API failure/layout doubles; actual Windows venv/descendant handle,
   root-exit/active-zero/CWD/stdout/pipe/thread/process-handle cleanup source;
   POSIX unchanged root behavior smoke source; correction1 standalone stdlib
   transport fixture/helpers and seven real Windows knowledge lifecycle/startup
   cases, including repeated lock reuse and uncertainty assertions.
5. `backend/tests/test_parser_process.py` — Windows real descendant success,
   malformed/overflow/timeout/root-exit and twice-repeated cleanup regressions;
   real suspended assign/resume failure with no executed marker/no retry.
6. `services/knowledge/tests/test_stdio_transport.py` — shared standalone helper
   origin assertion retained; correction1 moved only new Windows cases to the
   backend helper test file; all inherited cases/deadline sources unchanged.
7. `docs/architecture/owned-process-lifetime.md` — lifecycle contract and limits,
   tests to execute, supplied Microsoft source links and no sandbox/crashproof claim.
8. `coordination/handoffs/U12b-owned-process.md` — this stable handoff.

Operation errors remain when ownership cleanup succeeds. Windows cleanup always
terminates/query-waits the job even when root poll shows exit; no descendant PID
discovery/kill, breakaway, retry or unowned fallback is introduced. Root/thread
cleanup shares a separate three-second deadline; the original extraction/call
deadline and all payload/byte/authority/environment/-I boundaries remain unchanged.
Preassignment failure is known no effect; after assignment/resume attempt,
knowledge failure is conservatively unknown. Pure failures assert no fallback;
real startup fixtures assert child code never runs for injected pre-resume errors.

Buffered streams close after their owned threads join to avoid an unbounded
buffered-lock wait. A live thread after the finite cleanup budget leaves its
buffered stream unclosed and reports failure; this is deliberately not a claimed
successful cleanup. Real Windows cases require an actual venv interpreter and
explicitly skip on POSIX. A synchronize-only observer handle in the helper test
pins the exact grandchild identity until kernel-exit assertion; no PID/name kill.
Atomic marker publication avoids reading a partially written descendant PID.

No tests/imports/WinAPI/runtime/DB/network/browser/Git/status/hash/audit or CI
commands were executed. Only text source reads and scoped file writes were used.
No runner/workflow/dependency/native/API/SQL/plan/registry edits, paid calls,
automation changes, external messages or delegation were performed. Supplied
Microsoft links were retained as source guidance, not fetched by this worker.
No test pass, kernel behavior, acceptance, durable orphan or PR75 qualification
is claimed. Fast ON remains requested and unavailable to verify/set.

Main must review actual bytes and execute new real Windows/pure/POSIX cases,
existing parser/knowledge/HTTP/PG gates (including unchanged ten-second bootstrap
fixtures), add the Windows cases to the existing native-engine-windows job while
preserving all eight gates, and qualify exact candidate/PR/push/post logs before
integration/acceptance. Native simulation is outside scope. POSIX retains root
termination behavior without a descendant lifetime guarantee. Abrupt host failure
before assignment is not covered. PR75 remains unaccepted until Main qualification.

Stable complete UNVERIFIED handoff; worker idle pending a bounded Main correction
or later human instructions. A later human pause takes precedence.
