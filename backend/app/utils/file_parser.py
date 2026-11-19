"""Bounded text extraction for locally admitted PDF, Markdown, and TXT files."""

import codecs
import hashlib
from io import BytesIO
import os
import re
import stat
import tempfile
from dataclasses import dataclass, replace
from itertools import islice
from pathlib import Path
from typing import List, Optional


MAX_CHUNK_INPUT_CHARS = 5_000_000
MAX_EMITTED_CHUNKS = 100_000


class ParseError(ValueError):
    """A safe, classified error with no source path or content."""

    code = "parse_error"

    def __init__(self) -> None:
        super().__init__(self.code)


class ParseLimitError(ParseError):
    code = "limit_exceeded"


class UnsupportedDocumentError(ParseError):
    code = "unsupported_document"


class MalformedDocumentError(ParseError):
    code = "malformed_document"


class InvalidSourceError(ParseError):
    code = "invalid_source"


@dataclass(frozen=True)
class ParseLimits:
    max_file_bytes: int = 50 * 1024 * 1024
    max_text_chars: int = 5_000_000
    max_pdf_pages: int = 500
    max_files: int = 20
    max_aggregate_chars: int = 5_000_000

    def __post_init__(self) -> None:
        for value in (
            self.max_file_bytes,
            self.max_text_chars,
            self.max_pdf_pages,
            self.max_files,
            self.max_aggregate_chars,
        ):
            if type(value) is not int or value <= 0:
                raise ValueError("parse limits must be positive integers")


DEFAULT_LIMITS = ParseLimits()

_SCANNED_PDF_MAX_BYTES = 2 * 1024 * 1024
_SCANNED_PDF_MAX_AXIS = 4096
_SCANNED_PDF_MAX_PIXELS = 4_000_000
_SCANNED_PDF_DPI = 200
_SCANNED_PDF_HEADER = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"
_SCANNED_CATALOG = b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
_SCANNED_PAGES = b"2 0 obj\n<< /Type /Pages /Count 1 /Kids [3 0 R] >>\nendobj\n"
_SCANNED_PAGE = re.compile(
    rb"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox \[0 0 "
    rb"([1-9][0-9]{0,3}) ([1-9][0-9]{0,3})\]"
    rb" /Resources << /XObject << /Im0 4 0 R >> >> /Contents 5 0 R >>\nendobj\n"
)
_SCANNED_IMAGE_HEADER = re.compile(
    rb"4 0 obj\n<< /Type /XObject /Subtype /Image /Width ([1-9][0-9]{0,4})"
    rb" /Height ([1-9][0-9]{0,4}) /ColorSpace /DeviceGray /BitsPerComponent 8"
    rb" /Length ([1-9][0-9]{0,6}) >>\nstream\n"
)
_SCANNED_STREAM_END = b"\nendstream\nendobj\n"
_SCANNED_OCR_SIZE = (1200, 600)
_SCANNED_OCR_MAX_TEXT_BYTES = 32 * 1024
_SCANNED_OCR_MAX_PDF_BYTES = 2 * 1024 * 1024
_SCANNED_OCR_DATA_DIR = Path(__file__).with_name("ocr_tessdata")
_SCANNED_OCR_DATA_NAME = "eng.traineddata"
_SCANNED_OCR_DATA_SIZE = 4_113_088
_SCANNED_OCR_DATA_SHA256 = "7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2"


@dataclass(frozen=True)
class _ScannedPDFImage:
    width: int
    height: int
    offset: int
    length: int


def _expect_pdf_bytes(data: bytes, offset: int, expected: bytes) -> int:
    if not data.startswith(expected, offset):
        raise MalformedDocumentError()
    return offset + len(expected)


