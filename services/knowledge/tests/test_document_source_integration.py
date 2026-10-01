"""Actual owned PostgreSQL + fresh saved DOCX CLI source; Main executes."""
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4
import zipfile

import pytest

from mirofish_storage import NotFound, ProjectStore, SourceStore
from test_project_store import snapshot
from test_source_store_postgres import factory

pytestmark = pytest.mark.postgres
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"


def _paragraph(text):
    return f'<w:p><w:r><w:t>{text}</w:t></w:r></w:p>'


def _package(body):
    output = BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", f'<Types xmlns="{CT}"><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        archive.writestr("_rels/.rels", f'<Relationships xmlns="{REL}"><Relationship Id="r1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        archive.writestr("word/document.xml", f'<w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>')
    return output.getvalue()


_CHILD = '''
import importlib.abc
import os
import runpy
import sys
import threading
sys.path.insert(0, REPOSITORY)
from tools.run_unit_tests import LoopbackOnlySockets
guard = LoopbackOnlySockets()
guard.install()
blocked = ('app', 'flask', 'dotenv', 'openai', 'camel', 'oasis', 'graphiti_core', 'mirofish_knowledge')
class NoApplicationOrProvider(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == item or fullname.startswith(item + '.') for item in blocked):
            raise AssertionError('unexpected application/provider import')
sys.meta_path.insert(0, NoApplicationOrProvider())
observed = set()
def observe(frame, event, arg):
    module = frame.f_globals.get('__name__', '')
    name = frame.f_code.co_name
    if event == 'return' and module == 'mirofish_storage.store' and name == 'get' and arg is not None:
        observed.add('authority')
    if event == 'call' and name == '_read_document' and module == '__main__':
        if 'authority' not in observed:
            raise AssertionError('document read before actual authority')
        observed.add('document_read')
    if event == 'call' and module == 'psycopg.connection' and name == 'close':
        observed.add('pg_close')
sys.setprofile(observe)
threading.setprofile(observe)
status = 96
try:
    assert all(not os.environ.get(key) for key in ('LLM_API_KEY', 'OPENAI_API_KEY', 'DEEPSEEK_API_KEY', 'ZEP_API_KEY', 'MIROFISH_SECRET_SENTINEL'))
    sys.argv = [SAVED_CLI, *sys.argv[1:]]
    try:
        runpy.run_path(SAVED_CLI, run_name='__main__')
    except SystemExit as exited:
        status = exited.code
finally:
    sys.setprofile(None)
    threading.setprofile(None)
    guard.restore()
if guard.blocked_attempts:
    status = 91
elif 'pg_close' not in observed:
    status = 92
elif status == 0 and not {'authority', 'document_read'} <= observed:
    status = 93
elif any(name == item or name.startswith(item + '.') for item in blocked for name in sys.modules):
    status = 94
raise SystemExit(status)
'''


