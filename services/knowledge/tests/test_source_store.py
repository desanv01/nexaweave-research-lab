"""Pure retained-text and codepoint passage validation contracts."""

from uuid import uuid4

import pytest

from mirofish_storage import InvalidProject, SourceStore
from mirofish_storage.__main__ import main as cli_main
from mirofish_storage.source import _passages_input, _source_input


def test_exact_utf8_bom_newline_and_codepoint_offsets():
    text = "\ufeffA😀\r\n猫"
    _, copied, digest, byte_length, codepoints = _source_input("upload", text)
    assert copied == text and byte_length == len(text.encode("utf-8"))
    assert codepoints == len(text) == 6
    passage = _passages_input([{"evidence_id": str(uuid4()), "start": 2, "end": 6,
                                "page": 7}], uuid4(), uuid4(), text)[0]
    assert passage.excerpt == "😀\r\n猫"
    assert len(passage.excerpt.encode("utf-8")) > passage.end - passage.start
    assert passage.page == 7 and len(digest) == len(passage.excerpt_sha256) == 64


@pytest.mark.parametrize("name,text", [
    ("", "x"), ("x" * 257, "x"), ("x", ""), ("x", "a\x00b"),
    ("\ud800", "x"), ("x", "\ud800"), ("x", "猫" * 350000),
    (12, "x"), ("x", b"bytes"),
], ids=["empty-name", "long-name", "empty-text", "nul-text",
        "surrogate-name", "surrogate-text", "oversized-utf8-text",
        "nonstring-name", "bytes-text"])
def test_source_rejects_invalid_text_before_connection(name, text):
    calls = []
    with pytest.raises(InvalidProject):
        SourceStore(lambda: calls.append(1)).ingest_text("owner", uuid4(), uuid4(),
                                                           name, text)
    assert calls == []


@pytest.mark.parametrize("item", [
    {"evidence_id": "bad", "start": 0, "end": 1},
    {"evidence_id": str(uuid4()), "start": True, "end": 1},
    {"evidence_id": str(uuid4()), "start": 1, "end": 1},
    {"evidence_id": str(uuid4()), "start": 0, "end": 99},
    {"evidence_id": str(uuid4()), "start": 0, "end": 1, "page": False},
    {"evidence_id": str(uuid4()), "start": 0, "end": 1, "excerpt": "fake"},
], ids=["bad-uuid", "bool-start", "empty-range", "out-of-bounds",
        "bool-page", "caller-excerpt"])
def test_passage_rejects_offsets_and_caller_excerpt(item):
    with pytest.raises(InvalidProject):
        _passages_input([item], uuid4(), uuid4(), "abc")


def test_passage_duplicate_and_excerpt_byte_cap():
    evidence = str(uuid4())
    with pytest.raises(InvalidProject):
        _passages_input([{"evidence_id": evidence, "start": 0, "end": 1},
                         {"evidence_id": evidence, "start": 1, "end": 2}],
                        uuid4(), uuid4(), "ab")
    with pytest.raises(InvalidProject):
        _passages_input([{"evidence_id": str(uuid4()), "start": 0, "end": 11000}],
                        uuid4(), uuid4(), "猫" * 11000)


def test_cli_rejects_bad_passages_before_connection(tmp_path, monkeypatch, capsys):
    source = tmp_path / "source.txt"
    source.write_text("猫", encoding="utf-8")
    declared = tmp_path / "passages.json"
    declared.write_text('[{"evidence_id":"%s","start":true,"end":1}]' % uuid4(),
                        encoding="utf-8")
    calls = []
    monkeypatch.setenv("MIROFISH_APPSTORE_DSN", "unused")
    monkeypatch.setattr("psycopg.connect", lambda *args, **kwargs: calls.append(1))
    result = cli_main(["import-source", "--principal", "owner", "--project-id", str(uuid4()),
                       "--source-revision", str(uuid4()), "--name", "text",
                       "--input", str(source), "--passages", str(declared)])
    assert result == 2 and calls == []
    assert capsys.readouterr().err == "invalid_project\n"