def _preflight_scanned_pdf(
    data: bytes, *, limits: Optional[ParseLimits] = None
) -> _ScannedPDFImage:
    """Validate the classic-xref grayscale scan profile without decoding a page."""
    if type(data) is not bytes:
        raise InvalidSourceError()
    resolved = _limits_or_default(limits)
    if len(data) > min(resolved.max_file_bytes, _SCANNED_PDF_MAX_BYTES):
        raise ParseLimitError()
    if not data.startswith(_SCANNED_PDF_HEADER):
        if data.startswith(b"%PDF-"):
            raise UnsupportedDocumentError()
        raise MalformedDocumentError()

    catalog_offset = len(_SCANNED_PDF_HEADER)
    pages_offset = _expect_pdf_bytes(data, catalog_offset, _SCANNED_CATALOG)
    page_offset = _expect_pdf_bytes(data, pages_offset, _SCANNED_PAGES)
    page = _SCANNED_PAGE.match(data, page_offset)
    if page is None:
        raise UnsupportedDocumentError()
    page_width, page_height = (int(part) for part in page.groups())

    image_offset = page.end()
    image = _SCANNED_IMAGE_HEADER.match(data, image_offset)
    if image is None:
        raise UnsupportedDocumentError()
    width, height, length = (int(part) for part in image.groups())
    if width > _SCANNED_PDF_MAX_AXIS or height > _SCANNED_PDF_MAX_AXIS:
        raise ParseLimitError()
    pixel_count = width * height
    if pixel_count > _SCANNED_PDF_MAX_PIXELS:
        raise ParseLimitError()
    if (width * 72 != page_width * _SCANNED_PDF_DPI
            or height * 72 != page_height * _SCANNED_PDF_DPI):
        raise UnsupportedDocumentError()
    if length != pixel_count or length > len(data) - image.end():
        raise MalformedDocumentError()
    samples_offset = image.end()
    content_offset = _expect_pdf_bytes(data, samples_offset + length, _SCANNED_STREAM_END)

    # The exact paint sequence excludes hidden text, inline images and forms.
    content = b"q %d 0 0 %d 0 0 cm /Im0 Do Q\n" % (page_width, page_height)
    content_object = (
        b"5 0 obj\n<< /Length %d >>\nstream\n" % len(content)
        + content + _SCANNED_STREAM_END
    )
    if not data.startswith(content_object, content_offset):
        raise UnsupportedDocumentError()

    xref_offset = content_offset + len(content_object)
    object_offsets = (catalog_offset, pages_offset, page_offset, image_offset, content_offset)
    xref = (
        b"xref\n0 6\n0000000000 65535 f \n"
        + b"".join(b"%010d 00000 n \n" % offset for offset in object_offsets)
        + b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % xref_offset
    )
    if _expect_pdf_bytes(data, xref_offset, xref) != len(data):
        raise MalformedDocumentError()
    return _ScannedPDFImage(width, height, samples_offset, length)


def _limits_or_default(limits: Optional[ParseLimits]) -> ParseLimits:
    if limits is None:
        return DEFAULT_LIMITS
    if not isinstance(limits, ParseLimits):
        raise TypeError("limits must be ParseLimits")
    return limits


def _bounded_source_bytes(file_path: str, limits: ParseLimits) -> bytes:
    """Reject special files and read at most one byte past the configured cap."""
    try:
        source_stat = os.lstat(file_path)
    except FileNotFoundError:
        raise FileNotFoundError("source file not found") from None
    except OSError:
        raise InvalidSourceError() from None

    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    attributes = getattr(source_stat, "st_file_attributes", 0)
    if not stat.S_ISREG(source_stat.st_mode) or (reparse_flag and attributes & reparse_flag):
        raise InvalidSourceError()
    if source_stat.st_size > limits.max_file_bytes:
        raise ParseLimitError()

    try:
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(file_path, flags)
        with os.fdopen(descriptor, "rb") as source:
            # A path can change between lstat and open.
            opened_stat = os.fstat(source.fileno())
            opened_attributes = getattr(opened_stat, "st_file_attributes", 0)
            if not stat.S_ISREG(opened_stat.st_mode) or (
                reparse_flag and opened_attributes & reparse_flag
            ):
                raise InvalidSourceError()
            data = source.read(limits.max_file_bytes + 1)
    except ParseError:
        raise
    except FileNotFoundError:
        raise FileNotFoundError("source file not found") from None
    except OSError:
        raise InvalidSourceError() from None

    if len(data) > limits.max_file_bytes:
        raise ParseLimitError()
    return data


def _ocr_data_file(details) -> bool:
    reparse = getattr(details, "st_file_attributes", 0) & getattr(
        stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0,
    )
    return (stat.S_ISREG(details.st_mode) and not reparse
            and details.st_nlink == 1 and details.st_size == _SCANNED_OCR_DATA_SIZE)


def _ocr_file_identity(details) -> tuple:
    return (details.st_dev, details.st_ino, details.st_size,
            details.st_mtime_ns, details.st_ctime_ns)


