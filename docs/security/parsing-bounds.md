# Parsing bounds

`FileParser` accepts optional `ParseLimits` on `extract_text`,
`extract_from_multiple`, and its format-specific extractors. The frozen
defaults are 50 MiB per source file, 5,000,000 extracted characters per
file, 500 PDF pages, 20 files per combined extraction, and 5,000,000
characters in the combined result. Custom values must be positive integers;
booleans, floats, and nonpositive values are rejected. Text chunking accepts
at most 5,000,000 input characters and emits at most 100,000 chunks.

The source reader checks that the path names a regular file without following
symlinks or Windows reparse points, then reads at most the byte limit plus
one. It rechecks the opened descriptor. This detects a source that grows
past the limit while it is read. A caller must still restrict sources to a
trusted root. The utility cannot rule out local path substitution races on all
platforms, hardlinks to files outside that root, or another process modifying
an open file. The upload admission layer belongs to the next packet.

Text encoding is detected only after the bounded read. UTF-8 and UTF BOMs
are handled directly; legacy encodings use the existing detector fallback.
Legacy encoding detection is heuristic: a short byte sequence can match
multiple encodings, so automatic selection is not guaranteed to reproduce
the author's intended characters. The tests select cp1252 explicitly when
exercising each legacy detector path.
Decoded NUL and obvious binary control characters are rejected. Valid
newline, carriage return, tab, and Unicode text remain accepted. No text is
silently shortened to meet a limit.

PDF input needs a PDF header in its first 1024 bytes. Encrypted documents,
invalid documents, and documents over the page limit are rejected. Extracted
page text is counted with join separators before it is appended. An empty or
scanned PDF produces empty text; OCR is not part of this utility. The PDF
document is closed after both success and parser failure.

Errors expose stable categories: `limit_exceeded`,
`unsupported_document`, `malformed_document`, and `invalid_source`.
Missing files retain `FileNotFoundError` with a static message. Combined
extraction uses at most 80 characters of a sanitized basename and static
error codes in per-file markers; it never includes raw paths or underlying
exception messages. Limit failures abort the combined extraction.

These are in-process input and output bounds. PyMuPDF can still spend
substantial CPU or memory while opening a bounded PDF or decoding one page,
and a single page's extracted text exists briefly before the character
limit can be checked. They are not a hard memory or time cap. Parser
subprocess isolation and route-level aggregate upload admission are separate
follow-up work.