@pytest.fixture
def saved_cli(tmp_path, factory):
    # factory first validates the exact disposable DSN and migrates explicitly.
    # The production CLI never migrates. All product calls in this test are real.
    from tools.run_unit_tests import _unit_environment
    root = Path(__file__).resolve().parents[3]
    cli = root / "backend" / "app" / "services" / "document_source_cli.py"
    wrapper = tmp_path / "fresh guarded document CLI.py"
    wrapper.write_text(f"REPOSITORY = {str(root)!r}\nSAVED_CLI = {str(cli)!r}\n" + _CHILD, encoding="utf-8")
    env = _unit_environment(tmp_path)
    for key in list(env):
        if (key.startswith(("PG", "KNOWLEDGE_")) or key in (
                "LLM_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY", "ZEP_API_KEY",
                "MIROFISH_SECRET_SENTINEL", "PYTHONPATH", "LLM_BASE_URL", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")):
            env.pop(key, None)
    env["MIROFISH_APPSTORE_DSN"] = os.environ["PROJECT_STORE_POSTGRES_TEST_DSN"]
    def run(project, revision, path, digest, principal="owner", name="DOCX evidence"):
        completed = subprocess.run(
            [sys.executable, "-I", "-u", str(wrapper), "ingest-docx", "--principal", principal,
             "--project-id", str(project), "--source-revision", str(revision),
             "--source-name", name, "--document-path", str(path), "--expected-document-sha256", digest],
            cwd=tmp_path, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=40, check=False)
        assert completed.returncode in (0, 2)
        assert completed.stderr == b""
        assert len(completed.stdout) <= 10 * 1024 * 1024 + 1
        assert len(completed.stdout.splitlines()) == 1
        value = json.loads(completed.stdout.decode("utf-8"))
        assert completed.returncode == (0 if value["ok"] else 2)
        if not value["ok"]:
            assert set(value) == {"ok", "error"}
            assert str(path) not in completed.stdout.decode("utf-8")
            assert env["MIROFISH_APPSTORE_DSN"] not in completed.stdout.decode("utf-8")
        return value
    return run


def _project(factory):
    project = uuid4()
    ProjectStore(factory).create("owner", uuid4(), project, "proj_1", snapshot())
    return project


def test_actual_fresh_saved_cli_retains_exact_unicode_and_table_passages(factory, saved_cli, tmp_path):
    project, revision = _project(factory), uuid4()
    original_project = ProjectStore(factory).get("owner", project)
    payload = _package(_paragraph("A😀猫") + '<w:tbl><w:tr><w:tc><w:tcPr><w:gridSpan w:val="2"/><w:vMerge w:val="restart"/></w:tcPr>'
                       + _paragraph("雪") + _paragraph("café") + '</w:tc><w:tc><w:p/></w:tc></w:tr></w:tbl>')
    document = tmp_path / "雪 owned.docx"
    document.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    reply = saved_cli(project, revision, document, digest)
    assert reply["ok"]
    result = reply["result"]
    retained = SourceStore(factory).get_source("owner", project, revision)
    assert retained.text == "A😀猫\n\n雪\ncafé\t"
    assert result["text_sha256"] == retained.text_sha256 == hashlib.sha256(retained.text.encode("utf-8")).hexdigest()
    assert result["codepoint_length"] == retained.codepoint_length == len(retained.text)
    assert result["byte_length"] == retained.byte_length == len(retained.text.encode("utf-8"))
    assert len(result["blocks"]) == 3 and result["blocks"][2]["empty"]
    assert result["blocks"][2]["evidence_id"] is None
    assert result["blocks"][1]["grid_span"] == 2 and result["blocks"][1]["vertical_merge"] == "restart"
    assert len(retained.passages) == len(result["passages"]) == 2
    for passage, emitted in zip(retained.passages, result["passages"]):
        resolved = SourceStore(factory).resolve_evidence("owner", project, passage.evidence_id)
        assert emitted["evidence_id"] == str(resolved.evidence_id)
        assert emitted["excerpt"] == resolved.excerpt == retained.text[passage.start:passage.end]
        assert (emitted["start"], emitted["end"]) == (resolved.start, resolved.end)
        assert emitted["offset_unit"] == "unicode_codepoint"
        assert emitted["declared_page"] is None and emitted["excerpt_sha256"] == resolved.excerpt_sha256
        assert emitted["source_sha256"] == resolved.source_sha256 == retained.text_sha256
        assert emitted["source_recorded_at"] == retained.recorded_at.isoformat()
    assert result["input_hash_verified"] and result["document_sha256"] == digest
    assert not result["binary_retained"] and not result["binary_persistently_bound"] and not result["original_document_verified"]
    assert not result["graph_ingestion_executed"] and not result["ocr_performed"]
    assert result["excluded_parts"] == ["headers", "footers", "footnotes", "endnotes"]
    assert result["page_layout"] == result["semantic_quality"] == "unknown"
    assert saved_cli(project, revision, document, digest) == reply
    assert SourceStore(factory).get_source("owner", project, revision) == retained
    assert len(SourceStore(factory).list_sources("owner", project)) == 1
    assert ProjectStore(factory).get("owner", project) == original_project
    assert document.read_bytes() == payload


def test_actual_authority_denied_before_missing_or_existing_document(factory, saved_cli, tmp_path):
    project, revision = _project(factory), uuid4()
    missing = tmp_path / "absent.docx"
    assert saved_cli(project, revision, missing, "a" * 64, principal="foreign") == {"ok": False, "error": "document_source_denied"}
    existing = tmp_path / "existing.docx"
    existing.write_bytes(b"not a package")
    assert saved_cli(project, revision, existing, "a" * 64, principal="foreign") == {"ok": False, "error": "document_source_denied"}
    assert saved_cli(uuid4(), revision, missing, "a" * 64) == {"ok": False, "error": "document_source_denied"}
    assert SourceStore(factory).list_sources("owner", project) == []


def test_actual_bad_hash_conflict_and_failure_do_not_overwrite(factory, saved_cli, tmp_path):
    project, revision = _project(factory), uuid4()
    document = tmp_path / "input.docx"
    original = _package(_paragraph("first😀"))
    document.write_bytes(original)
    digest = hashlib.sha256(original).hexdigest()
    assert saved_cli(project, revision, document, "a" * 64) == {"ok": False, "error": "document_sha256_mismatch"}
    with pytest.raises(NotFound):
        SourceStore(factory).get_source("owner", project, revision)
    assert saved_cli(project, revision, document, digest)["ok"]
    retained = SourceStore(factory).get_source("owner", project, revision)
    assert saved_cli(project, revision, document, digest, name="changed") == {"ok": False, "error": "source_conflict"}
    changed = _package(_paragraph("changed猫"))
    document.write_bytes(changed)
    assert saved_cli(project, revision, document, hashlib.sha256(changed).hexdigest()) == {"ok": False, "error": "source_conflict"}
    assert SourceStore(factory).get_source("owner", project, revision) == retained
    assert document.read_bytes() == changed
    assert len(SourceStore(factory).list_sources("owner", project)) == 1


@pytest.mark.parametrize("kind", ["malformed", "unsupported", "blank", "passages", "excerpt", "text"],
                         ids=["malformed", "unsupported", "blank", "101-blocks", "excerpt-bytes", "utf8-text-bytes"])
def test_actual_parser_and_store_limits_write_no_source(factory, saved_cli, tmp_path, kind):
    project, revision = _project(factory), uuid4()
    payload = {
        "malformed": b"private invalid zip",
        "unsupported": _package('<w:altChunk/>'),
        "blank": _package('<w:p/>'),
        "passages": _package(_paragraph("x") * 101),
        "excerpt": _package(_paragraph("😀" * 8193)),
        "text": _package(_paragraph("😀" * 270000)),
    }[kind]
    document = tmp_path / "bounded.docx"
    document.write_bytes(payload)
    error = {"malformed": "malformed_document", "unsupported": "unsupported_document", "blank": "no_extractable_text"}.get(kind, "limit_exceeded")
    assert saved_cli(project, revision, document, hashlib.sha256(payload).hexdigest()) == {"ok": False, "error": error}
    assert SourceStore(factory).list_sources("owner", project) == []
    assert document.read_bytes() == payload


def test_actual_owned_linked_ancestor_refused(factory, saved_cli, tmp_path):
    project, revision = _project(factory), uuid4()
    real = tmp_path / "real"
    real.mkdir()
    document = real / "evidence.docx"
    payload = _package(_paragraph("safe"))
    document.write_bytes(payload)
    linked = tmp_path / "linked"
    try:
        linked.symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip("host does not permit directory symlink creation")
    assert saved_cli(project, revision, linked / document.name, hashlib.sha256(payload).hexdigest()) == {"ok": False, "error": "invalid_source"}
    assert SourceStore(factory).list_sources("owner", project) == []
    assert document.read_bytes() == payload


def test_actual_exact_100_passages_and_excerpt_byte_limit(factory, saved_cli, tmp_path):
    project, revision = _project(factory), uuid4()
    payload = _package(_paragraph("😀" * 8192) + _paragraph("猫") * 99)
    document = tmp_path / "exact-passages.docx"
    document.write_bytes(payload)
    reply = saved_cli(project, revision, document, hashlib.sha256(payload).hexdigest())
    assert reply["ok"]
    record = SourceStore(factory).get_source("owner", project, revision)
    assert len(record.passages) == 100
    assert len(record.passages[0].excerpt.encode("utf-8")) == 32768
    assert len(reply["result"]["passages"]) == 100


def test_actual_exact_one_mib_utf8_retained_source(factory, saved_cli, tmp_path):
    project, revision = _project(factory), uuid4()
    # 31 two-newline delimiters add 62 bytes. The final cell's 64-byte
    # reduction plus 2 literal ASCII bytes makes the full text exactly 1 MiB.
    body = _paragraph("😀" * 8192) * 31 + _paragraph("😀" * 8176 + "aa")
    payload = _package(body)
    document = tmp_path / "exact-text.docx"
    document.write_bytes(payload)
    reply = saved_cli(project, revision, document, hashlib.sha256(payload).hexdigest())
    assert reply["ok"]
    record = SourceStore(factory).get_source("owner", project, revision)
    assert record.byte_length == len(record.text.encode("utf-8")) == 1024 * 1024
    assert len(record.passages) == 32
    assert reply["result"]["byte_length"] == record.byte_length
    assert all(len(p.excerpt.encode("utf-8")) <= 32768 for p in record.passages)
