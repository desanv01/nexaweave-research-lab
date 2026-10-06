# U07e native observations UI — correction2 stable source-only handoff

Status: **UNVERIFIED / source and fixture authoring only**. Accepted base
`409120adb062fc97d6ebcb8a88682920e4911357`; existing u01 checkout, task branch
`task/u07e-native-observations-workbench`. No checks, imports, parsers, tests,
builds, installs, Git, network, browser, providers, runtime, skill scripts or
other-chat messages were executed. Files were read statically and edited with
apply_patch. No root/Main integration source was authored.

Exclusive assigned paths, all authored:

1. `frontend/src/api/nativeObservations.js`
2. `frontend/src/components/workbench/NativeObservations.vue`
3. `frontend/src/i18n/nativeObservations.js`
4. `frontend/src/api/workbench.js`
5. `frontend/src/views/ResearchWorkbench.vue`
6. `frontend/src/components/workbench/NativeLaunch.vue`
7. `frontend/src/i18n/nativeLaunch.js`
8. `frontend/tests/native-observations-client.test.mjs`
9. `frontend/tests/native-observations-render.test.mjs`
10. `frontend/tests/native-launch-render.test.mjs`
11. `docs/architecture/native-observations-workbench.md`
12. `coordination/handoffs/U07e-native-observations-workbench.md`

The completed-launch Inspect control revalidates the existing status validator
and emits a detached launch/READY context, without fetching. The new panel/client
implement explicit bounded POST pages, exact completed receipt/READY/source
binding, canonical native manifest hashes, original UTF-8 record hashes,
duplicate/finite/object/depth/Unicode admission, stable pagination/counts and
inert expandable literal records. Unknown records remain visible. EN/ZH/MS
labels and errors distinguish full-log recorded counts, bounded pages, stale
retained pages, malformed evidence and refusal.

Observations have a private channel controller/epoch through fetch, stream and
digest validation. Local clear cannot abort any other channel. Parent source,
preparation/native reset, graph/disconnect/unmount and selected-context changes
clear observations; late replies cannot restore them. Auth/Origin denial clears
parent private state. No implicit load, dispatch, polling, retry, storage or
model call was added. Limits are unchanged: 4KiB request / 256KiB data+128 bytes,
20/page / 10,000 records / 8MiB action log / 8192 UTF8 bytes / depth8 / 64MiB file.
Correction1 aligns with Main's clarified U07E contract: every numeric token,
including integer tokens, must decode to a finite IEEE754 double. Unsafe finite
numeric convenience values become null without changing raw evidence or
string/boolean counts. Raw literal CR/LF is refused after actual terminators
have been removed; escaped JSON backslash-r/n remains valid.
The selected log size now includes the canonical physical-line minimum:
`3*total_records-1 + sum(page raw UTF8 bytes-2)` for nonempty logs; zero records
require zero bytes. Existing count correspondence and original limits remain.

Authored coverage: independent ASCII/UTF8 crypto producers; float/exponent/
negative-zero/large-integer raw evidence; strict payload and rehashed receipt/
manifest/source/pagination/count/duplicate/depth/Unicode negatives; bounded
stream errors; selected platform; disabled model authorization reads; protected
POST policies; observations/source channel independence; clear/native-clear/
disconnect late fetch fencing; actual mounted SFC explicit controls, languages,
focus, unknown/inert records, pending controls, platform change and late reset/
selection/graph/disconnect/unmount; completed-only Inspect selection.
Correction1 adds independently hashed finite 1e308 integer acceptance, 1e309
integer overflow refusal, bare internal/trailing CR and LF refusal, escaped
newline acceptance, and full/partial/offset-at-total physical log lower-bound
fixtures with correctly rebound receipt/manifest/record hashes. The earlier
contradictory arbitrary-integer/CR positive expectations were corrected to the
explicit contract; all other prior assertion bodies and limits are retained.

Correction2 changes only the assigned client fixture and this handoff. Main
reported the original affected Node result as 198pass/1fail/zero skips, preserved
at `u07e-node-initial.log`; this worker did not execute or independently qualify
that run. Static source confirmed the explicit receipt-evidence corruption was
overwritten by the loop's unconditional manifest digest assignment, restoring
that case to the valid baseline. The fixture now retains every negative mutation
and invalid_reply rejection, gives every case a stable descriptive name, and
asserts each mutation actually changes its baseline. Only intentional manifest
mutations are rehashed. Those cases bind selected and returned receipt digests
to the same independently hashed malformed manifest, ensuring structural
correspondence is tested rather than an incidental selected-receipt mismatch.
The explicit corrupted receipt digest is never overwritten. Source-revision and
noncompleted-state rejection messages are also named. Static review found no
other no-op mutation in this group and no product defect requiring alteration;
production validators, prior limits and all other fixture bodies are unchanged.
Correction2 remains **UNVERIFIED source-only**, pending Main execution/review.

Main integration dependency: update separately owned parent render fixture
import maps to include `NativeObservations.vue`, preserving old bodies/assertions.
Main owns all execution, appropriate fixture corrections, browser 320/768/1440
and keyboard verification, real protected-store/evidence qualification, exact
hosted review/Git/acceptance. Static CSS hooks are not responsive/browser proof.
No native browser/report/livefeed/model-quality/full44 acceptance is claimed.

Installed UI/UX Pro Max was read and applied statically, using Main's verified
Focus States/Focus Not Obscured and Vue SFC/PascalCase records. Existing research
tokens and explicit-load contract were preserved. No skill scripts or hosted
design tools were invoked.

Stable handoff: tools/edits stop after completion; exclusive ownership retained
until Main requests a bounded correction or release. Future human pause controls
immediately. No field, path, API or bound expansion was needed.
