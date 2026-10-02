"""Source-authored strict PDF child admission and cold-start regressions."""
import base64
import hashlib
import json
import subprocess
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest

from mirofish_knowledge.source_library import SourceLibrary, SourceError, validate_payload, encoded


def pdf_payload(binary=b"%PDF-1.7\nwire admission fixture\n%%EOF"):
    return {"schema_version": 1, "source_revision": str(uuid4()), "source_name": "PDF 猫",
        "format": "pdf", "content": base64.b64encode(binary).decode(),
        "input_sha256": hashlib.sha256(binary).hexdigest()}


@pytest.mark.parametrize("change", [
    lambda p: p.update(extra="path"), lambda p: p.update(schema_version=True),
    lambda p: p.update(format="docx"), lambda p: p.update(input_sha256="0" * 64),
    lambda p: p.update(content=p["content"] + "="), lambda p: p.update(content="%%%"),
    lambda p: p.update(content="x" * 2796205), lambda p: p.update(source_name="\x00"),
])
def test_pdf_child_rejects_before_authority_or_parser(change, monkeypatch):
    import mirofish_storage.pdf as parser
    monkeypatch.setattr(parser, "extract_pdf", lambda binary: pytest.fail("parser reached"))
    library = SourceLibrary(object(), connection_factory=lambda: pytest.fail("connection reached"))
    payload = pdf_payload()
    change(payload)
    with pytest.raises(ValueError):
        library.execute("retain_pdf", payload)


def test_pdf_child_authority_denial_precedes_native_extraction(monkeypatch):
    import mirofish_storage.pdf as parser
    monkeypatch.setattr(parser, "extract_pdf", lambda binary: pytest.fail("parser reached"))
    library = SourceLibrary(object(), connection_factory=lambda: pytest.fail("connection reached"))
    def denied(): raise SourceError("source_denied")
    monkeypatch.setattr(library, "authorize", denied)
    with pytest.raises(SourceError) as error:
        library.execute("retain_pdf", pdf_payload())
    assert error.value.code == "source_denied"


def test_pdf_child_payload_exact_byte_hash_and_schema():
    value = pdf_payload()
    validate_payload("retain_pdf", value)
    assert len(encoded(value)) < 4 * 1024 * 1024
    for binary in [b"missing header\n%%EOF", b"%PDF-1.7\ntruncated", b"%PDF-" + b"x" * (2 * 1024 * 1024)]:
        with pytest.raises(ValueError):
            validate_payload("retain_pdf", pdf_payload(binary))


def test_cold_source_and_pdf_module_imports_are_native_and_sdk_free(tmp_path):
    code = r'''
import importlib.abc,sys
class Deny(importlib.abc.MetaPathFinder):
    def find_spec(self,fullname,path=None,target=None):
        if fullname.split('.')[0] in {'pymupdf','fitz','graphiti_core','openai','neo4j','camel','oasis','temporalio'} or fullname in {'mirofish_knowledge.provider','mirofish_knowledge.read_runtime'}:
            raise AssertionError('forbidden cold runtime import')
sys.meta_path.insert(0,Deny())
import mirofish_knowledge.source_bootstrap
import mirofish_storage.pdf
assert 'pymupdf' not in sys.modules and 'fitz' not in sys.modules
print('cold-source-pdf')
'''
    result = subprocess.run([sys.executable, "-I", "-c", code], cwd=tmp_path,
        capture_output=True, timeout=30, check=False)
    assert result.returncode == 0, "cold installed source/PDF import failed"
    assert result.stdout.strip() == b"cold-source-pdf"


def test_missing_pdf_profile_is_unavailable_without_storage_mutation(monkeypatch):
    scope = SimpleNamespace(project_id=uuid4())
    library = SourceLibrary(SimpleNamespace(principal="owner"),
        connection_factory=lambda: pytest.fail("storage mutation reached"))
    monkeypatch.setattr(library, "authorize", lambda: scope)
    monkeypatch.setitem(sys.modules, "pymupdf", None)
    with pytest.raises(SourceError) as error:
        library.execute("retain_pdf", pdf_payload())
    assert error.value.code == "source_unavailable"


def test_authority_rechecked_after_pdf_extraction_before_storage_mutation(monkeypatch):
    import mirofish_storage.pdf as parser
    scope = SimpleNamespace(project_id=uuid4())
    library = SourceLibrary(SimpleNamespace(principal="owner"),
        connection_factory=lambda: pytest.fail("storage mutation reached"))
    calls = []
    def authorize():
        calls.append("authorize")
        if len(calls) == 3: raise SourceError("source_denied")
        return scope
    def extract(binary):
        calls.append("extract")
        assert calls == ["authorize", "extract"]
        return parser.PdfExtraction("猫", ({"page": 1, "start": 0, "end": 1,
            "empty": False, "excerpt_sha256": hashlib.sha256("猫".encode()).hexdigest()},), 1, 0)
    monkeypatch.setattr(library, "authorize", authorize)
    monkeypatch.setattr(parser, "extract_pdf", extract)
    with pytest.raises(SourceError) as error:
        library.execute("retain_pdf", pdf_payload())
    assert error.value.code == "source_denied"
    assert calls == ["authorize", "extract", "authorize"]
