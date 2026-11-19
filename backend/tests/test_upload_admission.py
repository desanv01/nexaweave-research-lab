"""Source tests for ontology upload admission and bounded persistence."""

import io
import os
import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import request
from werkzeug.datastructures import FileStorage, MultiDict

from app import create_app
from app.api import graph as graph_api
from app.models import project as project_module
from app.models.project import ProjectManager, ProjectStatus
from app.utils.file_parser import MalformedDocumentError, ParseLimitError, UnsupportedDocumentError
from app.utils.llm_client import LLMResponseError
from app.utils.parser_process import (
    ParserFailedError, ParserProtocolError, ParserTimeoutError,
)
from app.utils.upload_admission import (
    DEFAULT_UPLOAD_POLICY, UploadAdmissionError, UploadLimitError, UploadWriteError,
)
from app.utils.upload_admission import admit_ontology_upload


@pytest.fixture
def route(tmp_path, monkeypatch):
    root = tmp_path / "projects"
    monkeypatch.setattr(ProjectManager, "PROJECTS_DIR", str(root))
    calls = {"constructed": 0, "generated": 0, "kwargs": None}

    class CountingGenerator:
        def __init__(self):
            calls["constructed"] += 1

        def generate(self, **kwargs):
            calls["generated"] += 1
            calls["kwargs"] = kwargs
            return {
                "entity_types": [{"name": "Person"}],
                "edge_types": [],
                "analysis_summary": "safe summary",
            }

    monkeypatch.setattr(graph_api, "OntologyGenerator", CountingGenerator)
    app = create_app()
    app.config.update(TESTING=True)
    return app.test_client(), root, calls


def _post(client, files=None, **fields):
    data = {"simulation_requirement": "Investigate discussion"}
    data.update(fields)
    data["files"] = files if files is not None else [(io.BytesIO(b"hello"), "one.txt")]
    return client.post(
        "/api/graph/ontology/generate",
        data=data,
        content_type="multipart/form-data",
    )


def _saved_project(response):
    project_id = response.json["data"]["project_id"]
    return ProjectManager.get_project(project_id)


def test_valid_unicode_single_and_multiple_use_real_isolated_parser(route):
    client, _root, calls = route
    response = _post(
        client,
        files=[
            (io.BytesIO("雪 and café".encode("utf-8")), "雪.txt"),
            (io.BytesIO(b"# Heading\nsecond"), "notes.markdown"),
        ],
        project_name="研究",
    )
    assert response.status_code == 200
    data = response.json["data"]
    assert set(data) == {
        "project_id", "project_name", "ontology", "analysis_summary",
        "files", "total_text_length",
    }
    assert data["project_name"] == "研究"
    assert len(data["files"]) == 2
    assert calls["constructed"] == calls["generated"] == 1
    assert calls["kwargs"]["document_texts"] == ["雪 and café", "# Heading\nsecond"]
    project = _saved_project(response)
    assert project.status == ProjectStatus.ONTOLOGY_GENERATED
    assert project.total_text_length == len(ProjectManager.get_extracted_text(project.project_id))


def _scanned_upload_pdf(samples):
    # Build the fixed raw-grayscale profile without importing the private OCR fixture.
    content = b"q 432 0 0 216 0 0 cm /Im0 Do Q\n"
    objects = (
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 432 216]"
        b" /Resources << /XObject << /Im0 4 0 R >> >> /Contents 5 0 R >>",
        (b"<< /Type /XObject /Subtype /Image /Width 1200 /Height 600"
         b" /ColorSpace /DeviceGray /BitsPerComponent 8 /Length %d >>\nstream\n"
         % len(samples)) + samples + b"\nendstream",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
    )
    pdf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(pdf))
        pdf.extend(f"{number} 0 obj\n".encode("ascii") + body + b"\nendobj\n")
    xref = len(pdf)
    pdf.extend(b"xref\n0 6\n0000000000 65535 f \n")
    for offset in offsets:
        pdf.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    pdf.extend(f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("ascii"))
    return bytes(pdf)


