"""Source-authored PDF profile tests. Main must install and require source-pdf.

No importorskip: absence of the optional profile fails this dedicated module.
"""
import hashlib
import sys
from types import SimpleNamespace
from uuid import uuid4

import pymupdf
import pytest

from nexaweave_storage.pdf import extract_pdf, PdfError


def pdf_bytes(pages):
    with pymupdf.open() as document:
        for text in pages:
            page = document.new_page()
            if text:
                # Built-in CJK font gives actual Unicode extraction without a
                # system font path or download. Tests compare extracted strings.
                page.insert_text((72, 72), text, fontname="china-s")
        return document.tobytes()


def test_actual_unicode_pages_empty_numbering_exact_offsets_and_ids():
    binary = pdf_bytes(["Beginning 猫", "", "Middle 雪", "End 中文"])
    with pymupdf.open(stream=binary, filetype="pdf") as document:
        expected = [page.get_text() for page in document]
    assert "猫" in expected[0] and "雪" in expected[2] and "中文" in expected[3]
    extracted = extract_pdf(binary)
    assert extracted.text == "\n\n".join(expected)
    assert extracted.page_count == 4 and extracted.empty_page_count == 1
    revision = str(uuid4())
    passages = extracted.declarations(revision)
    assert [item["page"] for item in passages] == [1, 3, 4]
    assert passages == extract_pdf(binary).declarations(revision)
    assert passages != extracted.declarations(str(uuid4()))
    for page, text in zip(extracted.pages, expected):
        assert extracted.text[page["start"]:page["end"]] == text
        assert page["excerpt_sha256"] == hashlib.sha256(text.encode()).hexdigest()


@pytest.mark.parametrize("binary", [b"", b"secret path", b"%PDF-1.7\n%%EOF", b"%PDF-1.7", b"x" * (2 * 1024 * 1024 + 1)],
    ids=["empty", "non_pdf", "malformed", "truncated", "over_input_limit"])
def test_malformed_truncated_and_input_limit(binary):
    with pytest.raises(PdfError) as error:
        extract_pdf(binary)
    assert str(error.value) == "invalid_request"


def test_actual_textless_and_page_limit():
    for binary in [pdf_bytes([""]), pdf_bytes(["x"] * 101)]:
        with pytest.raises(PdfError):
            extract_pdf(binary)


def test_actual_image_only_page_is_rejected_without_ocr():
    with pymupdf.open() as document:
        page = document.new_page()
        image = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 8, 8), False)
        image.clear_with(255)
        page.insert_image(pymupdf.Rect(72, 72, 144, 144), pixmap=image)
        binary = document.tobytes()
    with pytest.raises(PdfError):
        extract_pdf(binary)


def test_admission_rejects_bad_bytes_without_opening_native_parser(monkeypatch):
    def forbidden(**kwargs):
        pytest.fail("native parser reached before byte/header/EOF admission")
    monkeypatch.setattr(pymupdf, "open", forbidden)
    for binary in [b"not a PDF", b"%PDF-1.7", b"%PDF-" + b"x" * (2 * 1024 * 1024)]:
        with pytest.raises(PdfError):
            extract_pdf(binary)


def test_actual_encrypted_rejected():
    with pymupdf.open() as document:
        document.new_page().insert_text((72, 72), "secret")
        binary = document.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256,
            owner_pw="owner-secret", user_pw="user-secret")
    with pytest.raises(PdfError) as error:
        extract_pdf(binary)
    assert str(error.value) == "invalid_request"


def repeated_text_pdf(page_count, repeats):
    # Every repeated text operator stays within the page rectangle. Overlapping
    # text is intentional: quotas apply to native extraction, not visual layout.
    with pymupdf.open() as document:
        for _ in range(page_count):
            page = document.new_page()
            for _ in range(repeats):
                page.insert_text((72, 72), "A" * 80, fontsize=5)
        return document.tobytes(deflate=True, garbage=4)


def test_actual_native_page_and_total_text_quotas():
    from nexaweave_storage.pdf import MAX_INPUT_BYTES, MAX_PAGE_BYTES, MAX_TEXT_BYTES
    page_binary = repeated_text_pdf(1, 420)
    total_binary = repeated_text_pdf(36, 380)
    assert len(page_binary) <= MAX_INPUT_BYTES and len(total_binary) <= MAX_INPUT_BYTES
    with pymupdf.open(stream=page_binary, filetype="pdf") as document:
        assert len(document[0].get_text().encode()) > MAX_PAGE_BYTES
    with pymupdf.open(stream=total_binary, filetype="pdf") as document:
        texts = [page.get_text() for page in document]
    assert all(len(text.encode()) <= MAX_PAGE_BYTES for text in texts)
    assert len("\n\n".join(texts).encode()) > MAX_TEXT_BYTES
    for binary in (page_binary, total_binary):
        with pytest.raises(PdfError):
            extract_pdf(binary)


def test_native_close_failure_prevents_success_and_is_sanitized(monkeypatch):
    class Document:
        needs_pass = is_encrypted = is_repaired = False
        def __len__(self): return 1
        def __getitem__(self, index): return SimpleNamespace(get_text=lambda: "text")
        def close(self): raise RuntimeError("private native path")
    monkeypatch.setattr(pymupdf, "open", lambda **kwargs: Document())
    with pytest.raises(PdfError) as error:
        extract_pdf(b"%PDF-1.7\n%%EOF")
    assert str(error.value) == "source_unavailable"


def test_native_cleanup_on_success_failure_and_incremental_quotas(monkeypatch):
    class Document:
        needs_pass = is_encrypted = is_repaired = False
        closed = False
        def __init__(self, pages): self.pages = pages
        def __len__(self): return len(self.pages)
        def __getitem__(self, index):
            text = self.pages[index]
            def get_text():
                if isinstance(text, Exception): raise text
                return text
            return SimpleNamespace(get_text=get_text)
        def close(self): self.closed = True
    for pages, failure in [(["猫"], False), ([RuntimeError("private path")], True),
            (["x" * 32769], True), (["x" * 32768] * 33, True), (["\x00"], True)]:
        document = Document(pages)
        monkeypatch.setattr(pymupdf, "open", lambda **kwargs: document)
        if failure:
            with pytest.raises(PdfError) as error:
                extract_pdf(b"%PDF-1.7\n%%EOF")
            assert "private" not in str(error.value)
        else:
            assert extract_pdf(b"%PDF-1.7\n%%EOF").text == "猫"
        assert document.closed


def test_whitespace_pages_retain_exact_text_without_empty_evidence(monkeypatch):
    class Document:
        needs_pass = is_encrypted = is_repaired = False
        closed = False
        def __len__(self): return 3
        def __getitem__(self, index):
            return SimpleNamespace(get_text=lambda: [" \t\n", "猫\r\n", "\n"][index])
        def close(self): self.closed = True
    document = Document()
    monkeypatch.setattr(pymupdf, "open", lambda **kwargs: document)
    extracted = extract_pdf(b"%PDF-1.7\n%%EOF")
    assert extracted.text == " \t\n\n\n猫\r\n\n\n\n"
    assert extracted.empty_page_count == 2
    assert [item["page"] for item in extracted.declarations(str(uuid4()))] == [2]
    assert document.closed


def test_missing_profile_is_fixed_unavailable(monkeypatch):
    monkeypatch.setitem(sys.modules, "pymupdf", None)
    with pytest.raises(PdfError) as error:
        extract_pdf(b"%PDF-1.7\n%%EOF")
    assert str(error.value) == "source_unavailable"
