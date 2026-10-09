# Native optional dependency import correction

The native runtime retains `camel-oasis==0.2.5` and all locked dependencies. A bounded installer modifies only the installed `oasis/social_platform/recsys.py` to defer optional SentenceTransformer and two vector-function imports until requested. This is modified vendor source, not an unmodified upstream installation. The actual class/functions remain the upstream implementations.

## Install and verify

Run from the repository root using the locked backend interpreter. On Windows, after the normal locked installation:

```powershell
uv sync --project backend --locked --extra dev --extra native-store --extra native-temporal --python 3.12.13
if ($LASTEXITCODE -ne 0) { throw 'Locked backend installation failed' }
backend/.venv/Scripts/python.exe tools/apply_native_import_patch.py --apply
if ($LASTEXITCODE -ne 0) { throw 'Native import patch application failed' }
backend/.venv/Scripts/python.exe tools/apply_native_import_patch.py --check
if ($LASTEXITCODE -ne 0) { throw 'Native import patch verification failed' }
```

For an already installed locked runtime, use the same two interpreter commands without repeating installation. `--apply` and `--check` are mutually exclusive. There is no force, alternate target, version, hash or environment override. Do not run this installer in a different interpreter and assume the backend environment changed.

The stdlib-only installer discovers the installed distribution through metadata without importing OASIS or its SDK dependencies. It admits only distribution version0.2.5 and the canonical target path. The original source's normalized LF/UTF-8 SHA-256 is:

```text
5c27e5219599b682a4658455c93adf382c007dfa3757bdaf48c7ff73a4b92345
```

The reviewed normalized source states are:

| Source state | SHA-256 |
|---|---|
| Pristine0.2.5 upstream | `5c27e5219599b682a4658455c93adf382c007dfa3757bdaf48c7ff73a4b92345` |
| Earlier SentenceTransformer-only lazy correction | `92cd2a6e04bca3d021283d3963ccc02b687031bf425db02b1bdfee794411631f` |
| Combined SentenceTransformer/vector lazy correction | `632e411baaba7cf2c6855993905b95a6d3b64c95f6b53049b050e6ebe32ef8e7` |

`--apply` admits only these exact reviewed states and upgrades pristine or the earlier correction to the combined target. Applying the combined state again is idempotent. `--check` requires the combined state; the earlier correction alone no longer qualifies this installer target. Unknown version/path/content refuses, with no force override or automatic acceptance of an arbitrary vendor edit. Application writes the installed target atomically and invalidates only its verified module cache. Retain emitted nonsecret fingerprint/status receipts; compare the combined hash to the reviewed value above. A previous SentenceTransformer-only check receipt is separate earlier evidence, not a combined-patch check.

Preserve the exact original bytes privately before Main applies the installer to a local environment. Keep the original license text and distribution version/metadata. Do not upload local vendor backups or private diagnostics to the public repository. The installer and emitted vendor before/after fingerprint receipts provide the reproducible transformation. `docs/upstream/patches.json` retains its65 original baseline entries unchanged; this vendor transformation is not an additional entry in that ledger.

Reinstallation or an environment rebuild can restore the original upstream file. Run `--apply`, then `--check` again after the locked sync/install and before native qualification. A previous successful check does not qualify a replacement environment. CI applies and verifies with the locked backend interpreter after its installation step.

## Behavior preserved and changed

The transformation removes the one eager top-level SentenceTransformer import. The existing MiniLM constructor branch obtains the actual `sentence_transformers.SentenceTransformer` class on first use with the original constructor arguments, device, cache behavior and exception chain. The getter honors an existing module-global injected class and stores the actual resolved class. Explicit `recsys.SentenceTransformer` access or from-import resolves the real class through the narrowly scoped module attribute hook; unknown attributes raise AttributeError. There is no proxy class or fake module.

The combined correction also removes the eager import of `generate_post_vector` and `generate_post_vector_openai` from `process_recsys_posts`. The original two TWHIN vector call sites resolve those actual functions on demand. Generic TWHIN retains its original model/tokenizer/corpus and batch_size1000; the OpenAI branch retains its original corpus/batch_size1000. No replacement embedding function, fake module or model proxy is introduced.

