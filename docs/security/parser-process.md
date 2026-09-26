# One-shot parser process

`extract_text_isolated(file_path, *, limits=None, timeout_seconds=30)`
launches a fixed sibling worker script with the current Python interpreter
(`-I -u`). It is an independently callable utility. Flask routes do not use
it yet. The caller must still admit the source under a trusted root.

The parent validates the path, `ParseLimits`, a finite timeout in
`(0, 120]`, and the at most 16 KiB UTF-8 JSON request before spawning.
It passes an absolute path without a shell. The worker receives one
EOF-terminated version 1 JSON object with exactly `version`,
`file_path`, and the five limits. It rejects duplicate keys, unknown
fields, invalid types, and malformed or oversized requests. The worker
loads only the fixed sibling `file_parser.py` through `importlib`;
it does not import application startup, Flask, configuration, dotenv,
or model code.

Each launch has a unique temporary directory for its working directory,
home, and temporary files. The child receives an explicit minimal
environment. It receives no inherited credentials, provider settings,
proxy variables, Python path, or user home. Standard error goes to
`DEVNULL`; native diagnostics and document content are never returned.
The child sends exactly one UTF-8 JSON response with version 1 and
either text or a stable error code. Missing files use `missing_file`.
The parent rejects duplicate and unknown fields, invalid types, version
mismatch, unknown errors, extra output, invalid UTF-8, and text beyond
`max_text_chars`.

The parent collects no more than `max_text_chars * 6 + 4096` response
bytes. Separate request writer and bounded response reader threads avoid
pipe deadlock. One monotonic deadline covers transfer, response, and
child exit after spawn. On failure, timeout, output overflow, or
cancellation, the parent stops and reaps only its child and joins its
own I/O threads. It never retries automatically. The fixed executable
and script paths are not user-configurable; the private script seam
exists only for tests.

This boundary contains a parser crash, hang, and oversized response
from the application process. It is **not** an OS security sandbox,
network firewall, hard native memory cap, or hard native CPU cap.
PyMuPDF can allocate or perform work before producing bounded output.
A hostile native library that escapes its process, launches descendants,
or accesses the host filesystem or network is not contained by
environment scrubbing. Process creation and OS teardown are not claimed
as mathematically hard deadline operations. Route-level upload admission
and error mapping are separate follow-up work.