def _stage_scanned_ocr_data(destination: Path) -> None:
    """Expose only a verified, exclusively staged copy to the OCR library."""
    path = _SCANNED_OCR_DATA_DIR / _SCANNED_OCR_DATA_NAME
    for parent in path.parents:
        details = parent.lstat()
        reparse = getattr(details, "st_file_attributes", 0) & getattr(
            stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0,
        )
        if not stat.S_ISDIR(details.st_mode) or reparse:
            raise RuntimeError("OCR profile unavailable")
    before = path.lstat()
    if not _ocr_data_file(before):
        raise RuntimeError("OCR profile unavailable")

    flags = (os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
             | getattr(os, "O_NONBLOCK", 0))
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if (not _ocr_data_file(opened)
                or _ocr_file_identity(opened)[:3] != _ocr_file_identity(before)[:3]):
            raise RuntimeError("OCR profile unavailable")
        digest = hashlib.sha256()
        remaining = _SCANNED_OCR_DATA_SIZE
        with (destination / _SCANNED_OCR_DATA_NAME).open("xb") as staged:
            while remaining:
                chunk = os.read(descriptor, min(65536, remaining))
                if not chunk:
                    raise RuntimeError("OCR profile unavailable")
                digest.update(chunk)
                staged.write(chunk)
                remaining -= len(chunk)
            if os.read(descriptor, 1) or digest.hexdigest() != _SCANNED_OCR_DATA_SHA256:
                raise RuntimeError("OCR profile unavailable")
        after = os.fstat(descriptor)
        path_after = path.lstat()
        if (not _ocr_data_file(after) or not _ocr_data_file(path_after)
                or _ocr_file_identity(after) != _ocr_file_identity(opened)
                or _ocr_file_identity(path_after) != _ocr_file_identity(before)):
            raise RuntimeError("OCR profile unavailable")
    finally:
        os.close(descriptor)


class _CappedOCRPDF(BytesIO):
    """Cap retained OCR PDF bytes, including after native seek and truncate."""

    exceeded = False

    def _check_extent(self, end):
        if not 0 <= end <= _SCANNED_OCR_MAX_PDF_BYTES:
            self.exceeded = True
            raise ParseLimitError()

    def write(self, data):
        self._check_extent(self.tell() + len(data))
        return super().write(data)

    def seek(self, offset, whence=os.SEEK_SET):
        if whence == os.SEEK_CUR:
            offset += self.tell()
        elif whence == os.SEEK_END:
            with self.getbuffer() as buffer:
                offset += len(buffer)
        elif whence != os.SEEK_SET:
            raise ValueError("invalid OCR output seek")
        self._check_extent(offset)
        return super().seek(offset)

    def truncate(self, size=None):
        size = self.tell() if size is None else size
        self._check_extent(size)
        return super().truncate(size)


def _recognize_scanned_samples(samples: bytes, image: _ScannedPDFImage, tessdata: Path) -> str:
    import pymupdf
    if pymupdf.VersionBind != "1.26.7":
        raise RuntimeError("OCR profile unavailable")

    gray = pymupdf.Pixmap(pymupdf.csGRAY, image.width, image.height, samples, False)
    rgb = pymupdf.Pixmap(pymupdf.csRGB, gray)
    del gray
    rgb.set_dpi(_SCANNED_PDF_DPI, _SCANNED_PDF_DPI)
    with _CappedOCRPDF() as output:
        try:
            rgb.pdfocr_save(output, compress=1, language="eng", tessdata=str(tessdata))
        finally:
            # Preserve the bound if the native bridge translates the exception.
            if output.exceeded:
                raise ParseLimitError() from None
        generated_pdf = output.getvalue()
    del rgb
    if not generated_pdf or len(generated_pdf) > _SCANNED_OCR_MAX_PDF_BYTES:
        raise RuntimeError("OCR output unavailable")
    with pymupdf.open(stream=generated_pdf, filetype="pdf") as recognized:
        if recognized.is_repaired or len(recognized) != 1:
            raise RuntimeError("OCR output unavailable")
        return recognized[0].get_text()


def _decode_text(data: bytes, limits: ParseLimits) -> str:
    if data.startswith(codecs.BOM_UTF8):
        text = data.decode("utf-8-sig", errors="replace")
    elif data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        try:
            text = data.decode("utf-16")
        except UnicodeError:
            raise MalformedDocumentError() from None
    else:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            encoding = None
            try:
                from charset_normalizer import from_bytes

                best = from_bytes(data).best()
                encoding = best.encoding if best else None
            except Exception:
                pass
            if not encoding:
                try:
                    import chardet

                    detected = chardet.detect(data)
                    encoding = detected.get("encoding") if detected else None
                except Exception:
                    pass
            try:
                text = data.decode(encoding or "utf-8", errors="replace")
            except (LookupError, UnicodeError):
                text = data.decode("utf-8", errors="replace")

    if len(text) > limits.max_text_chars:
        raise ParseLimitError()
    if any((ord(char) < 32 and char not in "\t\n\r") or ord(char) == 127 for char in text):
        raise MalformedDocumentError()
    return text