def test_real_tiny_pdf_uses_isolated_parser(route, monkeypatch):
    import fitz

    client, _root, calls = route
    recovered_text = "PUBLIC LIBRARY NOTICE\nMEETING AT 10 AM"
    attempts = []
    native_recognize = None
    if os.environ.get("NEXAWEAVE_TEST_SCANNED_OCR") == "1":
        assert os.name == "nt", "qualified scanned OCR requires the Windows Job profile"
        native_recognize = getattr(graph_api.parser_process, "extract_scanned_pdf_text_isolated", None)
        assert callable(native_recognize), "qualified scanned OCR requires the staged parser"

    def recognize(file_path, *, limits, timeout_seconds):
        assert 0 < timeout_seconds <= graph_api.UPLOAD_POLICY.child_seconds
        assert Path(file_path).stat().st_size <= limits.max_file_bytes
        attempts.append(Path(file_path))
        if native_recognize is not None:
            return native_recognize(file_path, limits=limits, timeout_seconds=timeout_seconds)
        return recovered_text

    # Hosted checks use the counted stub. The qualified opt-in forwards the
    # positive scan to the same callable used by the upload route.
    monkeypatch.setattr(graph_api.parser_process, "extract_scanned_pdf_text_isolated", recognize, raising=False)
    monkeypatch.setattr(graph_api.Config, "SCANNED_PDF_OCR_ENABLED", True)
    with fitz.open() as pdf:
        page = pdf.new_page()
        page.insert_text((72, 72), "pdf evidence")
        payload = pdf.tobytes()
    response = _post(client, files=[(io.BytesIO(payload), "evidence.pdf")])
    assert response.status_code == 200
    assert "pdf evidence" in calls["kwargs"]["document_texts"][0]
    assert attempts == []

    with fitz.open() as pdf:
        page = pdf.new_page(width=432, height=216)
        for y, line in zip((80, 135), recovered_text.splitlines(), strict=True):
            page.insert_text((36, y), line, fontsize=24)
        image = page.get_pixmap(dpi=200, colorspace=fitz.csGRAY, alpha=False)
        assert (image.width, image.height, image.n) == (1200, 600, 1)
        scanned = _scanned_upload_pdf(image.samples)

    def post_scan():
        return _post(client, files=[(io.BytesIO(scanned), "notice.pdf")])

    monkeypatch.setattr(graph_api.Config, "SCANNED_PDF_OCR_ENABLED", False)
    response = post_scan()
    assert response.status_code == 422 and response.json["error_code"] == "no_extractable_text"
    assert attempts == [] and calls["constructed"] == calls["generated"] == 1

    monkeypatch.setattr(graph_api.Config, "SCANNED_PDF_OCR_ENABLED", True)
    response = post_scan()
    assert response.status_code == 200
    assert calls["constructed"] == calls["generated"] == 2
    extracted, = calls["kwargs"]["document_texts"]
    if native_recognize is None:
        assert extracted == recovered_text
    else:
        recognized_lines = [" ".join(line.split()) for line in extracted.splitlines() if line.strip()]
        assert recognized_lines == recovered_text.splitlines()
    saved_upload, = attempts
    assert saved_upload.read_bytes() == scanned
    project = _saved_project(response)
    assert project.status == ProjectStatus.ONTOLOGY_GENERATED
    combined = f"\n\n=== notice.pdf ===\n{extracted}"
    assert ProjectManager.get_extracted_text(project.project_id) == combined
    assert project.total_text_length == response.json["data"]["total_text_length"] == len(combined)

    def unsupported(*_args, **_kwargs):
        raise UnsupportedDocumentError()

    monkeypatch.setattr(graph_api.parser_process, "extract_scanned_pdf_text_isolated", unsupported)
    response = post_scan()
    assert response.status_code == 422 and response.json["error_code"] == "no_extractable_text"

    monkeypatch.delattr(graph_api.parser_process, "extract_scanned_pdf_text_isolated")
    response = post_scan()
    assert response.status_code == 503 and response.json["error_code"] == "parser_unavailable"
    failed = _saved_project(response)
    assert failed.status == ProjectStatus.FAILED
    assert ProjectManager.get_extracted_text(failed.project_id) is None
    assert calls["constructed"] == calls["generated"] == 2


