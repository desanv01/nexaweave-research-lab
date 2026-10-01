"""Offline extraction characterization source. Main runs these tests."""
from dataclasses import FrozenInstanceError
from io import BytesIO
import struct
import zipfile

import pytest

from app.utils import docx_extraction as docx
from app.utils.file_parser import FileParser, MalformedDocumentError, ParseLimitError, ParseLimits, UnsupportedDocumentError

W = docx.WORD_NAMESPACES[0]


def document(body, namespace=W):
    return (f'<w:document xmlns:w="{namespace}"><w:body>{body}</w:body></w:document>').encode("utf-8")


def package(body=None, *, raw=None, namespace=W, extra=(), main_type=docx.MAIN_TYPE,
            target="word/document.xml", mode="Internal", compression=zipfile.ZIP_DEFLATED):
    output = BytesIO()
    with zipfile.ZipFile(output, "w", compression=compression) as archive:
        archive.writestr("[Content_Types].xml", (
            f'<Types xmlns="{docx.CT}"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            f'<Override PartName="/word/document.xml" ContentType="{main_type}"/></Types>'))
        archive.writestr("_rels/.rels", (
            f'<Relationships xmlns="{docx.REL}"><Relationship Id="rId1" Type="{docx.OFFICE_TYPES[0]}" Target="{target}" TargetMode="{mode}"/></Relationships>'))
        archive.writestr("word/document.xml", document(body or "", namespace) if raw is None else raw)
        for name, value in extra:
            archive.writestr(name, value)
    return output.getvalue()


def paragraph(text):
    # Fixture text is explicitly safe XML literal input, not a general writer.
    return f'<w:p><w:r><w:t>{text}</w:t></w:r></w:p>'


@pytest.mark.parametrize("namespace", docx.WORD_NAMESPACES, ids=["transitional", "strict"])
def test_order_unicode_table_delimiters_ranges_and_merge_metadata(namespace):
    body = (paragraph("A😀猫") +
            '<w:tbl><w:tblGrid><w:gridCol/></w:tblGrid><w:tr>'
            '<w:tc><w:tcPr><w:gridSpan w:val="2"/><w:vMerge w:val="restart"/></w:tcPr>'
            + paragraph("雪") + paragraph("café") + '</w:tc><w:tc><w:p/></w:tc></w:tr>'
            '<w:tr><w:tc><w:tcPr><w:vMerge/></w:tcPr><w:p/></w:tc><w:tc>'
            + paragraph("Z") + '</w:tc></w:tr></w:tbl>' + paragraph("end"))
    result = docx.extract_docx(package(body, namespace=namespace))
    assert result.text == "A😀猫\n\n雪\ncafé\t\n\tZ\n\nend"
    assert [result.text[b.start:b.end] for b in result.blocks] == ["A😀猫", "雪\ncafé", "", "", "Z", "end"]
    assert [(b.start, b.end) for b in result.blocks] == [(0, 3), (5, 11), (12, 12), (13, 13), (14, 15), (17, 20)]
    assert [(b.table, b.row, b.cell) for b in result.blocks[1:5]] == [(0, 0, 0), (0, 0, 1), (0, 1, 0), (0, 1, 1)]
    assert result.blocks[1].grid_span == 2 and result.blocks[1].vertical_merge == "restart"
    assert result.blocks[3].vertical_merge == "continue"
    assert result.excluded_parts == ("headers", "footers", "footnotes", "endnotes")
    with pytest.raises(FrozenInstanceError):
        result.blocks[0].end = 99


def test_retained_insertions_literal_hyperlinks_and_fields_without_evaluation():
    body = ('<w:p><w:r><w:t xml:space="preserve"> a </w:t><w:tab/><w:t>😀</w:t><w:br/><w:cr/></w:r>'
            '<w:del><w:r><w:delText>deleted</w:delText></w:r></w:del>'
            '<w:ins><w:r><w:t>inserted</w:t></w:r></w:ins>'
            '<w:hyperlink xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" r:id="external">'
            '<w:r><w:t>visible</w:t></w:r></w:hyperlink>'
            '<w:r><w:instrText>INCLUDETEXT secret</w:instrText><w:fldChar w:fldCharType="begin"/></w:r>'
            '<w:fldSimple w:instr="HYPERLINK https://invalid"><w:r><w:t>result</w:t></w:r></w:fldSimple></w:p>')
    result = docx.extract_docx(package(body, extra=[("word/header1.xml", b"excluded"), ("word/_rels/document.xml.rels", b"external literal ignored")]))
    assert result.text == " a \t😀\n\ninsertedvisibleresult"
    assert "deleted" not in result.text and "INCLUDETEXT" not in result.text


def test_blank_blocks_and_multiple_tables_are_explicit():
    result = docx.extract_docx(package('<w:p/><w:tbl><w:tr><w:tc><w:p/></w:tc></w:tr></w:tbl><w:tbl><w:tr><w:tc><w:p/></w:tc></w:tr></w:tbl>'))
    assert result.text == "\n\n\n\n"
    assert len(result.blocks) == 3 and all(b.start == b.end for b in result.blocks)
    assert [b.table for b in result.blocks] == [None, 0, 1]
    assert docx.extract_docx(package()).text == ""


@pytest.mark.parametrize("body", [
    '<w:altChunk/>', '<w:sdt><w:sdtContent>' + paragraph("lost") + '</w:sdtContent></w:sdt>',
    '<w:p><w:r><w:drawing/></w:r></w:p>', '<w:p><w:r><w:pict><w:txbxContent/></w:pict></w:r></w:p>',
    '<w:tbl><w:tr><w:tc><w:tbl/></w:tc></w:tr></w:tbl>',
    '<w:customXml>' + paragraph("lost") + '</w:customXml>',
    '<mc:AlternateContent xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"/>',
    '<w:p><w:r><w:unknown>lost</w:unknown></w:r></w:p>',
    '<w:p><w:r><w:br w:type="page"/></w:r></w:p>',
], ids=["altchunk", "sdt", "drawing", "textbox", "nested-table", "custom-xml", "alternate", "unknown-inline", "page-break"])
def test_unsupported_content_never_reports_partial_success(body):
    with pytest.raises(docx.DocxError, match="^unsupported_document$"):
        docx.extract_docx(package(paragraph("prefix") + body))


@pytest.mark.parametrize("raw,code", [
    (b'<!DOCTYPE x [<!ENTITY x "private">]>' + document(paragraph("&x;")), "malformed_document"),
    (document(paragraph("x")).decode().encode("utf-16"), "malformed_document"),
    (b'<?xml version="1.0" encoding="ISO-8859-1"?>' + document(paragraph("x")), "unsupported_document"),
    (document(paragraph("\x00")), "malformed_document"),
    (b'<wrong/>', "unsupported_document"), (b'<w:document', "malformed_document"),
    (b'\xff', "malformed_document"),
], ids=["entity", "utf16", "encoding", "control", "root", "truncated", "bad-utf8"])
def test_strict_xml_profile(raw, code):
    with pytest.raises(docx.DocxError, match="^" + code + "$"):
        docx.extract_docx(package(raw=raw))


@pytest.mark.parametrize("options", [
    {"main_type": "application/vnd.ms-word.document.macroEnabled.main+xml"},
    {"target": "../secret"}, {"target": "https://invalid/document.xml", "mode": "External"},
    {"extra": [("word/embeddings/item.bin", b"private")]},
    {"namespace": "urn:unknown"}, {"compression": zipfile.ZIP_BZIP2},
], ids=["macro", "alternate-main", "external-main", "embedded", "namespace", "compression"])
def test_unsupported_packages(options):
    with pytest.raises(docx.DocxError, match="^unsupported_document$"):
        docx.extract_docx(package(paragraph("safe"), **options))


@pytest.mark.parametrize("name", ["../secret", "/absolute", "word\\bad", "word//bad", "word/./bad", "word/%2e%2e/bad", "word/a:bad", "word/document.xml"],
                         ids=["traversal", "absolute", "backslash", "empty", "dot", "encoded", "colon", "duplicate"])
def test_noncanonical_zip_names(name):
    if name == "word\\bad":
        # Windows ZipInfo normalizes backslashes while writing. Mutate only
        # the two serialized filename fields after writing a canonical name.
        canonical = b"word/bad"
        hostile = b"word\\bad"
        raw = bytearray(package(paragraph("safe"), extra=[(canonical.decode("ascii"), b"secret")]))
        with zipfile.ZipFile(BytesIO(raw)) as archive:
            local = archive.getinfo(canonical.decode("ascii")).header_offset
        assert raw[local:local + 4] == b"PK\x03\x04"
        assert struct.unpack_from("<H", raw, local + 26)[0] == len(canonical)
        local_name = local + 30
        assert raw[local_name:local_name + len(canonical)] == canonical
        raw[local_name:local_name + len(canonical)] = hostile
        # The fixture writer emits an ordinary EOCD without a ZIP comment.
        assert raw[-22:-18] == b"PK\x05\x06"
        central = struct.unpack_from("<L", raw, len(raw) - 22 + 16)[0]
        central_name = None
        while central < len(raw) - 22:
            assert raw[central:central + 4] == b"PK\x01\x02"
            length, extra, comment = struct.unpack_from("<3H", raw, central + 28)
            start = central + 46
            if raw[start:start + length] == canonical:
                central_name = start
                raw[start:start + length] = hostile
                break
            central += 46 + length + extra + comment
        assert central_name is not None
        assert raw[local_name:local_name + len(hostile)] == hostile
        assert raw[central_name:central_name + len(hostile)] == hostile
        payload = bytes(raw)
    else:
        payload = package(paragraph("safe"), extra=[(name, b"secret")])
    with pytest.raises(docx.DocxError, match="^malformed_document$"):
        docx.extract_docx(payload)


def test_zip_crc_encryption_special_entries_and_missing_parts():
    raw = bytearray(package(paragraph("private"), compression=zipfile.ZIP_STORED))
    location = raw.find(b"private")
    assert location > 0
    raw[location] ^= 1
    with pytest.raises(docx.DocxError, match="malformed_document"):
        docx.extract_docx(bytes(raw))
    raw = bytearray(package(paragraph("safe")))
    central = raw.find(b"PK\x01\x02")
    struct.pack_into("<H", raw, central + 8, 1)
    with pytest.raises(docx.DocxError, match="unsupported_document"):
        docx.extract_docx(bytes(raw))
    link = zipfile.ZipInfo("word/link")
    link.create_system = 3
    link.external_attr = (0o120777 << 16)
    with pytest.raises(docx.DocxError, match="unsupported_document"):
        docx.extract_docx(package(extra=[(link, b"/secret")]))
    out = BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("word/document.xml", document(""))
    with pytest.raises(docx.DocxError, match="malformed_document"):
        docx.extract_docx(out.getvalue())


def test_exact_text_input_and_blocks_limits(monkeypatch):
    raw = package(paragraph("😀猫"))
    assert docx.extract_docx(raw, max_file_bytes=len(raw), max_text_chars=2).text == "😀猫"
    for options in ({"max_file_bytes": len(raw) - 1}, {"max_text_chars": 1}):
        with pytest.raises(docx.DocxError, match="limit_exceeded"):
            docx.extract_docx(raw, **options)
    monkeypatch.setattr(docx, "MAX_BLOCKS", 2)
    assert len(docx.extract_docx(package('<w:p/><w:p/>')).blocks) == 2
    with pytest.raises(docx.DocxError, match="limit_exceeded"):
        docx.extract_docx(package('<w:p/><w:p/><w:p/>'))


def test_actual_entry_depth_node_and_declared_zip_limits():
    with pytest.raises(docx.DocxError, match="limit_exceeded"):
        docx.extract_docx(package(extra=[(f"extra/{i}", b"") for i in range(1998)]))
    deep = '<w:r>' * 32 + '<w:t>x</w:t>' + '</w:r>' * 32
    with pytest.raises(docx.DocxError, match="limit_exceeded"):
        docx.extract_docx(package('<w:p>' + deep + '</w:p>'))
    with pytest.raises(docx.DocxError, match="limit_exceeded"):
        docx.extract_docx(package('<w:p><w:r>' + '<w:tab/>' * 100000 + '</w:r></w:p>'))
    raw = bytearray(package(paragraph("safe")))
    central = raw.find(b"PK\x01\x02")
    struct.pack_into("<L", raw, central + 24, docx.MAX_XML + 1)
    with pytest.raises(docx.DocxError, match="limit_exceeded"):
        docx.extract_docx(bytes(raw))


def test_exact_depth_and_node_limits():
    exact = '<w:p>' + '<w:r>' * 28 + '<w:t>x</w:t>' + '</w:r>' * 28 + '</w:p>'
    assert docx.extract_docx(package(exact)).text == "x"
    over = '<w:p>' + '<w:r>' * 29 + '<w:t>x</w:t>' + '</w:r>' * 29 + '</w:p>'
    with pytest.raises(docx.DocxError, match="limit_exceeded"):
        docx.extract_docx(package(over))
    assert len(docx.extract_docx(package('<w:p><w:r>' + '<w:tab/>' * 99996 + '</w:r></w:p>')).text) == 99996
    with pytest.raises(docx.DocxError, match="limit_exceeded"):
        docx.extract_docx(package('<w:p><w:r>' + '<w:tab/>' * 99997 + '</w:r></w:p>'))


def test_parsed_xml_size_exact_and_one_byte_over():
    base = document('<w:p/>')
    # A trailing XML comment is syntactically valid and contains no body text.
    raw = base + b'<!--' + b'x' * (docx.MAX_XML - len(base) - 7) + b'-->'
    assert len(raw) == docx.MAX_XML
    assert docx.extract_docx(package(raw=raw)).text == ""
    with pytest.raises(docx.DocxError, match="limit_exceeded"):
        docx.extract_docx(package(raw=raw + b' '))


def test_total_declared_uncompressed_zip_limit():
    raw = bytearray(package(extra=[("media/a", b""), ("media/b", b"")]))
    cursor = 0
    for _ in range(5):
        central = raw.find(b"PK\x01\x02", cursor)
        if b"media/" in raw[central + 46:central + 60]:
            struct.pack_into("<L", raw, central + 24, 33 * 1024 * 1024)
        cursor = central + 46
    with pytest.raises(docx.DocxError, match="limit_exceeded"):
        docx.extract_docx(bytes(raw))


@pytest.mark.parametrize("body", [
    '<w:tbl><w:tr><w:trPr><w:del/></w:trPr><w:tc>' + paragraph("deleted row") + '</w:tc></w:tr></w:tbl>',
    '<w:p><w:pPr><w:rPr><w:del/></w:rPr></w:pPr><w:r><w:t>ambiguous mark</w:t></w:r></w:p>',
    '<w:tbl><w:tr><w:tc><w:tcPr><w:cellDel/></w:tcPr>' + paragraph("deleted cell") + '</w:tc></w:tr></w:tbl>',
], ids=["deleted-row", "deleted-mark", "deleted-cell"])
def test_ambiguous_property_revisions_fail_instead_of_retaining_deleted_text(body):
    with pytest.raises(docx.DocxError, match="unsupported_document"):
        docx.extract_docx(package(body))


@pytest.mark.parametrize("span,merge", [("0", "restart"), ("-1", "restart"), ("1", "unknown"), ("10000", "restart")],
                         ids=["zero-span", "negative-span", "bad-merge", "large-span"])
def test_malformed_table_merge_metadata(span, merge):
    body = f'<w:tbl><w:tr><w:tc><w:tcPr><w:gridSpan w:val="{span}"/><w:vMerge w:val="{merge}"/></w:tcPr><w:p/></w:tc></w:tr></w:tbl>'
    with pytest.raises(docx.DocxError, match="malformed_document"):
        docx.extract_docx(package(body))


def test_file_parser_safe_adapter(tmp_path):
    source = tmp_path / "private.docx"
    source.write_bytes(package(paragraph("😀猫")))
    assert FileParser.is_supported(source) and FileParser.extract_text(source) == "😀猫"
    with pytest.raises(ParseLimitError):
        FileParser.extract_text(source, limits=ParseLimits(max_text_chars=1))
    source.write_bytes(package('<w:altChunk/>'))
    with pytest.raises(UnsupportedDocumentError) as caught:
        FileParser.extract_text(source)
    assert str(source) not in str(caught.value)
    source.write_bytes(b"private invalid zip")
    with pytest.raises(MalformedDocumentError) as caught:
        FileParser.extract_text(source)
    assert "private" not in str(caught.value)
