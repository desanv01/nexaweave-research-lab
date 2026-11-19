"""Contract tests for bounded file extraction and chunking. Main runs these."""

import codecs
import os
import sys
import types
from dataclasses import FrozenInstanceError

import pytest

from app.utils import file_parser as parser_module
from app.utils.file_parser import (
    FileParser,
    InvalidSourceError,
    MalformedDocumentError,
    ParseLimitError,
    ParseLimits,
    UnsupportedDocumentError,
    split_text_into_chunks,
)


@pytest.mark.parametrize("field", [
    "max_file_bytes", "max_text_chars", "max_pdf_pages", "max_files",
    "max_aggregate_chars",
])
@pytest.mark.parametrize("bad", [True, False, 0, -1, 1.2, None, "10"])
def test_limits_reject_invalid_values(field, bad):
    with pytest.raises(ValueError):
        ParseLimits(**{field: bad})


def test_limits_are_frozen():
    limits = ParseLimits()
    with pytest.raises(FrozenInstanceError):
        limits.max_files = 1


@pytest.mark.parametrize("payload,expected", [
    ("café\n".encode("utf-8"), "café\n"),
    (codecs.BOM_UTF8 + "雪\n".encode("utf-8"), "雪\n"),
    ("雪\n".encode("utf-16"), "雪\n"),
])
def test_text_encodings(tmp_path, payload, expected):
    source = tmp_path / "source.md"
    source.write_bytes(payload)
    assert FileParser.extract_text(source) == expected


def test_legacy_charset_normalizer_selection(tmp_path, monkeypatch):
    source = tmp_path / "legacy.txt"
    source.write_bytes("café\n".encode("cp1252"))
    detected = types.SimpleNamespace(encoding="cp1252")
    monkeypatch.setitem(
        sys.modules,
        "charset_normalizer",
        types.SimpleNamespace(from_bytes=lambda _data: types.SimpleNamespace(best=lambda: detected)),
    )
    assert FileParser.extract_text(source) == "café\n"


def test_legacy_chardet_fallback_selection(tmp_path, monkeypatch):
    source = tmp_path / "legacy.txt"
    source.write_bytes("café\n".encode("cp1252"))

    def unavailable(_data):
        raise RuntimeError("detector unavailable")

    monkeypatch.setitem(
        sys.modules,
        "charset_normalizer",
        types.SimpleNamespace(from_bytes=unavailable),
    )
    monkeypatch.setitem(
        sys.modules,
        "chardet",
        types.SimpleNamespace(detect=lambda _data: {"encoding": "cp1252"}),
    )
    assert FileParser.extract_text(source) == "café\n"


def test_text_limits_and_binary_rejection(tmp_path):
    source = tmp_path / "source.txt"
    source.write_bytes(b"abcdef")
    exact = ParseLimits(max_file_bytes=6, max_text_chars=6)
    assert FileParser.extract_text(source, limits=exact) == "abcdef"
    with pytest.raises(ParseLimitError):
        FileParser.extract_text(source, limits=ParseLimits(max_file_bytes=5))
    with pytest.raises(ParseLimitError):
        FileParser.extract_text(source, limits=ParseLimits(max_text_chars=5))
    source.write_bytes(b"a\x00b")
    with pytest.raises(MalformedDocumentError):
        FileParser.extract_text(source)


def test_read_detects_growth_after_lstat(tmp_path, monkeypatch):
    source = tmp_path / "growing.txt"
    source.write_bytes(b"abcd")
    real_lstat = parser_module.os.lstat

    def grow_after_stat(path):
        result = real_lstat(path)
        if os.fspath(path) == os.fspath(source):
            source.write_bytes(b"abcdef")
        return result

    monkeypatch.setattr(parser_module.os, "lstat", grow_after_stat)
    with pytest.raises(ParseLimitError):
        FileParser.extract_text(source, limits=ParseLimits(max_file_bytes=4))


def test_missing_unsupported_and_nonregular_are_safe(tmp_path):
    missing = tmp_path / "private" / "secret.txt"
    with pytest.raises(FileNotFoundError) as caught:
        FileParser.extract_text(missing)
    assert str(missing) not in str(caught.value)

    unsupported = tmp_path / "secret.exe"
    unsupported.write_bytes(b"x")
    with pytest.raises(UnsupportedDocumentError) as caught:
        FileParser.extract_text(unsupported)
    assert str(unsupported) not in str(caught.value)


