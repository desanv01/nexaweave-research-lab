# Connected experiment observation workbench

The research route now hosts an additive experiment section using its existing authenticated private client. The operator-bound experiment project is separate from the current graph: no association is inferred from graph IDs, source IDs or recorded anchors.

Mount, connect, locale changes and reload issue no experiment requests. The user explicitly loads the fixed `GET /api/experiments/catalog`, selects 1–16 members, enters a literal title and explicitly calls `POST /api/experiments/compare`. The POST contains only `version: 1`, `title` and `member_ids` in catalog order. Titles are nonempty, control-free, bounded to 160 UTF8 bytes; selection and request bodies are bounded. Local admission also accounts for the accepted host's ASCII-escaped request guard. No launch, registration, cancellation, recording capture, model or graph operation is introduced.

## Transport and lifecycle

The component receives only methods, connected/busy/reset version and locale. The methods close over the parent's single token-holding client. Existing loopback origin, bearer, guarded stream, duplicate JSON key, UTF8, depth, deadline, generation/AbortController and reader cleanup rules apply. Catalog replies have a 32KiB data allowance plus 16KiB envelope; comparison replies have 512KiB plus 16KiB. No redirect, ambient credentials, caching, referrer, polling or automatic retry is introduced. `experiment_unavailable` is admitted only for HTTP503 alongside inherited fixed guarded error codes. HTTP401/403 clear client authorization and parent protected state.

Catalog refresh clears its previous window, title, selection and comparison before requesting. Title/selection changes invalidate results and their generation. Disconnect, reconnect and reset clear local protected state; unmount invalidates pending ownership. Shared cancellation aborts the active read. Frozen detached request/catalog snapshots bind every response; late responses cannot join against edited selection or restore cleared data. Failures never retain a prior comparison as current. Language changes only alter copy.

## Admission and evidence boundary

`experimentComparison.js` admits exact keysets for catalog, members, metrics, recordings, distributions, pairwise matrix and coverage. It checks bounded labels/IDs/UUIDs/digests, integer counts, canonical signed64 seed strings through bounded BigInt admission, declared state/disposition, null metric availability, action count sums, cancellation overlap, distribution groups/sample/missing counts, ordered membership and matrix member fields. Server statistical values are preserved; the browser does not calculate a native comparison or recreate Python floating/canonical serialization. Digests are validated in shape and displayed as server-validated provenance; this is not browser cryptographic verification, a signature or a truth judgment.

Comparison admission conservatively matches every catalog member field, including state and cancellation intent, against the admitted catalog snapshot. The accepted server obtains a fresh catalog during comparison. A run transition between the explicit catalog read and comparison can therefore produce a valid server reply that this UI rejects as stale. The localized recovery instructs an explicit catalog reload. This fail-closed choice avoids silently substituting a newer observation into a reviewed selection; no backend contract change was made. Catalog ordering is always authoritative.

Successful observations display actual retained logged totals, by-type counts and nullable final table counts. Non-successful metrics are unavailable. Known zero and unavailable remain distinct. Distribution details show available sample/missing context and label floating statistics approximate. Pair details use text for same/different/unavailable; seeds remain exact, including signed64 extremes and 2^53+1. Expandable run/recording/digest details render inert text without raw JSON, arbitrary links, event/profile/source contents or protected storage.

The coverage copy explicitly states descriptive-only results, non-atomic cohort reads, possible initial log duplicates, later interviews in trace, no causal attribution/provider quality/ensemble launch/shared budget enforcement, unknown actual spend, and no exact event-row or historical-truth proof.

## UI and qualification

The existing system font and navy/white/neutral workbench tokens are retained. Responsive cards avoid a wide matrix; native labels, checkboxes, buttons and details supply keyboard controls, 44px actions, visible focus, polite progress and expandable provenance. EN/ZH/MS copy is additive. Existing source/research/dossier/ingestion workflows are unchanged apart from the new section and private methods.

Authored Node tests cover actual private transport, immutable snapshots, bounds, malformed/foreign projections, exact seeds, all five dispositions/cancellation overlap, unavailable recovery and cancellation/auth clearing. Actual compiled SFC sources cover localization, inert labels, null versus zero, selection/title invalidation, reset/disconnect/unmount suppression and route integration with the real nested component.

NO checks executed; Main qualification pending. These test sources and responsive styles do not establish successful Node/build, real browser/PG acceptance, full accessibility, native ensemble/causal quality or all44 completion. Main owns execution, review, browser qualification and Git integration.