def test_docx_upload_uses_actual_parser_before_offline_generator(route):
    from test_docx_extraction import package, paragraph

    client, _root, calls = route
    payload = package(paragraph("A😀猫") + '<w:tbl><w:tr><w:tc>' + paragraph("雪") + '</w:tc></w:tr></w:tbl>')
    response = _post(client, files=[(io.BytesIO(payload), "evidence.DOCX")])
    assert response.status_code == 200
    assert calls["constructed"] == calls["generated"] == 1
    assert calls["kwargs"]["document_texts"] == ["A😀猫\n\n雪"]
    # CountingGenerator is offline characterization, not ontology quality.
    assert _saved_project(response).status == ProjectStatus.ONTOLOGY_GENERATED


@pytest.mark.parametrize("kind", ["malformed", "unsupported", "blank", "oversized"])
def test_docx_rejection_precedes_ontology_construction(route, monkeypatch, kind):
    from test_docx_extraction import package, paragraph

    client, _root, calls = route
    payload = {"malformed": b"private invalid zip", "unsupported": package('<w:altChunk/>'),
               "blank": package('<w:p/>'), "oversized": package(paragraph("safe"))}[kind]
    if kind == "oversized":
        monkeypatch.setattr(graph_api, "UPLOAD_POLICY", replace(DEFAULT_UPLOAD_POLICY, max_file_bytes=len(payload) - 1))
    response = _post(client, files=[(io.BytesIO(payload), "evidence.docx")])
    assert response.status_code == (413 if kind == "oversized" else 422)
    assert response.json["error_code"] == ("upload_limit_exceeded" if kind == "oversized" else "no_extractable_text" if kind == "blank" else "invalid_document")
    assert calls["constructed"] == calls["generated"] == 0
    assert "private" not in response.get_data(as_text=True)


def test_real_pdf_page_boundary_exact_and_over(route, monkeypatch):
    import fitz

    client, _root, calls = route
    pdf = fitz.open()
    pdf.new_page()
    pdf.new_page()
    payload = pdf.tobytes()
    pdf.close()
    # Blank pages are parsed but rejected as having no usable text.
    monkeypatch.setattr(
        graph_api, "UPLOAD_POLICY",
        replace(DEFAULT_UPLOAD_POLICY, max_pdf_pages=2),
    )
    exact = _post(client, files=[(io.BytesIO(payload), "two.pdf")])
    assert exact.status_code == 422
    assert exact.json["error_code"] == "no_extractable_text"
    monkeypatch.setattr(
        graph_api, "UPLOAD_POLICY",
        replace(DEFAULT_UPLOAD_POLICY, max_pdf_pages=1),
    )
    over = _post(client, files=[(io.BytesIO(payload), "two.pdf")])
    assert over.status_code == 413
    assert over.json["error_code"] == "upload_limit_exceeded"
    assert calls["constructed"] == 0


@pytest.mark.parametrize("fields", [
    {"simulation_requirement": ""},
    {"simulation_requirement": "  \n "},
    {"project_name": "   "},
])
def test_invalid_scalar_fields_have_no_side_effects(route, fields):
    client, root, calls = route
    response = _post(client, **fields)
    assert response.status_code == 400
    assert response.json["success"] is False
    assert "error_code" in response.json
    assert calls["constructed"] == 0
    assert not root.exists()


@pytest.mark.parametrize("key,value", [
    ("simulation_requirement", "a" * 20_001),
    ("project_name", "a" * 201),
    ("additional_context", "a" * 20_001),
])
def test_field_length_limits_have_no_side_effects(route, key, value):
    client, root, calls = route
    response = _post(client, **{key: value})
    assert response.status_code == 413
    assert response.json["error_code"] == "upload_field_limit"
    assert calls["constructed"] == 0
    assert not root.exists()


