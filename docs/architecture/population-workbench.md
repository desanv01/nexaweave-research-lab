# Graph-to-population workbench — U07b authored candidate

The existing Vue `/research` route now offers explicit population preview,
native export preparation and a separate visible Save link through its private
workbench client. This is
an implementation candidate awaiting Main qualification. It connects typed
graph actors to the accepted inherited rule-based population producer; it does
not configure a scenario, launch OASIS, enrich personas with a model, or establish
full U07/all44 acceptance.

## Requests and ownership

`PopulationWorkbench.vue` receives callback methods, connection/busy/reset state,
locale and admitted graph type labels. Credentials, origin and graph authority
remain inside `createWorkbenchClient`. The panel has no mount, locale, control
change, polling or retry fetch. Preview and Prepare make its only population
requests, explicit POSTs:

- `/api/graph/population/<bound_graph>/preview`
- `/api/graph/population/<bound_graph>/export`

Type selection excludes `Entity` and `Node`, shows at most 50 unique canonical
custom labels, and preserves the submitted order. Leaving all types unchecked
omits `types`, retaining the backend's all-custom-types behavior. Agent limit is
an integer 1–100; seed is an integer 0–4294967295. The form accepts canonical whole
number text and rejects fractions, exponent notation, whitespace and coercion.
The pure client helper also rejects null/string/nonfinite numeric options.

The inherited loopback origin, bearer, authenticated graph read and single active
request generation remain authoritative. Fetch retains `redirect:error`,
`credentials:omit`, `cache:no-store` and `referrerPolicy:no-referrer`. Requests
are bounded at 16 KiB; preview bodies are bounded at 2 MiB plus 1024 bytes of
envelope overhead, and export bodies at 2 MiB. The existing finite 125-second
deadline covers fetch and body reading. Readers are cancelled/released and
requests aborted after completion, replacement or cancellation. Malformed 401
bodies clear the private connection before parsing; population 403 does likewise.
An old host's non-JSON 404/501 returns a fixed optional-unavailable code.

The client keeps a private detached preview and its exact submitted options.
Export requires that same admitted preview/options. Non-population requests,
explicit cancellation and disconnect invalidate this admission. Parent callback
dispatch preserves it only for a population operation's internal cancellation;
it does not make a new connection or share tokens through props. Parent operation
generations and the population reset counter clear the panel during replacement
research/source/ingestion/comparison work. Component generations reject stale
successes, errors and downloads after option edits/reset/disconnect/unmount.

## Preview admission and interpretation

`populationWorkbench.js` admits the complete DTO emitted by
`KnowledgePopulation.build`: bound graph ID, fixed generator/enrichment flags,
false model/simulation/snapshot flags, real calendar date, selected/eligible
counts and ordered synthetic field declarations. It checks every complete
profile field, nonnegative integer counters, nullable demographic strings/age,
topic strings, sequential identities, unique usernames/source UUIDs and sorted
source UUID order. The profile date and first custom producer type must agree
with grounding. Selected count must equal `min(eligible_count, submitted_limit)`.

Grounding must have exactly the selected source UUID membership and full source
entity UUID, labels, summary, JSON attributes, episode/evidence references and
incident fact fields. Each fact's direction must join the inspected source node;
self-loops are outgoing only. Repeated incident edges across selected endpoints
must agree in their complete fields and have their opposite endpoint entry.
Unknown fields, missing joins, divergent references/text, malformed UUIDs,
nonfinite attributes and invalid Unicode fail closed. Original reader reference
arrays can contain repeated IDs, so admission preserves that producer behavior.
The admitted result is deeply detached before display or later comparisons.

The inspector renders complete source summaries, attributes, fact text and
references with Vue text interpolation and wrapping preformatted text. It does
not activate URLs, render HTML/Markdown or clip source text. Synthetic bio,
persona and demographic/activity traits have a separate generated-assumptions
section. Profile rows page by ten. They show the actual producer type and its
individual/organization/other rule branch classification; these labels are not
identity or empirical truth judgments. An atomic graph snapshot is explicitly
unsupported.

## Native export admission and bytes

Successful Twitter output requires `text/csv; charset=utf-8`; Reddit requires
`application/json; charset=utf-8`. Redirected, malformed, oversized and invalid
UTF8 responses fail closed. The browser-visible content type is validated;
`Content-Disposition` is never used as a filename or path. Flask's existing
export route supplies `X-Content-Type-Options:nosniff`; no new backend/CORS change
is made to expose that header to cross-origin JavaScript.

