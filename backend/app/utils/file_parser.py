"""Bounded text extraction for locally admitted PDF, Markdown, and TXT files."""

import codecs
import os
import stat
from dataclasses import dataclass
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
        name = "_mirofish_docx_extraction"
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