@pytest.mark.parametrize("key", [
    "simulation_requirement", "project_name", "additional_context",
])
def test_duplicate_scalar_rejected_before_project(route, key):
    client, root, calls = route
    data = MultiDict([
        ("simulation_requirement", "valid"),
        ("files", (io.BytesIO(b"x"), "one.txt")),
        (key, "first"),
        (key, "second"),
    ])
    response = client.post(
        "/api/graph/ontology/generate", data=data,
        content_type="multipart/form-data",
    )
    assert response.status_code == 400
    assert response.json["error_code"] == "invalid_upload_fields"
    assert calls["constructed"] == 0
    assert not root.exists()


def test_nonfile_files_form_field_rejects_mixed_batch(route):
    client, root, calls = route
    data = MultiDict([
        ("simulation_requirement", "valid"),
        ("files", "not a file part"),
        ("files", (io.BytesIO(b"good"), "good.txt")),
    ])
    response = client.post(
        "/api/graph/ontology/generate", data=data,
        content_type="multipart/form-data",
    )
    assert response.status_code == 400
    assert response.json["error_code"] == "invalid_upload_file"
    assert calls["constructed"] == 0
    assert not root.exists()


@pytest.mark.parametrize("filename,status", [
    ("", 400), ("source.exe", 400), ("source", 400), (".pdf", 400),
    ("dir/source.txt", 400),
    ("secret\n.txt", 400), ("secret\x00.txt", 400),
    ("a" * 252 + ".txt", 413),
])
def test_invalid_filename_in_mixed_batch_rejects_whole_batch(route, filename, status):
    client, root, calls = route
    response = _post(
        client,
        files=[
            (io.BytesIO(b"good"), "good.txt"),
            (io.BytesIO(b"bad"), filename),
        ],
    )
    assert response.status_code == status
    assert response.json["success"] is False
    assert calls["constructed"] == 0
    assert not root.exists()


def _multipart_with_escaped_filename(filename):
    """Build wire bytes with a quoted-pair backslash in Content-Disposition."""
    boundary = b"U01dBoundary"
    escaped = filename.replace("\\", "\\\\").encode("ascii")
    body = b"".join([
        b"--" + boundary + b"\r\n",
        b'Content-Disposition: form-data; name="simulation_requirement"\r\n\r\n',
        b"Investigate discussion\r\n",
        b"--" + boundary + b"\r\n",
        b'Content-Disposition: form-data; name="files"; filename="good.txt"\r\n',
        b"Content-Type: text/plain\r\n\r\n",
        b"good\r\n",
        b"--" + boundary + b"\r\n",
        b'Content-Disposition: form-data; name="files"; filename="' + escaped + b'"\r\n',
        b"Content-Type: text/plain\r\n\r\n",
        b"bad\r\n",
        b"--" + boundary + b"--\r\n",
    ])
    return body, "multipart/form-data; boundary=U01dBoundary"


@pytest.mark.parametrize("filename", ["dir\\source.txt", "C:\\folder\\source.txt"])
def test_escaped_wire_backslash_rejects_entire_mixed_batch(route, filename):
    client, root, calls = route
    body, content_type = _multipart_with_escaped_filename(filename)
    with client.application.test_request_context(
        "/api/graph/ontology/generate", method="POST",
        data=body, content_type=content_type,
    ):
        parsed = request.files.getlist("files")
        assert [upload.filename for upload in parsed] == ["good.txt", filename]

    response = client.post(
        "/api/graph/ontology/generate", data=body, content_type=content_type,
    )
    assert response.status_code == 400
    assert response.json["success"] is False
    assert response.json["error_code"] == "invalid_upload_file"
    assert calls["constructed"] == calls["generated"] == 0
    assert not root.exists()


@pytest.mark.parametrize("filename", ["dir\\source.txt", "C:\\folder\\source.txt"])
def test_admission_rejects_parsed_backslash_filename(filename):
    form = MultiDict([("simulation_requirement", "Investigate discussion")])
    files = MultiDict([
        ("files", FileStorage(stream=io.BytesIO(b"good"), filename="good.txt")),
        ("files", FileStorage(stream=io.BytesIO(b"bad"), filename=filename)),
    ])
    with pytest.raises(UploadAdmissionError) as caught:
        admit_ontology_upload(form, files)
    assert caught.value.status == 400
    assert caught.value.code == "invalid_upload_file"


