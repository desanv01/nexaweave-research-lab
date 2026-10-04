# Retained dossier downloads workbench (U09b)

The existing EvidenceResults mounts the actual DossierExport component only for
its current model_free_evidence_dossier result. ResearchWorkbench already clears
this protected result on new evidence requests, connection/reset/disconnect and
authorization failure. No parent/client/backend/transport changes are introduced.

Each native button click detaches the current dossier as a JSON DTO snapshot.
The local helper reuses validateResult('dossier', snapshot,
snapshot.request.display_graph_ids[0]); it also validates the input before
serialization. The existing private authenticated client initially admits the
result. This local shape/join/count/flag validation is not reauthentication,
cryptographic fingerprint verification or authority from self-declared metadata.
No bearer, origin, credentials, configuration or path is passed to the component.

Markdown is the retained server markdown encoded literally as UTF8, including
Unicode, BOM, line endings and numeric entities. It is never rendered as HTML or
interpreted as instructions. JSON is JSON.stringify of the detached complete DTO
plus one LF. It preserves citations, limitations and server fingerprints, but is
not the original HTTP wire representation, Python canonical hashing, a signature,
semantic truth or a browser-recomputed fingerprint. Interface locale changes only
translate controls and feedback, not evidence or its recorded output language.

The final UTF8 byte array for each format must be at most 4 MiB; overflow rejects
rather than clips. Filename/MIME pairs are fixed:

- mirofish-evidence-dossier.md / text/markdown;charset=utf-8
- mirofish-evidence-dossier.json / application/json;charset=utf-8

Only explicit clicks create a Blob, owned object URL and temporary hidden anchor.
The anchor is removed immediately after the initiation attempt. There is at most
one current URL. Replacement clicks revoke the previous URL and cancel its timer;
a one-second cleanup timer revokes the newly owned URL without initiating any
other download. Result replacement/reset and unmount revoke/cancel immediately.
Creation/click failures also remove the anchor and clean resources. Feedback is
fixed localized text saying download requested, never completed or saved. The
browser controls saving; clearing UI state cannot delete downloaded files.

No automatic download, new HTTP/model/graph request, external href, configurable
filename/path, disk API, executable bundle, dependency or hosted design call is
added. Existing workbench tokens, system fonts, native buttons, visible focus and
44px minimum controls are retained; actions wrap on small screens. Authored SFC
DOM regressions mock only browser download APIs and retain actual compiled nested
components. Node/jsdom cannot prove native keyboard synthesis, browser saving or
responsive layout. Main must run relevant regressions/build and a bounded actual
browser two-download lifecycle/byte workflow before accepting this packet.

NO checks executed; Main qualification pending. This bounded local export does
not establish generated narrative, whole U09/all44, semantic/provider/billing,
paid call or public release acceptance.
