"""Strict, bounded OOXML main-body extraction; no relationships are fetched."""
from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import re
import stat
import struct
import zlib
from xml.etree import ElementTree as ET
import zipfile

MAX_INPUT = 50 * 1024 * 1024
MAX_ENTRIES = 2000
MAX_TOTAL = 64 * 1024 * 1024
MAX_XML = 8 * 1024 * 1024
MAX_BLOCKS = 5000
WORD_NAMESPACES = (
    "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "http://purl.oclc.org/ooxml/wordprocessingml/main",
)
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
MAIN_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
OFFICE_TYPES = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument",
    "http://purl.oclc.org/ooxml/officeDocument/relationships/officeDocument",
)
COVERAGE = ("main_body_paragraphs", "main_body_table_cells")
EXCLUDED_PARTS = ("headers", "footers", "footnotes", "endnotes")


class DocxError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _malformed():
    raise DocxError("malformed_document")


def _unsupported():
    raise DocxError("unsupported_document")


def _limit():
    raise DocxError("limit_exceeded")


@dataclass(frozen=True)
class Block:
    kind: str
    start: int
    end: int
    table: int | None = None
    row: int | None = None
    cell: int | None = None
    grid_span: int = 1
    vertical_merge: str | None = None


@dataclass(frozen=True)
class Extraction:
    text: str
    blocks: tuple[Block, ...]
    coverage: tuple[str, ...] = COVERAGE
    excluded_parts: tuple[str, ...] = EXCLUDED_PARTS


def _central_bound(data):
    # Check central-directory count before ZipFile allocates ZipInfo objects.
    end = data.rfind(b"PK\x05\x06", max(0, len(data) - 65557))
    if end < 0 or end + 22 > len(data):
        _malformed()
    disk, central_disk, disk_count, count, size, offset, comment = struct.unpack_from("<4H2LH", data, end + 4)
    if disk or central_disk or disk_count != count or count == 65535:
        _unsupported()
    if end + 22 + comment != len(data) or offset + size != end:
        _malformed()
    if count > MAX_ENTRIES:
        _limit()
    cursor, actual = offset, 0
    while cursor < end:
        if actual >= MAX_ENTRIES:
            _limit()
        if cursor + 46 > end or data[cursor:cursor + 4] != b"PK\x01\x02":
            _malformed()
        name, extra, note = struct.unpack_from("<3H", data, cursor + 28)
        cursor += 46 + name + extra + note
        actual += 1
    if cursor != end or actual != count:
        _malformed()


def _parts(data):
    _central_bound(data)
    needed = {"[Content_Types].xml", "_rels/.rels", "word/document.xml"}
    result, seen, total = {}, set(), 0
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            for entry in archive.infolist():
                name = entry.filename
                if (name != entry.orig_filename or not name or name in seen
                        or any(part in ("", ".", "..") for part in name.split("/"))
                        or any(char in name for char in "\\:%?#")
                        or any(ord(char) < 32 or ord(char) == 127 for char in name)):
                    _malformed()
                seen.add(name)
                mode = entry.external_attr >> 16
                if entry.is_dir() or entry.external_attr & 0x10 or (stat.S_IFMT(mode) not in (0, stat.S_IFREG)):
                    _unsupported()
                if entry.flag_bits & 1 or entry.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                    _unsupported()
                if (name.startswith(("word/embeddings/", "word/activeX/"))
                        or name.lower().endswith("vbaproject.bin")):
                    _unsupported()
                total += entry.file_size
                if total > MAX_TOTAL or (name in needed and entry.file_size > MAX_XML):
                    _limit()
            if not needed <= seen:
                _malformed()
            # Stream every admitted entry through EOF for CRC verification. Only
            # the three fixed XML parts are retained, never extracted to disk.
            for entry in archive.infolist():
                chunks, consumed = [], 0
                with archive.open(entry) as source:
                    while True:
                        chunk = source.read(min(32768, entry.file_size - consumed + 1))
                        if not chunk:
                            break
                        consumed += len(chunk)
                        if consumed > entry.file_size:
                            _malformed()
                        if entry.filename in needed:
                            chunks.append(chunk)
                if consumed != entry.file_size:
                    _malformed()
                if entry.filename in needed:
                    result[entry.filename] = b"".join(chunks)
    except DocxError:
        raise
    except (OSError, ValueError, RuntimeError, NotImplementedError, zipfile.BadZipFile, EOFError, zlib.error):
        _malformed()
    return result