def test_no_files_and_too_many_files_rejected(route):
    client, root, calls = route
    empty = client.post(
        "/api/graph/ontology/generate",
        data={"simulation_requirement": "valid"},
        content_type="multipart/form-data",
    )
    assert empty.status_code == 400
    many = _post(
        client, files=[(io.BytesIO(b"x"), f"{index}.txt") for index in range(21)]
    )
    assert many.status_code == 413
    assert many.json["error_code"] == "upload_file_count_limit"
    assert calls["constructed"] == 0
    assert not root.exists()


def test_http_content_limit_maps_to_safe_413(route):
    client, root, calls = route
    client.application.config["MAX_CONTENT_LENGTH"] = 100
    response = _post(client, files=[(io.BytesIO(b"x" * 500), "source.txt")])
    assert response.status_code == 413
    assert response.json["error_code"] == "request_too_large"
    assert calls["constructed"] == 0
    assert not root.exists()


def test_actual_byte_and_aggregate_limits_exact_and_plus_one(route, monkeypatch):
    client, _root, calls = route
    policy = replace(
        DEFAULT_UPLOAD_POLICY,
        max_file_bytes=3, max_total_bytes=3,
    )
    monkeypatch.setattr(graph_api, "UPLOAD_POLICY", policy)
    exact = _post(
        client, files=[
            (io.BytesIO(b"a"), "a.txt"),
            (io.BytesIO(b"bb"), "b.txt"),
        ],
    )
    assert exact.status_code == 200
    assert calls["generated"] == 1
    over = _post(
        client, files=[
            (io.BytesIO(b"a"), "a.txt"),
            (io.BytesIO(b"bbb"), "b.txt"),
        ],
    )
    assert over.status_code == 413
    assert calls["generated"] == 1
    assert _saved_project(over).status == ProjectStatus.FAILED

    part = "\n\n=== a.txt ===\na"
    monkeypatch.setattr(
        graph_api, "UPLOAD_POLICY",
        replace(DEFAULT_UPLOAD_POLICY, max_aggregate_chars=len(part)),
    )
    assert _post(client, files=[(io.BytesIO(b"a"), "a.txt")]).status_code == 200
    monkeypatch.setattr(
        graph_api, "UPLOAD_POLICY",
        replace(DEFAULT_UPLOAD_POLICY, max_aggregate_chars=len(part) - 1),
    )
    capped = _post(client, files=[(io.BytesIO(b"a"), "a.txt")])
    assert capped.status_code == 413
    assert _saved_project(capped).status == ProjectStatus.FAILED


def test_saver_bounded_reads_forged_length_and_partial_cleanup(route):
    _client, root, _calls = route
    project = ProjectManager.create_project("sample")

    class TrackingStream(io.BytesIO):
        def __init__(self, payload):
            super().__init__(payload)
            self.read_sizes = []

        def read(self, count=-1):
            self.read_sizes.append(count)
            assert 0 < count <= 64 * 1024
            return super().read(count)

    exact_stream = TrackingStream(b"abc")
    storage = FileStorage(stream=exact_stream, filename="exact.txt", content_length=0)
    info = ProjectManager.save_file_to_project(
        project.project_id, storage, "exact.txt", max_bytes=3
    )
    assert info["size"] == 3
    assert max(exact_stream.read_sizes) == 4

    over_stream = TrackingStream(b"abcd")
    storage = FileStorage(stream=over_stream, filename="over.txt", content_length=0)
    before = set((root / project.project_id / "files").iterdir())
    with pytest.raises(UploadLimitError):
        ProjectManager.save_file_to_project(
            project.project_id, storage, "over.txt", max_bytes=3
        )
    assert set((root / project.project_id / "files").iterdir()) == before

    class FailingStream:
        def __init__(self):
            self.calls = 0

        def read(self, _count):
            self.calls += 1
            if self.calls == 1:
                return b"a"
            raise OSError("PRIVATE-SOURCE-CONTENT")

    storage = FileStorage(stream=FailingStream(), filename="fail.txt")
    with pytest.raises(UploadWriteError) as caught:
        ProjectManager.save_file_to_project(
            project.project_id, storage, "fail.txt", max_bytes=3
        )
    assert "PRIVATE" not in str(caught.value)
    assert set((root / project.project_id / "files").iterdir()) == before


