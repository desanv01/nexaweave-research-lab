# Connected protected evidence workbench — U11b candidate

The native Vue `/research` route reads the existing Flask `knowledge_read_app`.
It is a configured local graph/principal reader, not a project directory or a
shared identity service. The existing home and investigative workflows remain.

## Connection and requests

The analyst supplies a canonical literal loopback origin (`http` or `https`,
`127.0.0.1` or `[::1]`, explicit port 1–65535), display graph ID matching
`[A-Za-z0-9_-]{1,128}`, and the configured bearer token. Host browser-origin
allowlisting must permit the actual frontend origin. Connect reads
`GET /api/graph/data/<display_id>`; health is never used as authentication.

The dedicated fetch client calls only that route and
`POST /api/graph/research/<display_id>` / `POST /api/graph/dossier/<display_id>`.
It does not use inherited Axios interceptors. The request scope is the one
configured graph. Optional `top_k` applies to each question (1–100). The UI's
temporal fields require explicit UTC `...Z`; client DTO validation also accepts
aware ISO offsets. No naive timestamp is interpreted in browser local time.
Research permits one nonblank question up to 2000 Unicode codepoints; dossier
permits a title and 1–6 ordered heading/question pairs with the accepted DTO
limits. Request JSON UTF8 limits are 16 KiB research / 32 KiB dossier.

The token remains only in route/client memory; the input clears on submission.
No browser storage, cookies, analytics, raw-error rendering, URL token, provider
key, retry, fallback or model call is introduced. Fetch uses `redirect:error`,
`credentials:omit`, `cache:no-store`, `referrerPolicy:no-referrer`. A finite
125-second deadline covers fetch and body reading. Stream caps are 2 MiB graph /
2 MiB research / 4 MiB dossier plus 1024 bytes of envelope overhead. Readers
cancel and release on completion/failure; requests abort on cancellation,
replacement, disconnect and unmount. Client and view generations prevent stale
responses or errors replacing the current state. HTTP 401 clears connection
and protected results even if its body is malformed. Unknown envelopes, DTO
fields, unsupported enum values, broken citation offsets/reference joins and
unknown errors fail closed into fixed localized error text.

## Evidence interpretation

Graph entities/relationships and claim rows have explicit pages of ten entries.
The inspector shows the whole exact retained Unicode excerpt as plain Vue text,
source name/revision, source SHA256, Unicode codepoint start/end (end exclusive),
evidence ID and optional declared page. It neither activates source links nor
renders HTML/Markdown. Missing retained citations have an explicit unavailable
state. No original DOCX/PDF opening, download or authenticity claim is supplied.

Research keeps source claims, simulation observations and other layers distinct.
It shows returned citation counts, retained-passage counts, actual per-query
scan/eligible/excluded/unknown/returned/truncated metadata, and competing-claim
review candidates. Dossier preserves declared section order, claim/reference
identities, query trace and digests. Sections with no claims remain visible.
Eligible facts are ranked by token overlap; returned facts can have zero overlap
with the question. Ranking does not require a positive match or establish source
claim support, which remains unreviewed. No model-generated conclusion or semantic
support judgment is supplied. Simulation observations are not real-world
predictions; historical edges are not a bitemporal reconstruction. Dossiers are individually
guarded queries, not atomic snapshots. Digests are record identifiers, not
signatures. The legacy report chat/interviews are not replaced.

## Locale and accessibility design

Dedicated route-only English, Chinese and Malay copy is selected in memory.
Inherited catalog changes only add the English/Chinese Home navigation label.
This is not full-application Malay coverage. Native Vue/scoped CSS use system
fonts and existing brand text, light slate surfaces, dark ink, institutional
blue and restrained amber. No hosted design, remote asset or dependency was added.
UI/UX Pro Max static guidance informed labels, progressive disclosure, visible
focus, 44px actions, wrapping and responsive flow. The citation inspector is
alongside results from 1024px and below them on smaller viewports. Selection
focuses the inspector; Escape/Close returns focus to the citation button. There
is one status live region, a skip link, and no decorative motion.
The route now assigns `--control-border:#7b8797` to input, textarea and select
boundaries, separately from the decorative panel border token. This scoped style
correction follows Main's reported browser measurement of insufficient boundary
contrast on white controls. Its final rendered contrast remains for Main to verify.

## Qualification still required

Worker authored Node client fixtures and actual SFC/jsdom mounting tests using
the existing locked Vue/compiler-sfc/jsdom toolchain. No worker command imported,
executed, installed or verified these sources. Main must run the authored tests,
Vue build, actual Flask/PostgreSQL/Neo4j flow and keyboard/browser review at
375/768/1024/1440 plus 200% zoom. jsdom focus/click assertions are not proof of
real browser keyboard activation, layout, contrast or accessibility conformance.
No paid/public/full-U11/all-44 acceptance is claimed.