CSV admission uses a bounded parser with quoted fields, doubled quotes, commas,
embedded CR/LF and complete row boundaries. It requires the exact five-column
header `user_id,name,username,user_char,description`, index identities and
ordered profile rows. The inherited mapping concatenates different bio/persona
values with one space and replaces CR and LF with spaces in `user_char` and
`description`, while preserving names/usernames exactly. Reddit uses `username`,
the producer's required fields and only the optional fields its truthy rules
include. Age zero, null strings and empty topic lists remain absent as in
`to_reddit_format`. Reordered, extra, missing, foreign or divergent rows fail.

Only after comparison does the panel create a Blob from the exact captured
server byte chunks. UTF8 decoding is fatal and retains a BOM for validation;
output is never reserialized or spreadsheet-escaped. Fixed local filenames are
`oasis-twitter-profiles.csv` and `oasis-reddit-profiles.json`.

Prepare Twitter CSV / Prepare Reddit JSON are explicit network actions. A
successful preparation retains one bounded Blob URL and renders one actual,
visible native file link: Save Twitter CSV / Save Reddit JSON. Preparation never
clicks a link, initiates a download or schedules an early revocation timer.
Prepared feedback explains that the analyst must choose Save. The link remains
available while the analyst decides, with native link/keyboard semantics, a
44px target and visible focus. A disabled/busy Save handler prevents default
activation. The immediate explicit Save click alone allows the browser's native
download action; it makes no request to the backend.

The component owns the one link, object URL and cleanup timer. Replacement
preparation revokes the preceding URL before fetching again. Option edits,
reset, disconnect and unmount revoke any prepared URL and remove the link.
After Save activation, the existing1000ms timer revokes that owned URL; it is not
started while waiting for Save. Feedback says a download was requested only
after Save activation and does not claim the browser saved a file. MIME/byte
shape, fatal UTF8 decoding and row admission failures normalize to fixed
`invalid_reply`; callback transport/auth/cancellation errors retain their codes.

Export re-reads the graph. Matching native profile rows cannot prove unchanged
source grounding or an atomic snapshot, and the UI states this limit. Native
loader compatibility and profile quality remain unqualified until Main checks
actual output against the real producer/loader.

## Language and accessibility

Dedicated EN/ZH/MS copy covers Preview/Prepare/Save controls, prepared/requested
feedback, errors, selection invalidation, empty/loading/denial, assumptions and
export limitations. Server evidence stays
unchanged. Native buttons, checkboxes, fieldsets and form labels use the existing
system font and blue/slate/amber tokens. The scoped layout wraps long text, uses
44px action targets and two columns only from 768px. No hosted design, remote
assets, animation, dependency or backend change is introduced.

Opening an inspector focuses its named aside; Escape/Close returns focus to the
originating button when still mounted, or the feedback node otherwise. Invalid
inputs have associated inline errors and focus the feedback node on submission.
Population feedback is forwarded to the existing route status live region; the
new panel adds no competing live region. Rendered layout, contrast, keyboard
activation, zoom and responsive behavior remain Main's browser responsibilities.

## Source fixtures and required qualification

The new client fixtures exercise routing/auth/private transport, strict options,
complete DTO/grounding mutations, detached hostile text, Unicode/quoted CSV,
Reddit optional presence, byte preservation, MIME/UTF8/size rejection, malformed
401, deadlines and stale replies. The new render fixtures compile the actual
SFCs with the existing compiler-sfc/jsdom toolchain and exercise parent wiring,
explicit requests, labels/locales, pagination, focus, option edits, reset,
prepared resources and delayed outcomes. Correction2 fixtures assert that
Prepare creates no download, Save creates no new backend request, there is one
fixed-name visible link, the URL survives waiting beyond1000ms before Save, and
the1000ms cleanup applies only after Save. They also cover disabled activation,
locale changes without refetching and full resource cleanup. The latest source
changes are UNVERIFIED; prior Main results apply only to their tested revision.

Main owns execution of these fixtures, affected existing regressions, the locked
Vue build, real Flask/PG/Neo browser journey, both downloaded-file byte/row proof
and any narrow native loader check. Main also owns the added
`PopulationWorkbench.vue` import-map entry required in the existing
`frontend/tests/workbench-render.test.mjs` and
`frontend/tests/source-library-render.test.mjs` harnesses. Those files are outside
this worker's nine-path scope and were not edited by the worker.
