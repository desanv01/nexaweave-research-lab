# Private parser and knowledge process lifetime

U12b candidate; **unverified** until Main executes the actual Windows gate,
inherited client/API/PG checks and exact CI qualification. This change addresses
the observed private parser CWD removal error after an expected oversized-response
error. Main did not observe a persistent survivor afterward; that observation
does not establish a durable orphan, sandbox escape or crash-proof supervision.

`backend/app/utils/owned_process.py` is the shared stdlib implementation. The
parser imports its sibling normally. The standalone knowledge client loads that
same file from the trusted module `__file__` tree with importlib, without loading
Flask, app startup or providers. Request/environment values cannot choose this
helper's origin. Existing callers still choose their fixed worker/bootstrap,
private environment, isolated `-I` interpreter, byte caps and extraction/call
deadline. Existing Popen capture hooks pass through the shared owner.

On Windows the owner creates a private unnamed, noninheritable Job Object with
`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`. Popen creates only the caller's child with
`CREATE_SUSPENDED | CREATE_NO_WINDOW`, then its actual owned process handle is
assigned to the job. Python closes CreateProcess's primary-thread handle, so a
Toolhelp snapshot identifies exactly one thread for this still-suspended PID.
Enumeration has a three-second/65,536-entry bound. Only that candidate is opened;
`GetProcessIdOfThread` must confirm the owned PID and `ResumeThread` must report
the expected suspension count of one. Snapshot and thread handles close in
finally blocks. Missing/ambiguous threads, failed job assignment, nested-job
restrictions or failed resume close down the owned child; there is no breakaway
flag, unowned fallback, taskkill, shell command or unrelated process manipulation.

Main's startup diagnosis observed one enumeration abort at 6,689 entries before
finding the owned thread under the initial new 0.5-second guard. Two subsequent
owned venv children enumerated 6,886/6,951 entries, found exactly one owned thread
and completed ownership/cleanup. Correction4 therefore sets only this new
internal enumeration guard to three seconds for the measured host workload.
This does not change inherited operation/extraction/test deadlines, the separate
three-second cleanup budget, entry cap, identity checks, retry policy or
fail-closed behavior. These observations are Main-supplied diagnosis; the worker
has not executed or qualified the correction.

Cleanup captures a bounded complete process-ID snapshot from the exact private
job handle, then always terminates the job, including after the root has exited
or capture fails. The documented `JobObjectBasicProcessIdList` uses information
class 3, two fixed DWORD counts (an eight-byte header), and pointer-sized ULONG_PTR
entries. Buffer growth and enumeration are bounded by 65,536 entries and the
remaining original three-second cleanup deadline. Only listed PIDs are opened,
noninheritable and with SYNCHRONIZE/PROCESS_QUERY_LIMITED_INFORMATION rights;
no termination rights are requested. Handles pin process identity and
`IsProcessInJob(handle, exact job)` must confirm membership before any wait.
Foreign/reused/unproved handles are closed without waiting or individually
killing their process, and fail cleanup closed. Only INVALID_PARAMETER when
opening an exited PID plus absence in a fresh exact-job list permits skipping
that PID; access failures or continued presence are not waived.

After job termination, verified captured handles must become kernel-signaled
within the remaining same cleanup deadline. Millisecond waits round down;
any submillisecond remainder uses that same deadline. Basic accounting must
then report active-zero on the still-open job handle. Every obtained process
handle closes on partial failure as well, before the job handle closes. It then
reaps the root, joins only owned I/O threads with remaining finite time, closes
their pipes and closes Popen's Windows process handle. Joining comes before
buffered pipe close because a live reader/writer can hold its buffered lock.
A thread still alive at the deadline prevents buffered close and reports a
fixed cleanup failure instead of blocking indefinitely or claiming closure.
Parser threads are daemon threads as an additional bound on a failed join;
normal successful cleanup still requires their termination.

Correction5 adds private-directory removal to the same cleanup budget. The first
`owner.stop` captures the original three-second cleanup deadline; partial-startup
and caller-finally stops cannot reset it. Each caller binds its exact created
TemporaryDirectory object, and the owner records the fixed Popen CWD. The helper
rejects foreign objects, changed names or a mismatching owned CWD before any
cleanup of that object. It invokes only this bound object's cleanup method and
does not accept arbitrary recursive deletion paths.

The initial directory cleanup is attempted. On Windows only WinError 5/32 may
retry, only after successful owner cleanup and verified active-zero job accounting
(or handled startup with no process), and only within the remaining original
cleanup budget. Each bounded sleep consumes that same remainder; expiry,
nontransient errors or unproved ownership do not receive a new grace period.
Successful Windows cleanup must also leave the directory absent. POSIX directory
cleanup retains one attempt without this Windows retry behavior. Caller error,
interrupt, unknown-write-outcome and lock-release mapping remain as above;
only successful removal permits retention of the original operation error.

