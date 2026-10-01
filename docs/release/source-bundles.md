# Unqualified committed source bundles

`tools/build_source_release.py` builds a deterministic source ZIP or verifies an
existing ZIP without extraction. Python 3.11+ and local Git are needed for build;
verify uses only the Python standard library. No application, dependency manager,
archived workflow, model provider or database is run.

Main must first review the exact commit, inherited notices, provenance and source
contents. The tool does not grant acceptance or permission to distribute the
inherited baseline. Preserve the original archive and unrelated research.

Use an explicit canonical absolute repository root, full lowercase 40-hex HEAD
revision, and an absolute new output filename in an existing trusted directory.
For example, Main replaces the placeholders below with trusted reviewed values:

```powershell
python tools/build_source_release.py build --repository C:/trusted/repository --revision FULL_LOWERCASE_40_HEX --output C:/trusted/artifacts/source.zip
python tools/build_source_release.py verify --artifact C:/trusted/artifacts/source.zip --revision FULL_LOWERCASE_40_HEX --expected-sha256 TRUSTED_LOWERCASE_64_HEX
```

The successful build prints the artifact SHA-256 externally. Retain that hash
through a trusted channel and supply it explicitly to verify. Neither operation
has a HEAD fallback, publication command or qualified-release switch. Every CLI
invocation reports one bounded JSON line with exit 0 for success or 2 for failure;
argument errors use fixed codes without echoing supplied values. CLI help switches
are deliberately absent; this document is the usage reference.

Build rejects tracked changes and mismatched HEAD before collecting committed
objects, and checks them again before output. It exports the complete admitted
tree using bounded read-only Git calls, including linked-worktree support through
Git itself. No `.git` copying or caller configuration parsing is involved.
Untracked and ignored material is excluded and expressly unaudited. The output
must not exist: creation uses exclusive admission and failure cleanup targets
only a regular file created by that invocation. Directory trees are never deleted.

All admitted committed source, tests, docs and lock bytes are retained. The fixed
128-entry original archive SHA identity, imported per-file hashes and recorded
original/current patch hashes are checked using the baseline checker's comparison
semantics. This preserves the inherited LICENSE, README/ignore references and
inert workflow references. It does not authenticate who reviewed a patch, determine
the original archive commit, or establish a licensing conclusion.

The ZIP adds `.mirofish-release/manifest.json` and
`.mirofish-release/dependency-inventory.json`. The manifest records the exact
revision, reconstructed Git tree ID, source paths/modes/sizes/SHA-256 values,
counts, total bytes and inventory digest. Both unqualified flags remain false.
ZIP members have fixed timestamps, sorted order, Unix regular-file mode metadata
and stored compression. Metadata contains no creation time, host identity,
absolute checkout path or test-pass assertion. The artifact does not contain its
own ZIP digest.

The inventory comes from the two committed uv TOML locks and npm package-lock
JSON. It reports declared package identities, lock digests, safe source locators
and present artifact hashes, with `license=NOASSERTION` and installed-platform
status `unknown`. Unsafe/unbounded optional locators are omitted with an explicit
reason. Original locks are still byte-preserved. This inventory does not resolve
active dependency closure, inspect installed packages, download dependencies or
assert a complete SBOM.

Verify checks the trusted whole-artifact digest, entry admission, CRC, exact
generated schemas/bytes, required notice/provenance/locks, baseline/patch hashes,
inventory digest, per-file data/modes, and reconstructed Git tree ID. It regenerates
the canonical ZIP in memory and compares every byte, rejecting extra entries,
comments, prefixes, trailing data, altered ordering and metadata. It does not
extract files, invoke Git, contact a network or execute packaged source. A matching
trusted hash establishes matching bytes, not publisher authenticity.

Main's proposed focused test command, to be run only after source review:

```powershell
python -m unittest discover -s tests -p test_source_release.py
```

Tests author local temporary Git repositories and commits only when Main executes
them. Their 128-entry baselines are synthetic and do not prove original-archive
identity. Main must separately build and verify the actual reviewed repository
revision and retain exact check evidence. See [fixed limits](source-bundle-limits.md).