def _xml(raw):
    try:
        text = raw.decode("utf-8-sig", errors="strict")
    except UnicodeError:
        _malformed()
    if (re.search(r"<!\s*(DOCTYPE|ENTITY)", text, re.I)
            or any((ord(c) < 32 and c not in "\t\r\n") or ord(c) == 127 for c in text)):
        _malformed()
    declaration = re.match(r"\s*<\?xml\s+[^?]*\?>", text)
    if declaration:
        encoding = re.search(r"encoding\s*=\s*['\"]([^'\"]+)['\"]", declaration.group(), re.I)
        if encoding and encoding.group(1).lower() != "utf-8":
            _unsupported()
    parser = ET.XMLPullParser(events=("start", "end"))
    depth, count, root = 0, 0, None
    try:
        for offset in range(0, len(text), 4096):
            parser.feed(text[offset:offset + 4096])
            for event, element in parser.read_events():
                if event == "start":
                    depth += 1
                    count += 1
                    if depth > 32 or count > 100000:
                        _limit()
                    if root is None:
                        root = element
                else:
                    depth -= 1
        parser.close()
    except ET.ParseError:
        _malformed()
    if root is None or depth:
        _malformed()
    return root


def _package(parts):
    types = _xml(parts["[Content_Types].xml"])
    rels = _xml(parts["_rels/.rels"])
    if types.tag != f"{{{CT}}}Types" or rels.tag != f"{{{REL}}}Relationships":
        _malformed()
    main, names = [], set()
    for item in types:
        if item.tag not in (f"{{{CT}}}Default", f"{{{CT}}}Override"):
            _unsupported()
        content = item.get("ContentType", "")
        if not content:
            _malformed()
        if any(token in content.lower() for token in ("macro", "oleobject", "activex")):
            _unsupported()
        key = (item.tag, item.get("PartName") if item.tag.endswith("Override") else item.get("Extension"))
        if not key[1] or key in names:
            _malformed()
        names.add(key)
        if item.get("PartName") == "/word/document.xml":
            main.append(content)
    if main != [MAIN_TYPE]:
        _unsupported()
    office, ids = [], set()
    for item in rels:
        if item.tag != f"{{{REL}}}Relationship" or not item.get("Id") or item.get("Id") in ids:
            _malformed()
        ids.add(item.get("Id"))
        if item.get("Type") in OFFICE_TYPES:
            office.append(item)
    if len(office) != 1 or office[0].get("TargetMode", "Internal") != "Internal" or office[0].get("Target") != "word/document.xml":
        _unsupported()