Main's correction4 gate reported 61 passes, ten failures and one expected POSIX
skip in 142.88s. Three knowledge lifecycle cases had active-zero/closed owner,
root pipes and handle, yet retained a private directory; parser cleanup also
failed, including the original 5,000-byte response case. Active-zero accounting
and closed root/pipes alone therefore do not qualify filesystem release: actual
directory absence must be checked. This is an observed cleanup race, not proof
of the reason for every NTFS lock, a durable orphan or a guarantee from retries.
Microsoft documents [TerminateProcess](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-terminateprocess)
as asynchronous with pending I/O affecting full termination; that supplied source
guidance does not establish a guarantee for this retry mechanism. Main still
owns diagnosis of the four original 0.7-second operation-deadline failures; those
deadlines are unchanged.

Correction6 addresses Main's next actual gate: 86 passes, four failures and one
exact POSIX skip in 135.02s. All directory/new parser/knowledge/pure cleanup cases
passed, but both real helper cases still saw immediate grandchild kernel-wait
timeout despite active-zero/closed owner. Main's one-child diagnosis confirmed
exact grandchild membership and five active processes before cleanup; a subsequent
bounded wait signaled within a reported submillisecond interval. This explains
why captured verified kernel handles now participate in cleanup; the unchanged
immediate kernel assertions still require actual qualification. Two original
0.7-second parser protocol deadline failures remain separate, without waiver.

Capture is a bounded snapshot, not an atomic freeze of the running job. A process
may exit before its handle opens, or a descendant may be created after capture
and before termination; such uncaptured objects have no pinned kernel signal
proof. Job termination/active-zero still covers the job's associated processes
under Windows nested-job semantics, while the fixed caller's root/pipe/thread
closure and directory absence remain required. Do not infer universal absence of
all filesystem/kernel races, a durable-orphan diagnosis, sandboxing or crash
recovery from snapshot capture, accounting or bounded waits.

The POSIX path retains root-only terminate/wait/kill behavior and zero creation
flags. It does not introduce process-group/tree supervision. Its waits/joins
are now finite; no new POSIX descendant-cleanup guarantee is asserted.

Protocol, timeout and operation errors survive successful cleanup. Parser
cleanup errors map to `parser_failed`; knowledge cleanup errors map to the
existing `transport_failure`. Failed job creation/assignment before code can run
has known no effect. Once assignment succeeds and resume is attempted, failures
are conservatively `outcome_unknown` for knowledge writes. Existing cancellation
propagation and lock release remain; failed operations are never retried.

Authored regressions include pure ownership/API failure doubles; real Windows
venv root/grandchild CWD/stdout retention with a held synchronize-only descendant
handle, active-zero accounting, closed streams/thread/process handles and CWD
removal; root exit before descendant; parser and framed knowledge success,
malformed/overflow/timeout, repeated reuse and handled suspended startup failure;
and POSIX root smoke. Real Windows cases explicitly skip on POSIX and require
an actual venv interpreter. Doubles do not establish real kernel behavior. Main
owns runner/CI changes and actual execution. Existing bootstrap ten-second test
limits are unchanged and must be qualified without a waiver.

Additional pure directory-cleanup sources cover transient success after owner
cleanup, exhausted shared budget without reset, nontransient errors, unproved or
failed owner, foreign object/name/CWD rejection without removal, handled no-process
startup, repeated-stop deadline preservation and POSIX single-attempt behavior.

Further pure process-list/kernel sources cover complete bounded buffer growth,
entry overflow, foreign/membership/open failure without unverified waits,
partial-handle closure before job closure, confirmed vanished exited PID handling,
capture expiry with unconditional job termination and asynchronous kernel-wait
expiry despite active-zero. Every real/inherited assertion remains intact.

Correction1 places the seven new real Windows knowledge cases (five lifecycle
modes and two startup seams) in `backend/tests/test_owned_process.py`, alongside
the real helper kernel/descendant-observer cases. A fixed trusted-file transport
fixture and stdlib JSON/UUID/child/echo helpers let this backend gate collect
without importing `mirofish_knowledge`, Graphiti SDK or providers. Every moved
lifecycle/startup assertion and its deadlines remain intact. The original
`services/knowledge/tests/test_stdio_transport.py` retains its standalone shared
helper-origin assertion and every inherited knowledge case/deadline. Main owns
the Windows runner's platform selection and checks that real helper, knowledge
and parser groups are nonempty; relocation does not waive inherited gates.

This is process lifetime control. It provides no network/native-library sandbox,
native-simulation supervision, abrupt-host-failure recovery or guarantee against
a host crash between CreateProcess and assignment. Handles are scoped only to
the created child/job. Permissions and process escape behavior remain bounded
by Windows Job Object semantics and the parent's enclosing job restrictions.

Source guidance supplied in the packet (not fetched or executed by this worker):

- [Microsoft Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)
- [AssignProcessToJobObject](https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-assignprocesstojobobject)
- [Thread handles and identifiers](https://learn.microsoft.com/en-us/windows/win32/procthread/thread-handles-and-identifiers)
- [Suspended-process Job assignment discussion](https://devblogs.microsoft.com/oldnewthing/20131209-00/?p=2433)
- [Job process-ID list layout](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_basic_process_id_list)
- [IsProcessInJob](https://learn.microsoft.com/en-us/windows/win32/api/jobapi/nf-jobapi-isprocessinjob)
- [Nested jobs](https://learn.microsoft.com/en-us/windows/win32/procthread/nested-jobs)
