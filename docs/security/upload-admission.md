# Ontology upload admission

`POST /api/graph/ontology/generate` validates scalar fields and every file
member before creating a project. A requirement is mandatory and limited to
20,000 characters. Project name defaults to `Unnamed Project` and is limited
to 200 characters. Optional context is limited to 20,000 characters.
Duplicate scalar occurrences are rejected. The route accepts one to 20
PDF, Markdown, or TXT files with Unicode basenames of at most 255 characters.
Empty names, path components, controls, and unsupported extensions are
rejected as a whole batch. No supplied field, filename, or source text is
logged.

If a malformed multipart file part is parsed as an ordinary `files` form
field, the entire batch is rejected even when other file parts are valid.

The inherited Flask `MAX_CONTENT_LENGTH` is 50 MiB for the complete
HTTP request. The route maps `RequestEntityTooLarge` to a safe 413
response. The saver independently reads each upload stream in chunks,
never more than its configured byte limit plus one. It does not rely on
filename, MIME, or content length headers. It creates a server-named file
exclusively and removes only its own new partial file on a write or size
failure. The route counts actual saved bytes against a 50 MiB batch cap.

After admission, a process-local nonblocking two-slot semaphore covers
project creation, saving, isolated extraction, and combined text storage.
A busy process responds 503 before creating a project. The phase has a
90-second monotonic deadline; each child parser receives at most 30 seconds
and no more than the remaining phase time. The slot is released before
ontology model generation and on failures or cancellation.

The phase deadline is checked again after combined-text storage returns,
so late storage completion does not start model generation.

The route uses `extract_text_isolated` for each saved source. It preserves
`TextProcessor.preprocess_text`. Every file's preprocessed text and its
label/separators count toward the 5,000,000-character combined bound before
the combined result is saved. A wholly blank or scanned batch responds
422 with `no_extractable_text`; it receives no fabricated OCR. Parse
failures prevent combined text storage and model construction. A created
project remains `FAILED` with a safe error code/message and its ID in the
response; successfully saved original uploads remain attached for explicit
later deletion. Existing provider error behavior after generation begins
is preserved.

If cancellation interrupts saving or extraction after project creation,
the route records `upload_cancelled` and `FAILED` where persistence is
possible, releases its slot, and propagates the cancellation. Saved
originals remain attached; the saver removes only its own partial file.

Malformed admission is 400; numeric field, file, byte, page, or aggregate
limits are 413. Invalid source or malformed parsing is 422, parser
timeout is 504, and worker failure, protocol failure, or missing source is
503. Responses use fixed error messages and codes without path, document
text, native diagnostics, or traceback.

The multipart parser may spool request data before route-level field
validation. Request rate, concurrent work across multiple server processes,
and shared host disk pressure need separate controls. The semaphore is not
distributed. Local path checks and exclusive creation assume a trusted
storage root without an active local attacker racing path replacement;
they are not an `openat` or OS sandbox guarantee. The deadline does not
hard-preempt blocked OS file I/O or cleanup. Native parser work remains
bounded by the child-process contract rather than a hard memory cap.

If combined-text storage completes after the phase deadline, the project is
marked FAILED and model generation does not start. That already-written text
may remain alongside originals; the deadline does not roll back completed IO.