@pytest.mark.parametrize("bad", [True, False, 0, -1, 1.5, None, "3"])
def test_saver_invalid_max_bytes_rejected_before_io(route, bad):
    _client, root, _calls = route
    storage = FileStorage(stream=io.BytesIO(b"x"), filename="source.txt")
    with pytest.raises(ValueError):
        ProjectManager.save_file_to_project(
            "proj_valid", storage, "source.txt", max_bytes=bad
        )
    assert not root.exists()


def test_saver_exclusive_file_never_overwrites(route, monkeypatch):
    _client, root, _calls = route
    project = ProjectManager.create_project("sample")
    monkeypatch.setattr(
        project_module.uuid, "uuid4",
        lambda: SimpleNamespace(hex="a" * 32),
    )
    destination = root / project.project_id / "files" / ("a" * 8 + ".txt")
    destination.write_bytes(b"untouched")
    storage = FileStorage(stream=io.BytesIO(b"new"), filename="source.txt")
    with pytest.raises(UploadWriteError):
        ProjectManager.save_file_to_project(
            project.project_id, storage, "source.txt"
        )
    assert destination.read_bytes() == b"untouched"


@pytest.mark.parametrize("failure,status,code", [
    (ParseLimitError, 413, "upload_limit_exceeded"),
    (MalformedDocumentError, 422, "invalid_document"),
    (ParserTimeoutError, 504, "parser_timeout"),
    (ParserFailedError, 503, "parser_unavailable"),
    (ParserProtocolError, 503, "parser_unavailable"),
    (FileNotFoundError, 503, "parser_unavailable"),
])
def test_parser_failure_maps_safely_and_restores_slot(
    route, monkeypatch, caplog, failure, status, code
):
    client, _root, calls = route
    slots = threading.BoundedSemaphore(2)
    monkeypatch.setattr(graph_api, "_ontology_upload_slots", slots)

    def fail(*_args, **_kwargs):
        raise failure("PRIVATE SOURCE PATH AND TEXT") if failure is FileNotFoundError else failure()

    monkeypatch.setattr(graph_api, "extract_text_isolated", fail)
    response = _post(client)
    assert response.status_code == status
    assert response.json["error_code"] == code
    assert "PRIVATE" not in response.get_data(as_text=True)
    assert "PRIVATE" not in caplog.text
    project = _saved_project(response)
    assert project.status == ProjectStatus.FAILED
    assert code in project.error
    assert ProjectManager.get_extracted_text(project.project_id) is None
    assert calls["constructed"] == calls["generated"] == 0
    assert slots.acquire(blocking=False)
    assert slots.acquire(blocking=False)
    slots.release()
    slots.release()


def test_blank_document_422_without_model_or_combined_text(route):
    client, _root, calls = route
    response = _post(client, files=[(io.BytesIO(b" \n "), "blank.txt")])
    assert response.status_code == 422
    assert response.json["error_code"] == "no_extractable_text"
    project = _saved_project(response)
    assert project.status == ProjectStatus.FAILED
    assert ProjectManager.get_extracted_text(project.project_id) is None
    assert calls["constructed"] == 0


def test_busy_returns_immediately_before_project_or_model(route, monkeypatch):
    client, root, calls = route
    slots = threading.BoundedSemaphore(2)
    slots.acquire()
    slots.acquire()
    monkeypatch.setattr(graph_api, "_ontology_upload_slots", slots)
    response = _post(client)
    assert response.status_code == 503
    assert response.json["error_code"] == "parser_busy"
    assert calls["constructed"] == 0
    assert not root.exists()
    slots.release()
    slots.release()


