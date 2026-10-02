# U04d stable implementation handoff — UNVERIFIED

Correction2 stable, UNVERIFIED: Main extended scope to thirteenth path
`backend/run.py`. Its two protected-mode conditions now include research_local
for Config.validate_readonly and the safe default loopback host. Legacy/debug
validation is unchanged. Authored regression sources load actual run.py with
real protected settings validation/factory and intercept Flask.run only; they
assert protected mode/default loopback/threaded/debugfalse, rejection of debug/
nonloopback, and no dependency on legacy model/Zep validation. Existing readonly/
default legacy cases and Main's three appended admission/deadline tests and
short parameter IDs were preserved. Startup documentation is updated. Only
run.py, the assigned API test source, architecture doc and this handoff changed
in correction2. No tests/import/checks/Git/runtime executed; worker idle.

Correction1 stable, UNVERIFIED: retained GET/list now use accepted SourceStore
read eligibility (nonempty valid UTF8/noNUL/name<=256/text<=1MiB), distinct from
stricter nonblank/control-restricted new POST admission. GET preserves stored
ordinal order, including overlap/reversed declarations, validating each bound/
hash/unique evidence ID independently. New retain still compares exact ordered
generated declarations/typed IDs/pageNone. Pure DTO and actual PG regression
sources include overlapping/reversed spans and legacy whitespace/control name/
text; strict corruption and new-retention-order denial cases remain authored.
Only assigned client/source_library/tests/docs/this handoff changed in correction1.
No test/import/check/runtime/Git execution; source reads/searches and apply_patch
only. Main qualification and all limitations below remain outstanding. Worker idle.

Resumed under the explicit human 2026-10-02 resume. Main reported all preserved
partial-file hashes and backup bytes unchanged before dispatch. Worker did not
run hashes, status or Git. Assigned checkout is existing u02,
`task/u04d-protected-source-library`, Main-recorded accepted base
`528a1681247e3a72c2cdbf67efbd1dfadfeedfa9`. No retired writer was restored.

Implementation and regression sources are stable for Main review. Worker is
idle after this handoff; no acceptance, execution or qualified runtime claim.

## Thirteen authored/edited paths

1. `backend/app/__init__.py` — explicit research_local factory branch and source
   facade injection; legacy behavior retained.
2. `backend/app/knowledge_read_app.py` — conditional source route registration
   and accurate mode/capabilities; existing auth/origin/read routes retained.
3. `backend/app/source_library_api.py` — bounded source GET/POST requests,
   strict input, sanitized errors, no-store/nosniff and HTTP DTO rechecks.
4. `backend/app/services/knowledge_source_client.py` — source transport profile,
   strict payload/result validation and deterministic codepoint passage IDs.
5. `backend/app/services/knowledge_source_facade.py` — max-two admission,
   context-before-DOCX extraction, shared deadline, repeat-authorized retention,
   input digest and transient extraction flags.
6. `services/knowledge/src/mirofish_knowledge/source_library.py` — PG-only trusted
   settings/dispatcher, exact persisted binding/project/workspace checks,
   bounded list/get/retention using unchanged accepted stores.
7. `services/knowledge/src/mirofish_knowledge/source_bootstrap.py` — fixed installed
   one-frame bootstrap and installed knowledge/storage path checks.
8. `services/knowledge/src/mirofish_knowledge/__init__.py` — Main-approved twelfth
   path extension: lazy public GraphitiKnowledgeProvider export, contracts and
   __all__ preserved; provider.py unchanged.
9. `backend/tests/test_source_library_api.py` — authored pure factory/auth/HTTP/
   input/Unicode/DOCX/DTO regressions, explicitly injected clients.
10. `services/knowledge/tests/test_source_library_postgres.py` — authored actual
    disposable PG ownership/idempotence/collision/snapshot/history/closure and
    tamper cases, plus fresh installed denied-provider/public-export regressions.
11. `docs/architecture/protected-source-library.md` — boundary/behavior/limits.
12. `coordination/handoffs/U04d-source-library.md` — this unverified handoff.
13. `backend/run.py` — Main-approved correction2 scope extension, two narrow
    protected-mode conditions for normal startup validation/default loopback.

The Main-owned task packet was read but not edited by this worker. No store,
SQL, dependency/lock, provider.py, evidence transport/API, frontend, runner,
CI, ledger, baseline or integration checkout edits were made by the worker.

## Operations used

Only PowerShell `Get-Content`/`Select-Object` source/coordination reads, `rg`
source searches/file discovery and `apply_patch` assigned source writes were
used. Initial reads included root/local rules, transfer/current continuation/
registry and packet; some attempted paths did not exist and were subsequently
resolved by source discovery. No product modules were executed or imported.
The human pause was obeyed immediately after the last atomic source write;
the eight partial source files were left intact. Resume continued those files
after Main's preservation verification.

No tests, lint/type checks, builds, runtime/install, status/hash/Git, network,
DB, browser, audits, delegation, other-chat messages or automation operations
were executed. All test/import/subprocess/SQL text in regression files is
authored source only. GPT6.1Sol/medium is the requested worker setting; FastON
has no available setter/verifier and is not claimed applied. No paid/public
calls or permission inferred.

## Main review and qualification remaining

All thirteen paths require Main source review and execution. Regression sources
are not passing-test evidence. Injected clients do not qualify actual process,
authority, PG, HTTP or model behavior. Main owns offline installed candidate,
actual HTTP/fresh child/PG tests, installed paths/import compatibility, socket
observations, concurrent admission/deadlines/owned process cleanup, inherited
gates, exact CI/log qualification and any Git integration/acceptance.

The PG tests require the established explicitly enabled disposable fixture,
current schema3 and knowledge migrations; they do not use a legacy2-only schema.
Fresh `-I` subprocess regressions require Main's noneditable installed candidate.

List is latest20/has_more, not complete pagination. No original binary is
retained; DOCX input digest/blocks are transient. No graph/ontology/model or
operation execution occurs. Source transactions are atomic within SourceStore;
binding/project/source transactions do not provide atomic revocation across
transactions/databases/graph. Postspawn transport failure is outcome_unknown;
no retry/regenerated revision/proven rollback claim. Input digest is caller
integrity, not publisher trust. No OCR/layout/semantic/original binary proof or
full-phase/full44 acceptance. The process boundary is not an OS/native-libpq
egress sandbox. HTTP revalidation of an explicitly injected DOCX facade can
only check structure/IDs/hash shapes; exact text joins are performed in the
real facade before returning the metadata-only retention result.