def test_directory_and_link_rejected(tmp_path):
    directory = tmp_path / "folder.txt"
    directory.mkdir()
    with pytest.raises(InvalidSourceError):
        FileParser.extract_text(directory)

    target = tmp_path / "target.txt"
    target.write_text("private", encoding="utf-8")
    link = tmp_path / "link.txt"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        if os.name == "nt":
            pytest.skip("Windows symlinks unavailable")
        raise
    with pytest.raises(InvalidSourceError):
        FileParser.extract_text(link)


def test_fifo_rejected_without_opening(tmp_path):
    if not hasattr(os, "mkfifo"):
        if os.name == "nt":
            pytest.skip("Windows FIFO unavailable")
        pytest.fail("FIFO support unexpectedly unavailable")
    pipe = tmp_path / "pipe.txt"
    try:
        os.mkfifo(pipe)
    except OSError:
        if os.name == "nt":
            pytest.skip("Windows FIFO creation unavailable")
        raise
    with pytest.raises(InvalidSourceError):
        FileParser.extract_text(pipe)


def _scanned_pdf_fixture(samples, content):
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 432 216]"
        b" /Resources << /XObject << /Im0 4 0 R >> >> /Contents 5 0 R >>",
        (b"<< /Type /XObject /Subtype /Image /Width 1200 /Height 600"
         b" /ColorSpace /DeviceGray /BitsPerComponent 8 /Length %d >>\nstream\n"
         % len(samples)) + samples + b"\nendstream",
        (b"<< /Length %d >>\nstream\n" % len(content)) + content + b"\nendstream",
    ]
    pdf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{number} 0 obj\n".encode("ascii"))
        pdf.extend(body)
        pdf.extend(b"\nendobj\n")
    xref_offset = len(pdf)
    pdf.extend(b"xref\n0 6\n0000000000 65535 f \n")
    for offset in offsets:
        pdf.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    pdf.extend(f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode("ascii"))
    return bytes(pdf)


def test_scanned_pdf_preflight_admits_exact_gray_samples_and_checks_bounds(tmp_path, monkeypatch):
    import fitz
    import pymupdf

    # Render the two fixture lines separately. Only the resulting pixels go
    # into the final PDF.
    with fitz.open() as staging:
        page = staging.new_page(width=432, height=216)
        page.insert_text((36, 80), "PUBLIC LIBRARY NOTICE", fontsize=24)
        page.insert_text((36, 135), "MEETING AT 10 AM", fontsize=24)
        pixmap = page.get_pixmap(dpi=200, colorspace=fitz.csGRAY, alpha=False)
        assert (pixmap.width, pixmap.height, pixmap.n) == (1200, 600, 1)
        samples = pixmap.samples
    assert len(samples) == 720_000

    content = b"q 432 0 0 216 0 0 cm /Im0 Do Q\n"
    pdf = _scanned_pdf_fixture(samples, content)
    limits = ParseLimits(max_file_bytes=len(pdf), max_pdf_pages=1)

    def forbid_native_access(*_args, **_kwargs):
        pytest.fail("scanned PDF preflight called a native PDF or image entry point")

    with monkeypatch.context() as guard:
        for module in (fitz, pymupdf):
            for entrypoint in ("open", "Document", "Pixmap"):
                guard.setattr(module, entrypoint, forbid_native_access)

        image = parser_module._preflight_scanned_pdf(pdf, limits=limits)
        assert (image.width, image.height, image.length) == (1200, 600, len(samples))
        assert image.offset == pdf.index(b"\nstream\n") + len(b"\nstream\n")
        assert pdf[image.offset:image.offset + image.length] == samples

        with pytest.raises(ParseLimitError):
            parser_module._preflight_scanned_pdf(pdf, limits=ParseLimits(max_file_bytes=len(pdf) - 1))
        with pytest.raises(ParseLimitError):
            parser_module._preflight_scanned_pdf(pdf.replace(b"/Width 1200", b"/Width 9200", 1))
        with pytest.raises(UnsupportedDocumentError):
            parser_module._preflight_scanned_pdf(_scanned_pdf_fixture(samples, content + b"q Q\n"))
        with pytest.raises(MalformedDocumentError):
            parser_module._preflight_scanned_pdf(pdf.replace(b"/Length 720000", b"/Length 720001", 1))
        with pytest.raises(MalformedDocumentError):
            parser_module._preflight_scanned_pdf(pdf.replace(b"0000000000 65535 f", b"0000000001 65535 f", 1))

    source = tmp_path / "image-only.pdf"
    source.write_bytes(pdf)
    assert FileParser.extract_text(source, limits=limits) == ""