def test_slot_is_released_before_generator_constructor(route, monkeypatch):
    client, _root, _calls = route
    slots = threading.BoundedSemaphore(2)
    monkeypatch.setattr(graph_api, "_ontology_upload_slots", slots)
    observed = {"constructor": False}

    class InspectingGenerator:
        def __init__(self):
            assert slots.acquire(blocking=False)
            assert slots.acquire(blocking=False)
            slots.release()
            slots.release()
            observed["constructor"] = True

        def generate(self, **_kwargs):
            return {"entity_types": [], "edge_types": [], "analysis_summary": "ok"}

    monkeypatch.setattr(graph_api, "OntologyGenerator", InspectingGenerator)
    response = _post(client)
    assert response.status_code == 200
    assert observed["constructor"]


def test_parser_cancellation_releases_owned_slot(route, monkeypatch):
    client, _root, calls = route
    slots = threading.BoundedSemaphore(2)
    monkeypatch.setattr(graph_api, "_ontology_upload_slots", slots)

    def cancel(*_args, **_kwargs):
        raise KeyboardInterrupt()

    monkeypatch.setattr(graph_api, "extract_text_isolated", cancel)
    with pytest.raises(KeyboardInterrupt):
        _post(client)
    assert calls["constructed"] == 0
    projects = ProjectManager.list_projects(limit=None)
    assert len(projects) == 1
    assert projects[0].status == ProjectStatus.FAILED
    assert projects[0].error.startswith("upload_cancelled:")
    assert len(projects[0].files) == 1
    assert slots.acquire(blocking=False)
    assert slots.acquire(blocking=False)
    slots.release()
    slots.release()


def test_expired_phase_does_not_start_save_or_parser(route, monkeypatch):
    client, _root, calls = route
    ticks = iter((0, 91))
    monkeypatch.setattr(graph_api, "time", SimpleNamespace(monotonic=lambda: next(ticks)))

    def forbidden(*_args, **_kwargs):
        pytest.fail("save or parser started after deadline")

    monkeypatch.setattr(ProjectManager, "save_file_to_project", forbidden)
    monkeypatch.setattr(graph_api, "extract_text_isolated", forbidden)
    response = _post(client)
    assert response.status_code == 504
    assert _saved_project(response).status == ProjectStatus.FAILED
    assert calls["constructed"] == 0


def test_combined_storage_finishing_after_deadline_skips_generator(route, monkeypatch):
    client, _root, calls = route
    clock = [0]
    monkeypatch.setattr(
        graph_api, "time", SimpleNamespace(monotonic=lambda: clock[0])
    )
    monkeypatch.setattr(
        graph_api, "extract_text_isolated", lambda *_args, **_kwargs: "source text"
    )
    original_save = ProjectManager.save_extracted_text

    def finish_late(_cls, project_id, text):
        original_save(project_id, text)
        clock[0] = 91

    monkeypatch.setattr(
        ProjectManager, "save_extracted_text", classmethod(finish_late)
    )
    response = _post(client)
    assert response.status_code == 504
    assert response.json["error_code"] == "parser_timeout"
    assert _saved_project(response).status == ProjectStatus.FAILED
    assert calls["constructed"] == calls["generated"] == 0


def test_private_strings_not_logged_on_unexpected_extraction_failure(
    route, monkeypatch, caplog
):
    client, _root, calls = route

    def fail(*_args, **_kwargs):
        raise RuntimeError("PRIVATE SOURCE CONTENT")

    monkeypatch.setattr(graph_api, "extract_text_isolated", fail)
    response = _post(client, project_name="PRIVATE PROJECT")
    assert response.status_code == 503
    assert "PRIVATE" not in response.get_data(as_text=True)
    assert "PRIVATE" not in caplog.text
    assert calls["constructed"] == 0


def test_provider_failure_response_contract_is_preserved(route, monkeypatch):
    client, _root, _calls = route

    class FailingGenerator:
        def generate(self, **_kwargs):
            raise LLMResponseError(
                "LLM JSON output was truncated at the token limit",
                finish_reason="length",
            )

    monkeypatch.setattr(graph_api, "OntologyGenerator", FailingGenerator)
    response = _post(client)
    assert response.status_code == 502
    assert "token limit" in response.json["error"]
    assert _saved_project(response).status == ProjectStatus.FAILED
