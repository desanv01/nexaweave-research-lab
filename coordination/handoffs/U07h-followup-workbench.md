# U07h follow-up workbench UI handoff

Owner: UI u01. Base: `f14c332c448cb8177ce4b8f43184043440da1a4d`. Branch: `task/u07h-followup-workbench`.

Authored only the eight paths in `NEXT-UI-PATHS.json`. Main owns integration, execution, review, CI, Git and acceptance. No checks were run by this worker; every result below is **unverified** until Main executes the suite.

## Source

- `frontend/src/api/connectedFollowups.js`: isolated protected follow-up channel; exact request envelopes, immutable plan and binding validation, signed 64-bit fingerprint path, receipt/manifest/head proof, prompt and protected history checks, read/download validation, complete-conversation current-null publication proof, abort/epoch and one-shot Start fencing. Credential state remains in the private client.
- `frontend/src/components/workbench/ConnectedFollowup.vue`: completed-report selection, question/history Review, distinct Start, same-ID refresh/cancel/manual recovery, paged history, verified answer and downloads, owned Blob URL cleanup, connection and reset clearing, localized accessible states.
- `frontend/src/i18n/connectedFollowups.js`: English, Chinese and Malay copy and error messages, including the external current receipt/head proof explanation.
- `frontend/src/components/workbench/ConnectedReports.vue`: verified completed-report follow-up action emits the admitted report snapshot for Main's parent to pass into the sibling.

## Test source awaiting Main execution

- `frontend/tests/connectedFollowups.test.js`: channel/codec admission, history, 64-bit values, current-null conversation proof, lost Start and confirmed denial cases.
- `frontend/tests/connectedFollowups.component.test.js`: mounted panel recovery, inert answer, Blob revoke and late reply clearing.
- `frontend/tests/workbenchConnectedFollowups.component.test.js`: mounts actual `ResearchWorkbench.vue`, `ConnectedReports.vue` and `ConnectedFollowup.vue` with scripted private transport; checks report action, Plan, lost Start, disconnect/reconnect/manual same-ID recovery, locale changes and credential isolation. Main must run it after integrating its parent/private client paths. The fixture intentionally has one Start request.

## Integration notes for Main

The channel factory signature is `createConnectedFollowupsChannel({ fetchImpl, deadlineMs, connection, denied })`; methods are `plan(payload, parentReport)`, `start/status/cancelTurn/read/download(payload, known)`, `history(payload)`, `clear()` and `cancel()`. The parent panel expects `methods.plan/start/status/cancel/read/download/history/clear`; its props mirror the connected-report panel plus `selection` and `resetVersion`.

Static inspection of backend u02 found completed follow-up progress currently recorded with `completed_sections=0,total_sections=0`. The UI validator now accepts a completed record when percent is 100 and completed sections equal total sections, matching the backend's validated shape. Test fixtures use a complete 1/1 record, which the same contract admits. Main should resolve any backend final shape or contract changes against this validator before acceptance.

Static inspection also found `conversation.json` current pair fields match `validateConversation`: full `answer`, current `receipt_sha256=null` and `published_head_sha256=null`, with the current proof supplied by the completed DTO. Prior pairs must carry non-null receipt/head. `turn.json` is hash checked as a selected export artifact; backend checks its own identity and proof scope before publication.

No runtime, test, import, parser, build, browser, DB, network or Git operation was performed by this worker. Main owns all execution and required repairs.