def _read_text_with_fallback(file_path: str, limits: Optional[ParseLimits] = None) -> str:
    resolved = _limits_or_default(limits)
    return _decode_text(_bounded_source_bytes(file_path, resolved), resolved)


def _safe_basename(file_path: str) -> str:
    name = str(file_path).replace("\\", "/").rsplit("/", 1)[-1]
    safe = "".join(char if char.isprintable() and char not in "<>" else "_" for char in name)
    return safe[:80] or "unnamed"


class FileParser:
    """File parser with bounded source and result sizes."""

    SUPPORTED_EXTENSIONS = {".pdf", ".md", ".markdown", ".txt", ".docx"}

    @classmethod
    def is_supported(cls, file_path: str) -> bool:
        return Path(file_path).suffix.lower() in cls.SUPPORTED_EXTENSIONS

    @classmethod
    def extract_text(cls, file_path: str, *, limits: Optional[ParseLimits] = None) -> str:
        resolved = _limits_or_default(limits)
        suffix = Path(file_path).suffix.lower()
        if suffix not in cls.SUPPORTED_EXTENSIONS:
            raise UnsupportedDocumentError()
        if suffix == ".pdf":
            return cls._extract_from_pdf(file_path, limits=resolved)
        if suffix == ".docx":
            return cls._extract_from_docx(file_path, limits=resolved)
        if suffix in {".md", ".markdown"}:
            return cls._extract_from_md(file_path, limits=resolved)
        return cls._extract_from_txt(file_path, limits=resolved)

    @staticmethod
    def _extract_from_docx(file_path: str, *, limits: Optional[ParseLimits] = None) -> str:
        # The parser worker uses a fixed spec loader under -I, so package-relative
        # imports and caller-controlled sys.path cannot supply this dependency.
        import importlib.util
        import sys

        resolved = _limits_or_default(limits)
        source = Path(__file__).with_name("docx_extraction.py")
        name = "_nexaweave_docx_extraction"
        module = sys.modules.get(name)
        if module is None:
            spec = importlib.util.spec_from_file_location(name, source)
            if spec is None or spec.loader is None:
                raise ImportError("parser module unavailable")
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
        data = _bounded_source_bytes(file_path, resolved)
        try:
            return module.extract_docx(data, max_file_bytes=resolved.max_file_bytes,
                                       max_text_chars=resolved.max_text_chars).text
        except module.DocxError as error:
            failures = {"limit_exceeded": ParseLimitError,
                        "unsupported_document": UnsupportedDocumentError,
                        "malformed_document": MalformedDocumentError,
                        "invalid_source": InvalidSourceError}
            raise failures.get(error.code, MalformedDocumentError)() from None

    @staticmethod
    def _extract_scanned_pdf_text(file_path: str, *, limits: Optional[ParseLimits] = None) -> str:
        if Path(file_path).suffix.lower() != ".pdf":
            raise UnsupportedDocumentError()
        resolved = _limits_or_default(limits)
        ocr_limits = replace(
            resolved, max_file_bytes=min(resolved.max_file_bytes, _SCANNED_PDF_MAX_BYTES),
        )
        data = _bounded_source_bytes(file_path, ocr_limits)
        image = _preflight_scanned_pdf(data, limits=ocr_limits)
        if (image.width, image.height) != _SCANNED_OCR_SIZE:
            raise UnsupportedDocumentError()
        samples = data[image.offset:image.offset + image.length]
        del data
        try:
            with tempfile.TemporaryDirectory(prefix="ocr-data-", dir=Path.cwd()) as directory:
                tessdata = Path(directory)
                _stage_scanned_ocr_data(tessdata)
                text = _recognize_scanned_samples(samples, image, tessdata)
            if len(text) > min(resolved.max_text_chars, _SCANNED_OCR_MAX_TEXT_BYTES):
                raise ParseLimitError()
            if len(text.encode("utf-8")) > _SCANNED_OCR_MAX_TEXT_BYTES:
                raise ParseLimitError()
            if not text.strip():
                raise UnsupportedDocumentError()
            return text
        except ParseError:
            raise
        except Exception:
            raise RuntimeError("OCR profile or result unavailable") from None

    @staticmethod
    def _extract_from_pdf(file_path: str, *, limits: Optional[ParseLimits] = None) -> str:
        resolved = _limits_or_default(limits)
        data = _bounded_source_bytes(file_path, resolved)
        if b"%PDF-" not in data[:1024]:
            raise MalformedDocumentError()
        try:
            import fitz
        except ImportError:
            raise ImportError("PyMuPDF is required") from None

        document = None
        try:
            document = fitz.open(stream=data, filetype="pdf")
            if document.needs_pass or document.is_encrypted:
                raise MalformedDocumentError()
            if len(document) > resolved.max_pdf_pages:
                raise ParseLimitError()
            text_parts = []
            char_count = 0
            for page in document:
                page_text = page.get_text()
                if page_text.strip():
                    added = len(page_text) + (2 if text_parts else 0)
                    if char_count + added > resolved.max_text_chars:
                        raise ParseLimitError()
                    text_parts.append(page_text)
                    char_count += added
            return "\n\n".join(text_parts)
        except ParseError:
            raise
        except Exception:
            raise MalformedDocumentError() from None
        finally:
            if document is not None:
                try:
                    document.close()
                except Exception:
                    # Keep a classified parse/limit failure from being replaced
                    # by a native close error containing source details.
                    pass

    @staticmethod
    def _extract_from_md(file_path: str, *, limits: Optional[ParseLimits] = None) -> str:
        return _read_text_with_fallback(file_path, limits)

    @staticmethod
    def _extract_from_txt(file_path: str, *, limits: Optional[ParseLimits] = None) -> str:
        return _read_text_with_fallback(file_path, limits)

    @classmethod
    def extract_from_multiple(
        cls, file_paths: List[str], *, limits: Optional[ParseLimits] = None
    ) -> str:
        resolved = _limits_or_default(limits)
        if isinstance(file_paths, (str, bytes)):
            raise InvalidSourceError()
        try:
            paths = list(islice(iter(file_paths), resolved.max_files + 1))
        except (TypeError, ValueError):
            raise InvalidSourceError() from None
        if len(paths) > resolved.max_files:
            raise ParseLimitError()

        parts = []
        aggregate_chars = 0
        for index, file_path in enumerate(paths, 1):
            name = _safe_basename(file_path)
            try:
                extracted = cls.extract_text(file_path, limits=resolved)
                part = f"=== 文档 {index}: {name} ===\n{extracted}"
            except ParseLimitError:
                raise
            except FileNotFoundError:
                part = f"=== 文档 {index}: {name} (提取失败: missing_file) ==="
            except ParseError as error:
                part = f"=== 文档 {index}: {name} (提取失败: {error.code}) ==="
            except Exception:
                part = f"=== 文档 {index}: {name} (提取失败: parse_error) ==="
            added = len(part) + (2 if parts else 0)
            if aggregate_chars + added > resolved.max_aggregate_chars:
                raise ParseLimitError()
            parts.append(part)
            aggregate_chars += added
        return "\n\n".join(parts)


