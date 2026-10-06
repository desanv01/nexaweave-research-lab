# Receipt-bound native observations workbench

U07e source-only implementation, based on accepted PR90
`409120adb062fc97d6ebcb8a88682920e4911357`. Qualification is owned by Main.

`NativeLaunch` exposes **Inspect recorded events** only for a validated completed
launch with a completed receipt. The button revalidates the launch and its exact
READY preparation, then emits a detached `{launch, preparation}` selection.
Selection performs no network request. Launch clear, changed preparation,
disconnect, graph/source changes and reset invalidate observations.

`NativeObservations` validates selection without loading events. Users explicitly
load the first page, move to the next or previous page, refresh the first page,
or clear the panel. Platform changes discard the page without fetching.
Requests contain only the schema, launch UUID/digest, selected platform, offset
and limit. The panel cannot start, recover, cancel or poll a native run.

The protected workbench client supplies loopback credentials privately to a
separate observations channel. Its AbortController, epoch, streaming reader and
deadline remain owned through asynchronous digest admission. Clearing observations
cannot abort graph/source/preparation/native requests. Disconnect invalidates
both channels. Native clear also invalidates observations without touching a
pending graph/source request. Authentication/Origin denial disconnects the parent.
No automatic requests, retries, browser storage or model calls are introduced.

Each reply validates the existing native status contract against the selected
launch/READY binding, completed receipt, canonical output manifest, platform,
pagination and counts. Manifest files follow the selected native platform order:
`<platform>_simulation.db`, `<platform>/actions.jsonl`. The SHA-256 of sorted
compact ASCII manifest JSON must equal the completed receipt evidence SHA-256.
Per-record hashes use **original UTF-8 line bytes**, preserving `1.0`, exponent,
negative-zero and large-integer lexemes. Every numeric token, including integer
tokens, must decode to a finite IEEE754 double. Unsafe finite parsed numeric
convenience values become null; literal evidence stays exact. Raw literal CR/LF
is refused after actual line terminators have been removed; escaped JSON
backslash-r/n remains admissible without changing its original bytes.
Parsed numeric values are not used as display evidence. Duplicate decoded keys,
nonfinite float/exponent values, invalid surrogate
strings, depth above eight and non-object records fail admission, even when
the malformed raw record was correctly hashed.

The fixed limits are 4 KiB requests, 256 KiB data plus a 128-byte envelope,
20 records per page, 10,000 records per platform, 8 MiB per action log,
8,192 UTF-8 bytes per line and 64 MiB per native output file. Replies must
contain the exact expected page length and next offset. Count categories match
strict string `event_type` / `action_type` and strict boolean action success.
The current page supplies lower/upper correspondence constraints; prior pages
must retain identical whole-log counts, total and manifest. The backend owns
the full-log accounting and actual safe-file authentication; the client cannot
prove undisplayed log bytes from a single bounded page.
The selected log's minimum physical byte length is `3 * total_records - 1`
for a nonempty log, plus `sum(page raw UTF8 length - 2)`: two bytes per
minimal JSON object and one separator between records. A zero count requires
zero bytes. CRLF and an optional final newline can add bytes, never remove this
minimum. These physical-line checks preserve stricter count correspondence.

EN/ZH/MS copy describes recorded log counts, repetition and seed events without
equating them to unique engine actions or billed calls. Known string action/event
labels appear alongside an expandable original literal JSON record. Unknown
objects remain inspectable. Vue text interpolation makes hostile HTML/URLs inert.
Controls and summaries have 44px minimum targets, visible focus, wrapped content
and responsive single-column provenance/counts. Async feedback uses polite live
status and explicit focus. Transient errors retain a clearly marked previously
validated page of the same identity; authorization, tombstone and resets clear it.

Main must update its separately owned parent render fixture import maps for the
new SFC before running those fixtures. New client/mounted fixtures author
independent producer hashes, request/receipt/manifest/record negatives, pagination,
inert literal rendering, explicit-load/language behavior, focus, reset/late-reply
and abort separation coverage. These fixture sources have **not been executed**.
Actual native browser completion, protected real stores and output inspection,
320/768/1440 layout and keyboard behavior, hosted qualification and all acceptance
remain Main gates. No reporting, live feed, model-quality or full44 claim follows
from these source changes.
