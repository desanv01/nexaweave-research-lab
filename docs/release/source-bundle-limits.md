# Fixed source bundle limits and qualification gaps

This mechanical U14a slice is an unqualified source artifact. It is not a release
certification, installer, binary distribution, C41 research-data export, complete
SBOM, signature/attestation, or acceptance of U14/all 44 capabilities. It performs
no tagging, publishing, public deployment or paid calls. Main owns every audit,
test, actual repository execution and acceptance decision.

| Admission | Fixed bound |
| --- | --- |
| Source files | 4,096 regular Git blobs, modes 100644/100755 only |
| Source file bytes | 16 MiB per blob; 128 MiB aggregate |
| Names | 1,024 UTF-8 bytes, at most 64 slash-separated components |
| Generated JSON | 8 MiB per document |
| Complete ZIP | 148 MiB |
| Git output | 2 MiB tree/status; 4 KiB root; 128 bytes revision/tree; declared blob size |
| Git duration | 15 seconds per command; 300 seconds total Git collection window |
| Inventory packages | 8,192 across all three locks |
| Artifact hash records | 32,768 aggregate; at most 4,096 wheels per uv package |
| Inventory fields | 256 bytes for names/versions; 1,024 for integrity/package keys; 2,048 characters for locators |

Memory use is finite but can exceed artifact size because source bytes, metadata,
ZIP input/output and canonical verification copies coexist. Parsing and canonical
verification consume bounded inputs but are not OS-level CPU/memory isolation.
Build may reject a large otherwise-valid repository rather than relax limits.
Python 3.11+ `tomllib` is required; only uv version 1 and npm lock versions 2/3 with
explicit package versions are supported. Inventory records declaration, not a
platform-selected installation. Artifact hashes do not establish license status.

Paths reject absolute/dot/empty/backslash/colon/control components, Windows device
names and trailing dots/spaces, casefold duplicates, file/ancestor collisions and
reserved generated paths. Submodules, symlinks and special Git modes are rejected.
Known secret/generated paths include `.env`/`.env.*` except `.env.example`, key
files, databases/logs, model-weight suffixes, `.git`, installed environments,
caches, uploads/data/test-results and dist/coverage directories. This is a fixed
path admission policy, not a complete content secret scan. Secrets inside admitted
source or lock bytes remain included. Main must audit the exact committed content
before packaging or any distribution decision.

Unsafe credential/query/fragment-bearing source locators are omitted from the
generated inventory, with omission reasons. Their original lock bytes remain in
the ZIP. No account credential, remote URL or installed package is looked up by
Git or an external package manager. Git runs fixed argv without shell/textconv/
external diff, scrubs inherited Git environment overrides and global/system
configuration, disables fsmonitor/hooks/credential helpers and optional locks.
Repository-local configuration and local Git executable remain within the caller's
trusted repository/tool boundary. There is no hostile-repository sandbox.

Caller-supplied repository, artifact and output directory ancestors must be
canonical, existing and free of links/reparse points. The new output is exclusive;
verify opens without following a final symlink where the OS supports it. These
checks assume the trusted directory ancestry is not concurrently replaced by a
hostile process; they are not an OS-wide filesystem race/security boundary. Owned
failure cleanup uses device/inode identity and never recursively deletes paths.

The source revision is a supplied exact commit identity matched to build HEAD.
Verify checks the manifest's matching revision and reconstructs its source Git
tree from blobs. It has no commit object, signature, remote publisher proof or
Git history, and cannot establish independent commit authenticity. The trusted
whole ZIP SHA-256 is the verification trust anchor.

The fixed original archive SHA plus 128-entry manifest and patch comparisons
match Main's baseline semantics. Manifest/patch author authenticity and review
status are not certified, and the original archive commit remains unknown. Tests
use synthetic manifests bearing that hardcoded identity solely to exercise the
comparison rules. Only Main's separate actual repository/archive evidence can
qualify preservation of the reviewed real baseline.

The worker authored tests without executing them. POSIX-only names, executable
mode/case collisions and symlink fixtures require Main's hosted Linux checks;
Windows privilege skips do not qualify those cases. Architecture, provider defaults,
separate embeddings and inherited notices remain outside this packet's edits.
