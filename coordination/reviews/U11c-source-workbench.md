# Main review — U11c connected source workbench

Reviewed on 2026-10-02 against accepted PR75 merge
`b9c6496ca20058c2aedd95991b62f6865dd8859b`. The eleven preserved worker/task
files match the latest pause checkpoint byte for byte. The worker remained idle;
Main owns this review, browser execution and Git/CI integration.

The protected route now exposes explicit source list, prepare/retain and inspect
actions through the existing private workbench client. New text and DOCX inputs
have bounded sizes, exact input digests and one UUID per reviewed attempt.
Submitting clears draft text/file state and never automatically retries a POST.
An uncertain attempt keeps only its reconciliation identity; explicit GET, rather
than a retry or rollback claim, is required. Shared cancellation, deadline and
401 handling fence late responses and clear protected state. GET validates text
and each declared excerpt digest using Unicode codepoints; overlapping and
reversed legacy declarations retain their stored order. DOCX receipts distinguish
transient extraction blocks from persisted text and graph evidence.

Main reviewed the API validator, route lifecycle, actual compiled Vue component,
locale keys and authored regression assertions. The inherited route assertions
remain, with only the real Sources child added to the test import mapping.
No dependency, lockfile, upstream notice or inherited baseline file was changed.

## Executed evidence

- Final unchanged candidate: 53 Node tests passed, zero skips/failures, 11.99s;
  Vite build passed in18.50s. Existing large-bundle warning retained. Root logs
  `u11c-node-2.log`, `u11c-build-1.log`; original failed/corrected runs preserved.
- Actual CUA browser against Vue, protected Flask, fixed installed knowledge
  children, real PostgreSQL and Neo4j, synthetic scoped records only: source
  list, explicit fresh inspection, Unicode/overlapping passage offsets and
  keyboard Escape focus return passed. A retained `<script>` string remained
  literal text with no script element; exact textarea DOM value matched GET.
- Prepared and retained synthetic UTF-8 BOM/CRLF file:64bytes/54codepoints,
  SHA256 `36fcab738dc54dd33cb53816703896263964f7d9c385459baa81e058f9ae7660`.
  Fresh GET preserved the BOM and CRLF exactly. Paste naturally uses the browser
  textarea's LF-normalized DOM value; original clipboard CRLF is not claimed.
- Synthetic DOCX1002bytes input SHA256
  `b6b7986519bf2340c0918c83f633e46d5c9a80e0f14336c690d6d671eda4d465` returned
  four transient blocks, including an empty table cell, and three nonempty
  passages. Retained text52UTF8bytes/44codepoints, SHA256
  `d6731bbed25df0f69cdf3546b339ae259e653fddc12e1847f0a744d2eb0ab8c3`;
  explicit GET verified `DOCX 😀猫\n\n雪 café\t\n\nEnd of synthetic document.`
- Actual EN/ZH/MS source controls/receipt/limits rendered. At viewport320/768/1440,
  no page horizontal overflow; visible source buttons were at least45.7px high,
  long hashes and exact text wrapped. Temporary viewport overrides reset.
- Reload cleared token, attempts, receipts, source inspection and file selection.
  Wrong synthetic token produced authorization denial and cleared state. Valid
  reconnect plus explicit refresh/GET recovered the same retained revision
  `5be8912a-464c-4248-b85f-0a7864aedeb3`, exact text60bytes/55codepoints,
  SHA256 `b2465de0a7283c1d951a50279b59c27a6055b67731c47157c5a5ce52c60f8e48`.
  Disconnect cleared all protected state again.
- Existing actual research and dossier calls each returned the fixture's source
  claim, two resolved citations and explicit unreviewed-support limitations.
  Source retention did not start any graph ingestion or paid provider operation.

Root screenshot, fixture hashes, DOM, events and closure receipts are preserved
in `u11c-browser-2026-10-02` and `u11c-browser-recovery-2026-10-02`. The screenshot
named `source-docx-desktop.jpg` uses the restored default617px viewport, not a
1440px screenshot. The first fixture exceeded its30minute harness lifetime after
a slow file chooser; that exit1 remains a harness failure, not a suite pass.
Both owned trees closed with no cleanup error. A separate bounded recovery
fixture finished exit0; fresh Neo4j groups removed, retained stores preserved,
named local PG/Neo containers stopped. Temporary browser tab closed.

## Acceptance limits

No original binary retention, OCR, page-layout recovery, semantic quality,
graph-ingestion execution, full source pagination or whole-phase/all44 acceptance.
Maximum-size/has-more/uncertain-response/nonJSON401/mode404/late-cancellation cases
are regression-test evidence, not separately reproduced live browser states.
200% browser zoom, comprehensive assistive-technology and contrast audits are
not verified here. Host restart persistence is PR75's real API/store evidence;
this browser journey proves reload/reconnect persistence within the live host.
No paid provider calls, public listener or deployment. Exact hosted PR/push and
postmerge gates remain required before integration acceptance.
