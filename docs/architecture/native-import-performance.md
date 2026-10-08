# Native optional dependency import correction

The native runtime retains `camel-oasis==0.2.5` and all locked dependencies. A bounded installer modifies only the installed `oasis/social_platform/recsys.py` so its optional SentenceTransformer dependency is resolved when requested rather than on every module import. This is a modified vendor source file; it is not an unmodified upstream installation.

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

Only that before fingerprint or the installer's deterministic after fingerprint is accepted. Unexpected version/path/content refuses before mutation. Application is idempotent, writes the transformed installed target atomically and invalidates only the verified cache for that module. `--check` verifies the required patched fingerprint without applying a change. Retain the emitted nonsecret before/after hashes and status in qualification evidence; the after hash must match the reviewed installer's canonical value, not a manually accepted hash from arbitrary installed bytes.

Preserve the exact original bytes privately before Main applies the installer to a local environment. Keep the original license text and distribution version/metadata. Do not upload local vendor backups or private diagnostics to the public repository. The installer and emitted vendor before/after fingerprint receipts provide the reproducible transformation. `docs/upstream/patches.json` retains its65 original baseline entries unchanged; this vendor transformation is not an additional entry in that ledger.

Reinstallation or an environment rebuild can restore the original upstream file. Run `--apply`, then `--check` again after the locked sync/install and before native qualification. A previous successful check does not qualify a replacement environment. CI applies and verifies with the locked backend interpreter after its installation step.

## Behavior preserved and changed

The transformation removes the one eager top-level SentenceTransformer import. The existing MiniLM constructor branch obtains the actual `sentence_transformers.SentenceTransformer` class on first use with the original constructor arguments, device, cache behavior and exception chain. The getter honors an existing module-global injected class and stores the actual resolved class. Explicit `recsys.SentenceTransformer` access or from-import resolves the real class through the narrowly scoped module attribute hook; unknown attributes raise AttributeError. There is no proxy class or fake module.

Recommender calculations, random/seed behavior, Torch, sklearn, TWHIN branches, source algorithms, authority checks and budgets/caps are unchanged. No dependency is removed, replaced or upgraded. The intentional behavior change is import timing: a missing or failing optional SentenceTransformer dependency is raised when that dependency is actually requested, rather than during an unrelated recsys import. Explicit access and actual constructor failure still expose the dependency's real error.

## Measurement and qualification

The retained cold probe observed a23.672-second loader and14.882-second cumulative SentenceTransformers import chain. Cumulative import measurements are nested; they are not additive, and removing an eager import does not establish that either interval is saved. This correction remains a candidate until Main measures the patched installed runtime.

Main qualification requires actual upstream fingerprint/idempotence/refusal checks, cold real-class resolution and constructor-branch/argument/error checks, the real native64 and PostgreSQL native-store29 selections, and the unchanged full eight-job CI review. Synthetic unit wiring alone cannot prove the actual OASIS installation or research quality. Preserve all existing gate selections, loopback/ownership controls, assertions and deadlines, including13 offline/14 serial fixture gates,990-second work cutoff,1000-second wrapper and20-minute native job. Do not claim saved seconds, CI acceptance, release readiness or semantic/research quality from the patch or cold probe alone.
