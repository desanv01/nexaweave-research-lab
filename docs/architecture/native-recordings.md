# Native recordings: authored U08a candidate

This is a bounded C32 implementation candidate, awaiting Main review and
qualification. C33 checkpoint continuation and C34 branch execution remain
disabled. Playback is the stored sequence, never a deterministic model rerun.

The trusted host supplies an owned absolute source root, a new absolute bundle
destination, graph/simulation/run/branch/project anchors and project revision,
the frozen runtime digest and OASIS/CAMEL versions. The host must confirm the
owned native session/process has successfully closed. This confirmation is not
a wire request field. Native `simulation_end` happens before close and the
one-shot `.native_prepared_start_claim` persists after close; neither proves
closure. Host ownership/closure remains a required trust boundary. A malicious
external writer that changes and restores bytes between observations is outside
this closed, exclusively owned source contract.

`capture_recording` admits enabled platform prepared inputs, complete action
JSONL and closed SQLite artifacts only. It rejects active/incomplete markers,
SQLite sidecars, unsupported legacy status markers, reparses/symlinks, executable
source files, credential-bearing JSON keys, missing platform artifacts, source
changes and existing destinations. Source reads use bounded no-follow descriptors
where available, file identities and repeated byte comparisons.
On Windows, path-stat and descriptor-stat ctime have different semantics, so
cross-API comparisons retain exact device/inode/size/mtime checks. Full
ctime-inclusive identities are still compared separately between both path
observations and between both descriptor observations. POSIX cross-API
comparisons retain ctime too. No sleep, tolerance or retry is used.
Root traversal, individual file sizes, total bytes and log event counts have
fixed bounds.
Source discovery uses incremental `scandir` with a global 2000-entry limit,
depth bound and at most four open directory iterators; it never materializes
an unbounded directory listing. Bundle admission traverses only the root and
fixed enabled-platform directories, immediately rejecting any extra entry.
Its path sets and entry budget are derived from the fixed artifact allowlist.
Source files are never written, reset or deleted. No scripts, models, pickle,
provider setup or arbitrary state are captured.

Byte copies are made only at this closed/no-sidecar boundary. Destination creation
is exclusive. Files are flushed before the manifest is written last; failures
retain partial destinations for inspection. Such destinations cannot pass
admission. Main keeps the returned manifest SHA256 outside untrusted wire input.
The manifest records exact file names, sizes/digests, anchors, runtime versions,
runtime digest and native schema digest/user_version/table coverage. It is a
recording manifest, not a live-engine checkpoint. A SHA256 pin is integrity
relative to trusted host provenance, not an independently signed authenticity
claim. Publication does not mutate file permissions; bundle bytes are pinned
and detached in memory on reader admission.

`NativeRecording(bundle, anchors=..., expected_revision=...)` requires exact
trusted anchors and a pinned manifest digest. It checks the entire allowlisted
path set, sizes/digests, JSON/UTF8 bounds, complete enabled-platform logs and
prepared identity before any SQLite deserialization. DB bytes are opened only
in memory with query-only/trusted-schema-off settings, no extension loading,
bounded SQL VM work and fixed queries. Views and triggers are rejected.
Virtual/shadow tables are rejected using SQLite's parsed
`PRAGMA table_list` classifications, including whitespace/comment SQL spellings.
Runtimes without this metadata fail closed. A closed SQLite format that cannot
be deserialized by the host fails safely; this is not a WAL recovery adapter.
No disk DB reopen occurs after
admission. Subsequent file mutations do not change an admitted reader's revision.

Launch `backend/app/services/native_recording_cli.py` directly in a fresh Python
process. Its trusted argv specifies `--bundle`, `--revision`, `--graph-id`,
`--simulation-id`, `--run-id`, `--branch-id`, `--project-id` and
`--project-revision`. Main constructs those arguments; wire callers never choose
them. Direct launch imports only the saved sibling recording modules and stdlib,
without backend app initialization or engine/provider/database service imports.
No installation, DB server, model credential or provider call is required.

Each JSON line requests version 1, operation `playback` or `metrics`, enabled
platform `twitter` or `reddit`, and integer `limit` from 1 to 100. Playback may
include a null or opaque `cursor`. Metrics requires a null/absent cursor; its
limit bounds each final-table preview. Extra fields, bool integers, duplicate
keys, nonfinite values and arbitrary selectors fail with fixed public errors.
Frames cap at 8192 bytes; a process serves at most 1000 frames and terminates on
oversized/incomplete frames. Responses cap at 2 MiB. Errors contain fixed codes,
never raw paths, SQL or exceptions. Admitted read replies include recording
revision, trusted provenance and both unsupported feature flags.

Playback keeps original per-platform file order, equal timestamps, duplicates,
initial events, round boundaries and action fields. It adds explicit platform
and zero-based source event offsets without rewriting the recorded object.
Native action records must omit `event_type`; explicit `event_type: null` is
rejected at capture/admission, so metrics cannot silently drop such an action.
Cursors bind revision, platform and offset; they are canonical integrity checks,
not authorization tokens. Exhaustion is explicit; no cross-platform merge order
is invented. Responses are detached copies of pinned data.

Metrics count logged actions by type, agent and round, with duplicates retained.
They report bounded previews and complete row counts for present allowlisted
native post/follow/like/dislike/comment/mute/trace tables. Missing tables and
truncated previews are explicit. Final rows may include interviews performed
after the last simulation log event. Initial manual action logs can duplicate
later trace-derived logs. Exact event-to-row links, historical graph snapshots,
causal conclusions and truth claims are unsupported. Metrics do not silently
deduplicate or fabricate historical native state.

Authored pure tests cover malformed requests, bounds, duplicate events, cursor
revision/platform binding, tamper-before-DB, inert arguments, scope, symlinks,
changing sources, active markers/sidecars, partial destinations, credential keys,
executable schema/source rejection and parent preservation. Additional pure
tests cover whitespace/comment virtual-table schema rejection, null event-type
rejection and incremental scan limits/immediate extra-path rejection.
Authored native tests use accepted offline both-platform NativeSimulationSession fixtures,
independent SQLite/log expectations and fresh CLI processes under scrubbed
environment, engine-import prohibition and a socket guard. Main must run those
sources and integrate runners/CI before accepting any capability slice.

All 44 plan requirements and inherited notices remain in force. Vue/Flask,
OASIS/CAMEL and Graphiti with self-hosted Neo4j Community are retained. The
configurable initial provider remains DeepSeek official `deepseek-flash` with
separate embeddings. This workflow makes no paid calls or public deployment.
