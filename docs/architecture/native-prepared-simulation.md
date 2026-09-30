# Prepared source graph to native OASIS execution

`NativeSimulationSession` is a trusted, in-process bridge from a READY neutral
preparation directory to the inherited parallel script's Twitter and Reddit
loops. It checks matching graph and simulation IDs, source grounding, enabled
platform artifacts, sequential actor IDs and names before loading OASIS. Its
caller supplies explicit CAMEL-compatible model objects and chooses a bounded
round count (1–24), seed and timeout. The bridge constructs no model client,
reads no `.env`, and does not invoke `SimulationRunner` or Zep graph memory.
Source graph memory updates are therefore unavailable in this path and are not
reported as persisted.

This bridge admits only a fresh run. Admission rejects either native database
or a prior `.native_prepared_start_claim`. Immediately before native work it
creates that claim exclusively and checks again for databases. The claim stays
after success, cancellation or uncertain failure, so a second session cannot
reset prior native databases through the inherited script's initialization.
It is a one-shot local admission rule, not checkpoint resume or a distributed
supervisor. Direct legacy CLI behavior is separate.

The platform scripts retain their native agent generators, `oasis.make`,
`env.reset`, `env.step`, SQLite traces, action loggers, and IPC handler interview
method. The neutral path passes a real native Twitter `Platform` configured for
OASIS's local random recommender, which avoids the default TWHIN weight load.
Reddit uses its native default recommender. The test model is injected only by
trusted test code; it is not a selectable production provider. Production CLI
calls keep the original native platform selections and model creation path.

The session owns an `asyncio.Runner` through rounds, interviews and close. Its
sync interview adapter is bound to exact graph, simulation, platform and actor
IDs when created. A single interview calls the inherited native handler method;
the report adapter calls `ParallelIPCHandler.handle_batch_interview` with an
explicit platform on every item. That handler performs one native `env.step`
per platform and writes an `IPCResponse` under `ipc_responses`. The adapter
requires completed status, the exact command ID and requested actor keys,
matching count, nonempty per-actor responses, and a newer SQLite interview
trace for every actor. It removes the response file after reading it, including
on failure. A missing, partial or stale response fails. It returns an
`InterviewResult` to the already accepted report capability. The in-process
adapter drives handler methods directly and does not run an IPC polling
background service. After close, interview calls fail.

The report capability validates its own arguments. When supplied, all custom
questions are retained in order in `InterviewResult.interview_questions` and
combined into one numbered native prompt of at most 4096 characters. Each
`AgentInterview.question` records that full prompt. The public single-agent
interview remains limited to 400 characters.

The installed `camel-oasis==0.2.5` Reddit loader directly indexes `persona`,
`mbti`, `gender`, `age`, and `country`. The accepted neutral preparation currently
requires `bio` and `persona` from model JSON but does not require those four
other fields, and its serializer omits absent optional values. This bridge
rejects a deficient prepared Reddit artifact before engine import; it does not
invent identity attributes. Main must qualify and, if needed, tighten the
separate preparation contract before calling this path complete end to end.
The connected offline fixture covers the supported subset where the accepted
profile generator explicitly receives all native-required fields. Arbitrary
accepted model output can still omit them and remains unsupported here.

Engine-only tests exercise the locked installed engine against disposable
SQLite and action logs with an explicit offline CAMEL model. They do not
establish paid-model quality, predictive validity, source graph ingestion,
public app routing, or a live native IPC subprocess contract.