When a vector name is absent, its resolver imports the real functions and records them without overwriting an already injected peer global. Existing injected values are recognized by key presence, not truthiness or callability: None, integers, exception objects and callables keep their natural lookup/call behavior. Named attribute/from-import and star-import vector access resolve actual upstream function objects when no injected value exists. Unknown attributes raise AttributeError except the deliberately supplied virtual `__all__`.

Cold `dir(recsys)` includes both vector names without importing the vector module. Virtual `__all__` includes current public globals and places those two names before ActionType as in their original public ordering; an explicitly injected module-global `__all__` takes precedence. These hooks preserve the reviewed export behavior, not every namespace/introspection detail:

- `vars(recsys)` omits lazy names until resolution; direct namespace lookup cannot assume eager presence.
- `getattr`/`hasattr` for virtual `__all__` now succeeds even though it need not be a stored global.
- Namespace insertion order after lazy resolution differs, and `dir()` exposes added private helpers/hooks.
- Named/star access can load the optional vector module; merely calling cold `dir()` does not. Consumers depending on eager namespace contents or introspection timing require separate compatibility review.

Recommender/vector calculations, random/seed behavior, Torch, sklearn, coarse filtering and original action/native behavior are unchanged. No dependency is removed, replaced or upgraded. Factories, connection lifetimes, SQL/authority reads, model selection, budgets/caps and gate selections are outside this correction. The intentional behavior change is dependency import/failure timing: missing/failing optional dependencies surface at actual SentenceTransformer or vector access/use rather than unrelated recsys import. Real constructor/function errors and their original arguments/chains remain; two separate calls need not raise the same exception object.

## Measurement and qualification

The earlier retained cold probe observed a23.672-second loader and14.882-second cumulative SentenceTransformers import chain. Cumulative measurements are nested and not additive. Keep that earlier probe distinct from the vector comparison below.

The private vector comparison used five fresh owned children, actual43-file copied OASIS inventories and the existing parallel native loader. Its before source was the earlier SentenceTransformer-lazy variant, not pristine upstream; only recsys differed in the combined copy. Local loader observations were:

| Copy/control mode | Loader seconds |
|---|---:|
| Earlier variant, cold |28.281|
| Earlier variant, named controls |19.766|
| Combined variant, cold |12.546|
| Combined variant, star-first controls |14.032|
| Combined variant, named/injection controls |15.109|

These are limited local samples from different fresh modes, not a statistical hosted gain or proof the whole serial fixture fits its deadline. Main's private review records exact reverse-source/AST equality, unchanged other42 vendor files and preserved license. Controls establish actual loaded classes, real vector function identity, named/star/dir/global-injection behavior and delegated empty-input error type/arguments. The initial failed star-first empty-error harness assumption is preserved as failed evidence; corrected control results do not erase it.

No model/weight/provider operation was requested by these controls; the OpenAI vector function was not invoked, and the generic function used only empty inputs for the real error comparison. Selected network/DB entrypoint guards recorded zero attempts. This is scoped intent/guard evidence, not a universal constructor, model-load, OS-network or database census and not a research-quality evaluation. Private-copy closure/source checks do not qualify the installed product or actual full native journey.

Current q24 PR all-eight success and push native990 deadline plus failed PostgreSQL fallback remain separate preserved outcomes. The push failed during followup disabled history/export recovery; PR success does not accept that incomplete result. No unchanged503eb head retry, timer widening, assertion reduction or factory/authority change follows from these import samples.

For this vendor delta, Main uses targeted installer and actual-runtime upgrade/cache/class/function compatibility checks plus relevant native regressions. Reuse reviewed report/followup/UI/component evidence with its explicitly unchanged scope; repeat or broaden local checks only for a concrete new change, failure or dependency. Full23-report/full6-followup remain original integration CI milestone selections, alongside all eight jobs and fresh exact-head integration review; they are not blanket local rerun requirements for every small correction. Synthetic unit wiring or copied-source import controls alone cannot prove installed OASIS, deadline fit or research quality. Preserve all existing gate selections, loopback/ownership controls, assertions and deadlines, including13 offline/14 serial fixture gates,990-second work cutoff,1000-second wrapper and20-minute native job. No acceptance shortcut, hosted-gain, release-readiness or semantic/research-quality claim is made here. All writers must be stable and source newly frozen before Main executes product qualification.