class _Body:
    def __init__(self, namespace, maximum):
        self.ns, self.maximum = namespace, maximum
        self.parts, self.blocks, self.length = [], [], 0

    def tag(self, name):
        return f"{{{self.ns}}}{name}"

    def local(self, element):
        if not element.tag.startswith("{" + self.ns + "}"):
            _unsupported()
        return element.tag.split("}", 1)[1]

    def append(self, text):
        if self.length + len(text) > self.maximum:
            _limit()
        self.parts.append(text)
        self.length += len(text)

    def paragraph(self, element):
        # Text is emitted directly into the bounded result, avoiding an
        # unbounded intermediate paragraph. Property subtrees are metadata.
        if element.text and element.text.strip():
            _malformed()
        for child in element:
            if child.tail and child.tail.strip():
                _malformed()
            name = self.local(child)
            if name in ("pPr", "rPr", "del", "moveFrom", "instrText", "delText", "fldChar",
                        "bookmarkStart", "bookmarkEnd", "proofErr", "commentRangeStart", "commentRangeEnd", "commentReference",
                        "lastRenderedPageBreak"):
                continue
            if name in ("r", "hyperlink", "ins", "moveTo", "fldSimple"):
                self.paragraph(child)
            elif name == "t":
                if len(child):
                    _unsupported()
                self.append(child.text or "")
            elif name == "tab":
                self.append("\t")
            elif name in ("br", "cr"):
                if name == "br" and child.get(self.tag("type"), "textWrapping") != "textWrapping":
                    _unsupported()
                self.append("\n")
            else:
                _unsupported()

    def block(self, element, kind, separator, **metadata):
        if len(self.blocks) >= MAX_BLOCKS:
            _limit()
        self.append(separator)
        start = self.length
        if kind == "paragraph":
            self.paragraph(element)
        else:
            if element.text and element.text.strip():
                _malformed()
            paragraphs = 0
            for child in element:
                if child.tail and child.tail.strip():
                    _malformed()
                name = self.local(child)
                if name == "tcPr":
                    continue
                if name != "p":
                    _unsupported()
                if paragraphs:
                    self.append("\n")
                self.paragraph(child)
                paragraphs += 1
            if not paragraphs:
                _malformed()
        self.blocks.append(Block(kind, start, self.length, **metadata))

    def table(self, element, number):
        if element.text and element.text.strip():
            _malformed()
        rows = 0
        for row in element:
            if row.tail and row.tail.strip():
                _malformed()
            name = self.local(row)
            if name in ("tblPr", "tblGrid"):
                continue
            if name != "tr":
                _unsupported()
            if row.text and row.text.strip():
                _malformed()
            cells = 0
            for cell in row:
                if cell.tail and cell.tail.strip():
                    _malformed()
                name = self.local(cell)
                if name == "trPr":
                    continue
                if name != "tc":
                    _unsupported()
                properties = cell.findall(self.tag("tcPr"))
                if len(properties) > 1:
                    _malformed()
                span, merge = 1, None
                if properties:
                    spans = properties[0].findall(self.tag("gridSpan"))
                    merges = properties[0].findall(self.tag("vMerge"))
                    if len(spans) > 1 or len(merges) > 1:
                        _malformed()
                    if spans:
                        raw = spans[0].get(self.tag("val"), "")
                        if not re.fullmatch(r"[1-9][0-9]{0,3}", raw):
                            _malformed()
                        span = int(raw)
                    if merges:
                        merge = merges[0].get(self.tag("val"), "continue")
                        if merge not in ("restart", "continue"):
                            _malformed()
                separator = "\t" if cells else ("\n" if rows else ("\n\n" if self.blocks else ""))
                self.block(cell, "table_cell", separator, table=number, row=rows, cell=cells,
                           grid_span=span, vertical_merge=merge)
                cells += 1
            if not cells:
                _malformed()
            rows += 1
        if not rows:
            _malformed()


def extract_docx(data: bytes, *, max_file_bytes=MAX_INPUT, max_text_chars=5_000_000) -> Extraction:
    """Extract admitted bytes with fixed finite caps; offsets are codepoints."""
    if type(data) is not bytes:
        raise DocxError("invalid_source")
    if any(type(v) is not int or v <= 0 for v in (max_file_bytes, max_text_chars)):
        raise ValueError("limits must be positive integers")
    if len(data) > min(max_file_bytes, MAX_INPUT):
        _limit()
    parts = _parts(data)
    _package(parts)
    document = _xml(parts["word/document.xml"])
    namespace = next((ns for ns in WORD_NAMESPACES if document.tag == f"{{{ns}}}document"), None)
    if namespace is None:
        _unsupported()
    builder = _Body(namespace, min(max_text_chars, 5_000_000))
    # Refuse embedded/layout/wrapper profiles even inside excluded revisions or
    # properties. Silent partial extraction must never look complete.
    forbidden = {"altChunk", "txbxContent", "textbox", "AlternateContent", "object", "drawing", "pict", "sdt", "customXml", "hMerge", "vanish", "specVanish", "cellDel", "cellMerge", "tblPrChange", "trPrChange", "tcPrChange", "pPrChange", "rPrChange"}
    for node in document.iter():
        if node.tag.rsplit("}", 1)[-1] in forbidden:
            _unsupported()
        if node.tag in (builder.tag("trPr"), builder.tag("pPr")) and any(
                descendant.tag in (builder.tag("del"), builder.tag("moveFrom"))
                for descendant in node.iter()):
            # Deleted row/paragraph-mark rendering differs from inline deleted
            # runs. Refuse that profile rather than publish deleted cell text.
            _unsupported()
    if len(document) != 1 or document[0].tag != builder.tag("body"):
        _unsupported()
    if ((document.text and document.text.strip())
            or (document[0].text and document[0].text.strip())
            or (document[0].tail and document[0].tail.strip())):
        _malformed()
    tables = 0
    for child in document[0]:
        if child.tail and child.tail.strip():
            _malformed()
        name = builder.local(child)
        if name == "p":
            builder.block(child, "paragraph", "\n\n" if builder.blocks else "")
        elif name == "tbl":
            builder.table(child, tables)
            tables += 1
        elif name == "sectPr":
            continue
        else:
            _unsupported()
    return Extraction("".join(builder.parts), tuple(builder.blocks))
