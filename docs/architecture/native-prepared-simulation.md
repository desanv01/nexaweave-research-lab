# Prepared source graph to native OASIS execution

`NativeSimulationSession` is a trusted, in-process bridge from a READY neutral
preparation directory to the inherited parallel script's Twitter and Reddit
loops. It checks matching graph and simulation IDs, source grounding, enabled
platform artifacts, sequential actor IDs and names before loading OASIS. Its
caller supplies explicit CAMEL-compatible model objects and chooses a bounded
round count (1–24), seed and timeout. The bridge constructs no model client,
does not call its CLI dotenv setup, and does not invoke `SimulationRunner` or
Zep graph memory. Importing the accepted `app` package still imports
`app.config`, whose inherited module initialization calls `load_dotenv`.
Source graph memory updates are therefore unavailable in this path and are not
reported as persisted.

This bridge admits only a fresh run. Admission rejects either native database
or a prior `.native_prepared_start_claim`. Immediately before native work it
creates that claim exclusively and checks again for databases. The claim stays
after success, cancellation or uncertain failure, so a second session cannot
reset prior native databases through the inherited script's initialization.
It is a one-shot local admission rule, not checkpoint resume or a distributed
supervisor. Direct legacy CLI behavior is separate.

The platform scripts retain `oasis.make`,
`env.reset`, `env.step`, SQLite traces, action loggers, and IPC handler interview
method. The neutral path keeps the native Twitter graph generator. Its Reddit
loader uses the real OASIS `AgentGraph`, `SocialAgent`, and `UserInfo` components
with a narrowly scoped `UserInfo` prompt renderer for optional fields. The
legacy CLI still calls the inherited Reddit graph generator unchanged. The
neutral path passes a real native Twitter `Platform` configured for
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

The installed `camel-oasis==0.2.5` Reddit loader at
`oasis/social_agent/agents_generator.py` indexes `persona`, `mbti`, `gender`,
`age`, and `country`; `oasis/social_platform/config/user.py` renders them as
known facts. The adapter in `native_reddit_profiles.py` follows that loader's
native component construction under the upstream Apache-2.0 attribution. It
requires exact actor identity plus nonblank username (at most 128 characters),
name (256), bio (2000), and persona (10000). Supplied gender (64), MBTI (32),
and country (128) must be nonblank text without control characters; supplied
age must be an integer from 1 to 120, excluding booleans. Bio and persona may
contain CRLF, LF, and tab characters. Length limits apply to the original
untrimmed strings; validation does not change their content. Missing or null
optional fields are absent from the
structured native profile and stated as not supplied in the prompt. Supplied
values remain unchanged. The adapter never rewrites preparation files or
invents demographic defaults. It constructs the graph under the configured
seed and restores the caller's RNG state even if construction fails, before
either native platform task starts. A constructed partial profile still uses native
tools, environment actions, SQLite records, and interview handlers.

Strict-neutral preparation now uses one honest formatter for realtime and final
Reddit JSON. When both `neutral_mode` and `strict_generation` are active,
supplied optional age/gender/MBTI/country values are retained and absent/null
values are omitted. An explicitly supplied zero age remains zero in the
artifact so the native bridge can reject it. Previously the final serializer
overwrote honest realtime JSON with fabricated age `30`, normalized gender,
MBTI `ISTJ`, and country `中国`. Legacy preparation still uses those inherited
defaults unchanged. The adapter never rewrites final preparation artifacts.

Engine-only tests exercise the locked installed engine against disposable
SQLite and action logs with an explicit offline CAMEL model. They do not
establish paid-model quality, predictive validity, source graph ingestion,
public app routing, or a live native IPC subprocess contract.