def split_text_into_chunks(
    text: str, chunk_size: int = 500, overlap: int = 50
) -> List[str]:
    """Split bounded text, preferring sentence boundaries without stalling."""
    if type(chunk_size) is not int or chunk_size <= 0:
        raise ValueError("chunk_size must be a positive integer")
    if type(overlap) is not int or overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap must be a nonnegative integer smaller than chunk_size")
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    if len(text) > MAX_CHUNK_INPUT_CHARS:
        raise ParseLimitError()
    if not text.strip():
        return []
    if len(text) <= chunk_size:
        return [text]

    chunks = []
    start = 0
    separators = ("。", "！", "？", ".\n", "!\n", "?\n", "\n\n", ". ", "! ", "? ")
    while start < len(text):
        end = min(start + chunk_size, len(text))
        if end < len(text):
            window = text[start:end]
            for separator in separators:
                last = window.rfind(separator)
                candidate = start + last + len(separator)
                if last > chunk_size * 0.3 and candidate - overlap > start:
                    end = candidate
                    break
        chunk = text[start:end].strip()
        if chunk:
            if len(chunks) >= MAX_EMITTED_CHUNKS:
                raise ParseLimitError()
            chunks.append(chunk)
        start = end - overlap if end < len(text) else len(text)
    return chunks
