# Native seed fidelity

U07f corrects the inherited Twitter dictionary overwrite and the initial action
logs that previously preceded native execution. It also makes Reddit seeds run
in their original global order. It preserves the inherited autonomous engine,
action catalogs, recommendation presets, profiles, source identity, interviews,
IPC, lifecycle and existing execution limits.

## Admission

`nexaweave_execution.native_seed_contracts.validate_native_seed_plan` is a
standard-library-only boundary. Owned factory validation calls it before the
model factory; native session preflight and all inherited runners use the same
contract. Admission snapshots the entire batch before seed effects. A nonempty
batch requires contiguous nonboolean integer actor IDs from `agent_configs`,
an explicit `poster_agent_id` for each seed, and explicit scalar UTF-8 string
content. Missing actor/content fields, unknown actors, booleans, malformed
containers and surrogate text are refused. Exact Unicode, empty string content,
repeated content, repeated actors and interleaved source order are retained.
Absent/empty seed lists preserve minimal owned binding compatibility. No new
seed-count or content truncation is imposed: the existing prepared 2 MiB input,
500-actor, 24-round, lifetime, call and shared budget bounds remain in force.

## Native execution adapter

`backend/scripts/native_seed_posts.py` does not load the SDK, construct a model,
connect to SQLite, create an output file, replace a native agent or patch the
SDK. It uses the caller's already reset environment, actual actor graph and
actual owned platform connection. It verifies the environment's platform value
against the trusted runner's platform and resolves every seed actor before
refresh or seed execution. The inherited scripts add the repository's shared
knowledge source directory at the end of the import path. An available installed
execution package takes precedence; direct legacy environments without that
package retain the trusted repository source fallback and the same pure contract
without SDK imports.

For nonempty admitted seeds, the adapter awaits exactly one actual
`platform.update_rec_table()` before the seeds. It then awaits each actual
`agent.perform_action_by_data("create_post", content=...)` in source order.
This is the installed public agent seam: it executes the real channel/platform
action and updates native agent memory. A dictionary keyed by actor and
`env.step` cannot establish this fidelity: dictionaries overwrite or regroup
seeds, and installed `step` concurrently gathers tasks and returns no responses.

Each successful seed requires all of:

- An exact native `{success: true, post_id: positive nonboolean integer}` response.
- A newly inserted post ID beyond the pre-action maximum, with the exact actor
  and content on the owned platform connection, outside an open transaction.
- Exactly one new native `create_post` trace after the pre-action cursor, with
  matching actor, exact content and actual nonboolean post ID.

Only after those facts does the optional action logger append a round-zero
`CREATE_POST`, `success=true`, exact content and the actual response as its
result. Count and cursor do not depend on whether a logger exists. Every seed
retains its own physical record, including identical repeated content.

After all seeds succeed, Twitter's actual `sandbox_clock.time_step` advances
once, reproducing the inherited manual-stage advancement. Seed Twitter posts
are created at clock 0 and the first autonomous stage at clock 1. Reddit's
clock is unchanged. No refresh after seeds, extra RNG draw, model call, clock
reset or per-seed step is introduced. Empty seed plans neither refresh nor
advance the clock.

The returned trace cursor is the current native cursor after initialization.
Both parallel platform runners use it even without optional execution controls.
The owned session's `initial_trace_cursor` likewise always excludes initial
follow/seed traces from later autonomous logging. Existing physical outputs and
accepted receipts are never rewritten or deduplicated.

## Failure and ownership

An explicit native `success=false` receives one round-zero failure record with
the fixed code `native_seed_response_failed`, then aborts initialization. Native
error text is not copied into that diagnostic. A malformed response, inconsistent
post/trace, thrown exception or unknown outcome raises a fixed uncertainty code
without fabricating an action success/failure record. Cancellation propagates.
There is no next seed and no retry after either kind of failure. Previously
committed effects and actual partial output remain. A failed initialization
does not reach `simulation_end`; owned session error handling preserves the
one-shot claim and closes adopted environments. Standalone runner `finally`
cleanup remains active on seed failure.

## Authored qualification, unverified

Offline boundary fixtures use synchronous pytest bodies with explicit
`asyncio.run`. They cover whole-batch refusal, actual actor resolution refusal,
interleaving, repetition, scalar transport, snapshot fidelity, platform mismatch,
declared failure, partial committed failure, malformed/extra response, boolean
ID, inconsistent trace, uncommitted effects, thrown/cancelled actions, safe
diagnostics, no retries and logger-independent count/cursor. A fresh isolated
interpreter fixture blocks SDK/backend imports for pure contract admission.

Dedicated engine fixtures import SDK and existing offline model/preparation
helpers lazily. They author real OASIS/CAMEL both-platform DB/trace/log order and
ID assertions, seed clock and next real step assertions, zero manual model
calls/RNG preservation, input preservation, source mapping, real autonomous
rounds, no replay with absent/empty controls, one-shot claim and cleanup checks.
Actual SQLite triggers refuse a second seed insertion through the real SDK;
they verify retained first seed, declared failure, no later seed, no model
calls, no completed event and owned cleanup. Those fixtures do not replace SDK
responses or create fabricated output databases.

Main owns execution, actual PG/Temporal/inherited-generation/browser provenance,
dedicated accounting, source review, exact hosted gates and acceptance. This
document records authored behavior, not a qualification or report/full44 claim.
