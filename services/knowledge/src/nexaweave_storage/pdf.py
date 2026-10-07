"""Bounded transient textual PDF extraction; native import only on explicit use.

This module does not retain original documents, render pages or perform OCR.
The caller must run extraction in the fixed owned source child after admission.
"""
from dataclasses import dataclass
import hashlib
import json
from uuid import UUID, uuid5

MAX_INPUT_BYTES = 2 * 1024 * 1024
MAX_TEXT_BYTES = 1024 * 1024
MAX_PAGE_BYTES = 32768
MAX_PAGES = 100


class PdfError(ValueError):
    def __init__(self, code="invalid_request"):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class PdfExtraction:
    text: str
    pages: tuple[dict, ...]
    page_count: int
    empty_page_count: int

    def declarations(self, revision):
        namespace = UUID(revision)
        if str(namespace) != revision:
            raise ValueError
        result = []
        for page in self.pages:
            if page["empty"]:
                continue
            identity = "pdf-page-text-v1:" + json.dumps(page, sort_keys=True,
                ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            result.append({"evidence_id": str(uuid5(namespace, identity)),
                "start": page["start"], "end": page["end"], "page": page["page"]})
        return result


def extract_pdf(binary):
    """Extract exact page.get_text() strings joined in page order by two newlines.

    Empty and whitespace-only pages keep their positions in the joined text,
    but have no passage. A page must fit the existing single-passage quota.
    Byte quotas bound retained output, not native allocator memory.
    """
    if (type(binary) is not bytes or not 0 < len(binary) <= MAX_INPUT_BYTES
            or b"%PDF-" not in binary[:1024] or not binary.rstrip().endswith(b"%%EOF")):
        raise PdfError()
    try:
        import pymupdf
    except ImportError:
        raise PdfError("source_unavailable") from None
    document = None
    try:
        document = pymupdf.open(stream=binary, filetype="pdf")
        # Repair is not proof of an intact original document. Reject it outright.
        if document.needs_pass or document.is_encrypted or document.is_repaired:
            raise PdfError()
        count = len(document)
        if not 1 <= count <= MAX_PAGES:
            raise PdfError()
        parts, pages = [], []
        size, offset, empty_count = 0, 0, 0
        for ordinal in range(count):
            text = document[ordinal].get_text()
            if type(text) is not str:
                raise PdfError()
            data = text.encode("utf-8")
            if (len(data) > MAX_PAGE_BYTES
                    or any((ord(c) < 32 and c not in "\t\n\r") or ord(c) == 127 for c in text)):
                raise PdfError()
            separator = 2 if ordinal else 0
            size += separator + len(data)
            offset += separator
            if size > MAX_TEXT_BYTES:
                raise PdfError()
            empty = not text.strip()
            empty_count += int(empty)
            pages.append({"page": ordinal + 1, "start": offset, "end": offset + len(text),
                "empty": empty, "excerpt_sha256": hashlib.sha256(data).hexdigest()})
            parts.append(text)
            offset += len(text)
        if empty_count == count or document.is_repaired:
            raise PdfError()
        return PdfExtraction("\n\n".join(parts), tuple(pages), count, empty_count)
    except PdfError:
        raise
    except Exception:
        raise PdfError() from None
    finally:
        if document is not None:
            try:
                document.close()
            except Exception:
                # No result survives native cleanup failure.
                raise PdfError("source_unavailable") from None