def test_pdf_header_and_valid_fixtures(tmp_path):
    import fitz
    malformed = tmp_path / "bad.pdf"
    malformed.write_bytes(b"not a PDF")
    with pytest.raises(MalformedDocumentError):
        FileParser.extract_text(malformed)

    source = tmp_path / "tiny.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "hello")
    doc.save(source)
    doc.close()
    extracted = FileParser.extract_text(source)
    assert "hello" in extracted
    assert FileParser.extract_text(
        source, limits=ParseLimits(max_text_chars=len(extracted))
    ) == extracted
    with pytest.raises(ParseLimitError):
        FileParser.extract_text(source, limits=ParseLimits(max_text_chars=2))

    blank = tmp_path / "blank.pdf"
    doc = fitz.open()
    doc.new_page()
    doc.save(blank)
    doc.close()
    assert FileParser.extract_text(blank) == ""


def test_pdf_page_limit_and_encryption(tmp_path):
    import fitz
    source = tmp_path / "two.pdf"
    doc = fitz.open()
    doc.new_page()
    doc.new_page()
    doc.save(source)
    doc.close()
    assert FileParser.extract_text(source, limits=ParseLimits(max_pdf_pages=2)) == ""
    with pytest.raises(ParseLimitError):
        FileParser.extract_text(source, limits=ParseLimits(max_pdf_pages=1))

    encrypted = tmp_path / "encrypted.pdf"
    doc = fitz.open()
    doc.new_page()
    doc.save(
        encrypted,
        encryption=fitz.PDF_ENCRYPT_AES_256,
        owner_pw="owner",
        user_pw="reader",
    )
    doc.close()
    with pytest.raises(MalformedDocumentError):
        FileParser.extract_text(encrypted)


def test_pdf_document_closes_when_page_extraction_fails(tmp_path, monkeypatch):
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-1.7\n")
    state = {"closed": False}

    class BadPage:
        def get_text(self):
            raise RuntimeError("private source content")

    class BadDocument:
        needs_pass = False
        is_encrypted = False

        def __len__(self):
            return 1

        def __iter__(self):
            return iter([BadPage()])

        def close(self):
            state["closed"] = True

    monkeypatch.setitem(sys.modules, "fitz", types.SimpleNamespace(open=lambda **_: BadDocument()))
    with pytest.raises(MalformedDocumentError) as caught:
        FileParser.extract_text(source)
    assert state["closed"]
    assert "private" not in str(caught.value)


def test_multiple_file_count_aggregate_and_private_errors(tmp_path):
    good = tmp_path / "good.txt"
    good.write_text("hello", encoding="utf-8")
    missing = tmp_path / "private" / "missing.txt"
    result = FileParser.extract_from_multiple([good, missing])
    assert "hello" in result and "missing_file" in result
    assert str(missing.parent) not in result
    single = FileParser.extract_from_multiple([good])
    assert FileParser.extract_from_multiple(
        [good], limits=ParseLimits(max_aggregate_chars=len(single))
    ) == single
    with pytest.raises(ParseLimitError):
        FileParser.extract_from_multiple([good, good], limits=ParseLimits(max_files=1))
    with pytest.raises(ParseLimitError):
        FileParser.extract_from_multiple(
            [good], limits=ParseLimits(max_aggregate_chars=len(single) - 1)
        )
    with pytest.raises(ParseLimitError):
        FileParser.extract_from_multiple([good], limits=ParseLimits(max_text_chars=4))


@pytest.mark.parametrize("size,overlap", [
    (0, 0), (True, 0), (1.2, 0), (4, -1), (4, 4), (4, True),
])
def test_invalid_chunk_parameters(size, overlap):
    with pytest.raises(ValueError):
        split_text_into_chunks("hello", size, overlap)


def test_chunking_progress_preserves_content_and_limits():
    text = "One. Two. Three! 雪。 End."
    chunks = split_text_into_chunks(text, chunk_size=10, overlap=8)
    assert chunks
    distinct = "".join(chr(0x4E00 + index) for index in range(40))
    distinct_chunks = split_text_into_chunks(distinct, chunk_size=10, overlap=8)
    assert all(char in "".join(distinct_chunks) for char in distinct)
    assert split_text_into_chunks("", 2, 1) == []
    assert split_text_into_chunks(" \n ", 2, 1) == []
    with pytest.raises(ParseLimitError):
        split_text_into_chunks("a" * 5_000_001)
    with pytest.raises(ParseLimitError):
        split_text_into_chunks("a" * 100_001, 1, 0)
