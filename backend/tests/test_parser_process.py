"""Source tests for the one-shot parser process. Main executes these."""

import codecs
import hashlib
import json
import os
import shutil
import sys
import threading
from pathlib import Path

import pytest

from app.utils import parser_process
from app.utils.file_parser import (
    MalformedDocumentError,
    ParseLimitError,
    ParseLimits,
    UnsupportedDocumentError,
)
from app.utils.parser_process import (
    ParserFailedError,
    ParserProtocolError,
    ParserTimeoutError,
    extract_scanned_pdf_text_isolated,
    extract_text_isolated,
)


def _synthetic_worker(tmp_path, monkeypatch, body):
    script = tmp_path / "synthetic worker.py"
    script.write_text("import json, os, sys, time\n" + body, encoding="utf-8")
    monkeypatch.setattr(parser_process, "_WORKER_SCRIPT", script)
    return script


@pytest.fixture
def owned_processes(monkeypatch):
    launched = []
    real_popen = parser_process.subprocess.Popen

    def capture(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        launched.append(process)
        return process

    monkeypatch.setattr(parser_process.subprocess, "Popen", capture)
    return launched


@pytest.fixture
def parser_owners(monkeypatch):
    owners = []
    real_owner = parser_process.OwnedProcess

    def capture():
        owner = real_owner()
        owners.append(owner)
        return owner

    monkeypatch.setattr(parser_process, "OwnedProcess", capture)
    return owners


def _assert_owned_cleanup(owned_processes):
    assert owned_processes
    assert all(process.poll() is not None for process in owned_processes)
    assert not [
        thread for thread in threading.enumerate()
        if thread.name.startswith("nexaweave-parser-")
    ]


def _assert_closed_windows_owner(owner):
    assert owner.tree_empty and owner.closed and owner.job is None
    assert owner.process.stdin.closed and owner.process.stdout.closed
    assert owner.process._handle.closed
    assert not Path(owner._directory_name).exists()


@pytest.mark.parametrize("cleanup_error", [OSError, parser_process.OwnedProcessError])
@pytest.mark.parametrize("interrupt", [None, KeyboardInterrupt, SystemExit])
def test_private_directory_cleanup_failure_maps_fixed_parser_error_and_preserves_interrupt(
    tmp_path, monkeypatch, owned_processes, cleanup_error, interrupt
):
    _synthetic_worker(tmp_path, monkeypatch,
                      "sys.stdin.buffer.read(); sys.stdout.buffer.write(b'{\"version\":1,\"text\":\"x\"}')\n")
    source = tmp_path / "private source.txt"
    source.write_text("x", encoding="utf-8")
    real_cleanup = parser_process.OwnedProcess.cleanup_private_directory
    removed = []

    def fail_after_actual_removal(owner, directory):
        real_cleanup(owner, directory)
        removed.append(Path(directory.name))
        if cleanup_error is OSError:
            raise OSError("private directory detail " + directory.name)
        raise cleanup_error(outcome_unknown=True)

    monkeypatch.setattr(parser_process.OwnedProcess, "cleanup_private_directory", fail_after_actual_removal)
    if interrupt is not None:
        real_start = threading.Thread.start

        def interrupt_reader_start(thread):
            real_start(thread)
            if thread.name == "nexaweave-parser-reader":
                raise interrupt()

        monkeypatch.setattr(parser_process.threading.Thread, "start", interrupt_reader_start)
    expected = ParserFailedError if interrupt is None else interrupt
    with pytest.raises(expected) as caught:
        extract_text_isolated(source)
    if interrupt is None:
        assert caught.value.code == "parser_failed" and str(caught.value) == "parser_failed"
        assert "private" not in str(caught.value)
    assert len(removed) == 1 and not removed[0].exists()
    _assert_owned_cleanup(owned_processes)


@pytest.mark.skipif(os.name != "nt", reason="real Windows venv/Job Object parser lifecycle")
@pytest.mark.parametrize("mode", ["success", "malformed", "overflow", "timeout", "root_exit"])
def test_windows_descendant_parser_cleanup_and_repeated_reuse(
    tmp_path, monkeypatch, owned_processes, mode
):
    assert sys.prefix != sys.base_prefix, "actual Windows venv redirector gate required"
    owners = []
    real_owner = parser_process.OwnedProcess

    class CaptureOwner(real_owner):
        def __init__(self):
            super().__init__()
            owners.append(self)

        def start(self, popen, args, **kwargs):
            self.directory = Path(kwargs['cwd'])
            return super().start(popen, args, **kwargs)

    monkeypatch.setattr(parser_process, "OwnedProcess", CaptureOwner)
    source = tmp_path / "source.txt"
    source.write_text("x", encoding="utf-8")
    for ordinal in range(2):
        marker = tmp_path / f"descendant-{ordinal}.txt"
        grandchild = (
            "import os,time; from pathlib import Path; "
            f"p=Path({str(marker)!r}); q=p.with_suffix('.tmp'); "
            "q.write_text(str(os.getpid())); q.replace(p); time.sleep(30)"
        )
        body = (
            "import subprocess\n"
            "sys.stdin.buffer.read()\n"
            f"child=subprocess.Popen([sys.executable,'-I','-u','-c',{grandchild!r}], "
            + ("stdout=subprocess.DEVNULL" if mode in {"success", "malformed"} else "stdout=sys.stdout")
            + ")\n"
            "end=time.monotonic()+5\n"
            f"while not os.path.exists({str(marker)!r}) and time.monotonic()<end: time.sleep(0.01)\n"
            f"assert os.path.exists({str(marker)!r})\n"
        )
        if mode == "success":
            body += "sys.stdout.buffer.write(b'{\"version\":1,\"text\":\"x\"}'); sys.stdout.buffer.flush()\n"
        elif mode == "malformed":
            body += "sys.stdout.buffer.write(b'not json'); sys.stdout.buffer.flush()\n"
        elif mode == "overflow":
            body += "sys.stdout.buffer.write(b'x'*5000); sys.stdout.buffer.flush(); time.sleep(30)\n"
        elif mode == "timeout":
            body += "time.sleep(30)\n"
        _synthetic_worker(tmp_path, monkeypatch, body)
        if mode == "success":
            assert extract_text_isolated(source, limits=ParseLimits(max_text_chars=1), timeout_seconds=7) == "x"
        else:
            error = ParserProtocolError if mode in {"malformed", "overflow"} else ParserTimeoutError
            with pytest.raises(error):
                extract_text_isolated(source, limits=ParseLimits(max_text_chars=1), timeout_seconds=7)
        assert marker.exists(), "failure must occur after the actual grandchild starts"
        owner = owners[-1]
        assert owner.tree_empty and owner.closed and owner.job is None
        assert owner.process.stdin.closed and owner.process.stdout.closed
        assert owner.process._handle.closed
        assert not owner.directory.exists()
        _assert_owned_cleanup(owned_processes)


@pytest.mark.skipif(os.name != "nt", reason="Windows preownership failure of actual suspended process")
@pytest.mark.parametrize("seam", ["assign", "resume", "verify_commit_limit"])
def test_windows_parser_startup_failure_has_no_child_code_or_retry(
    tmp_path, monkeypatch, owned_processes, parser_owners, seam
):
    from app.utils import owned_process
    marker = tmp_path / "must not execute.txt"
    _synthetic_worker(tmp_path, monkeypatch,
                      f"open({str(marker)!r}, 'w').write('executed')\n")

    def fail(self, process):
        raise owned_process.OwnedProcessError()

    job_type = owned_process._WindowsJob
    if seam == "verify_commit_limit":
        assignments, verified = [], []
        real_assign, real_verify = job_type.assign, job_type.verify_commit_limit

        def assign(job, process):
            real_assign(job, process)
            assignments.append((job, process))

        def refuse_mismatched_limit(job, expected_bytes):
            assert assignments == [(job, owned_processes[0])]
            assert job.active_count() == 1 and owned_processes[0].poll() is None
            assert expected_bytes == 268435456
            verified.append(expected_bytes)
            real_verify(job, expected_bytes - 4096)

        def forbid_resume(*_args):
            pytest.fail("a refused memory profile reached resume")

        monkeypatch.setattr(job_type, "assign", assign)
        monkeypatch.setattr(job_type, seam, refuse_mismatched_limit)
        monkeypatch.setattr(job_type, "resume", forbid_resume)
        with pytest.raises(ParserFailedError, match=r"^parser_failed$"):
            extract_scanned_pdf_text_isolated(tmp_path / "source.pdf", timeout_seconds=1)
        assert verified == [268435456] and assignments[0][0].handle is None
        assert len(parser_owners) == 1 and not parser_owners[0].started
        assert parser_owners[0].terminal_job_memory is None
        _assert_closed_windows_owner(parser_owners[0])
    else:
        monkeypatch.setattr(job_type, seam, fail)
        with pytest.raises(ParserFailedError):
            extract_text_isolated(tmp_path / "source.txt", timeout_seconds=1)
    assert not marker.exists() and len(owned_processes) == 1
    assert owned_processes[0]._handle.closed
    _assert_owned_cleanup(owned_processes)


def test_real_text_unicode_bom_and_path_with_spaces(tmp_path):
    source = tmp_path / "雪 source.txt"
    source.write_bytes("Café 雪\n".encode("utf-8"))
    assert extract_text_isolated(source) == "Café 雪\n"
    source.write_bytes("Café 雪\r\n".encode("utf-8"))
    assert extract_text_isolated(source) == "Café 雪\r\n"
    source.write_bytes(codecs.BOM_UTF8 + "雪".encode("utf-8"))
    assert extract_text_isolated(source) == "雪"
    source.write_bytes("雪".encode("utf-16"))
    assert extract_text_isolated(source) == "雪"


def test_real_fixed_docx_child_unicode_tables_and_safe_failures(tmp_path, owned_processes):
    from test_docx_extraction import package, paragraph

    source = tmp_path / "雪 body.docx"
    source.write_bytes(package(paragraph("A😀猫") + '<w:tbl><w:tr><w:tc>' + paragraph("雪") + '</w:tc><w:tc><w:p/></w:tc></w:tr></w:tbl>'))
    assert extract_text_isolated(source) == "A😀猫\n\n雪\t"
    with pytest.raises(ParseLimitError):
        extract_text_isolated(source, limits=ParseLimits(max_text_chars=3))
    source.write_bytes(package('<w:altChunk/>'))
    with pytest.raises(UnsupportedDocumentError):
        extract_text_isolated(source)
    source.write_bytes(b"private broken zip")
    with pytest.raises(MalformedDocumentError) as caught:
        extract_text_isolated(source)
    assert "private" not in str(caught.value)
    _assert_owned_cleanup(owned_processes)


def test_scanned_ocr_profile_refusal_and_qualified_success(
    tmp_path, monkeypatch, owned_processes, parser_owners
):
    import fitz
    from test_file_parser_bounds import _scanned_pdf_fixture

    with fitz.open() as staging:
        page = staging.new_page(width=432, height=216)
        page.insert_text((36, 80), "PUBLIC LIBRARY NOTICE", fontsize=24)
        page.insert_text((36, 135), "MEETING AT 10 AM", fontsize=24)
        pixmap = page.get_pixmap(dpi=200, colorspace=fitz.csGRAY, alpha=False)
        assert (pixmap.width, pixmap.height, pixmap.n) == (1200, 600, 1)
        samples = pixmap.samples
    pdf = _scanned_pdf_fixture(samples, b"q 432 0 0 216 0 0 cm /Im0 Do Q\n")
    source = tmp_path / "image-only.pdf"
    source.write_bytes(pdf)
    original_sha256 = hashlib.sha256(pdf).hexdigest()
    limits = ParseLimits(max_file_bytes=len(pdf), max_pdf_pages=1, max_text_chars=128)

    scratch = tmp_path / "child scratch"
    scratch.mkdir()
    monkeypatch.setattr(parser_process.tempfile, "tempdir", str(scratch))
    assert extract_text_isolated(source, limits=limits) == ""
    assert parser_owners[0].terminal_job_memory is None
    expected_children = 1

    if os.name == "nt":
        # Exercise the real missing-data response under the Windows memory profile.
        unprovisioned = tmp_path / "unprovisioned"
        unprovisioned.mkdir()
        installed_worker = parser_process._WORKER_SCRIPT
        for name in ("parser_worker.py", "file_parser.py"):
            shutil.copyfile(installed_worker.with_name(name), unprovisioned / name)
        with monkeypatch.context() as isolated_installation:
            isolated_installation.setattr(
                parser_process, "_WORKER_SCRIPT", unprovisioned / "parser_worker.py",
            )
            with pytest.raises(ParserFailedError, match=r"^parser_failed$"):
                extract_scanned_pdf_text_isolated(source, limits=limits)
        expected_children += 1
        assert parser_owners[-1].started
    else:
        with pytest.raises(ParserFailedError, match=r"^parser_failed$"):
            extract_scanned_pdf_text_isolated(source, limits=limits)
        assert parser_owners[-1].process is None and parser_owners[-1].closed

    # Only the separately admitted, provisioned native lane enables OCR.
    # Once enabled, missing English data or unusable OCR output fails the case.
    if os.environ.get("NEXAWEAVE_TEST_SCANNED_OCR") == "1":
        assert os.name == "nt", "qualified scanned OCR requires the Windows Job profile"
        text = extract_scanned_pdf_text_isolated(source, limits=limits)
        assert [" ".join(line.split()) for line in text.splitlines() if line.strip()] == [
            "PUBLIC LIBRARY NOTICE", "MEETING AT 10 AM",
        ]
        limit, process_peak, job_peak = parser_owners[-1].terminal_job_memory
        assert limit == 268435456 and 0 < process_peak <= job_peak <= limit
        expected_children += 1

    assert hashlib.sha256(source.read_bytes()).hexdigest() == original_sha256
    assert len(owned_processes) == expected_children
    assert all(process.returncode == 0 for process in owned_processes)
    if os.name == "nt":
        assert [owner.process for owner in parser_owners] == owned_processes
        for owner in parser_owners:
            _assert_closed_windows_owner(owner)
    _assert_owned_cleanup(owned_processes)
    assert not list(scratch.iterdir())


def test_real_pdf_malformed_encrypted_and_limits(tmp_path):
    import fitz

    source = tmp_path / "tiny.pdf"
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "hello")
    document.save(source)
    document.close()
    try:
        text = extract_text_isolated(source)
    except ParserFailedError as error:
        if os.name == "nt":
            context = error.__context__
            # Directory cleanup can add its own error and the earlier public
            # parser error before the tagged owner.stop() failure.
            for _ in range(3):
                if context is None:
                    break
                if isinstance(context, parser_process.OwnedProcessError):
                    stage = context._cleanup_stage
                    if stage in {
                        "job_snapshot_pin", "job_terminate", "job_process_wait", "job_active_zero",
                        "job_process_handle_close", "job_teardown", "job_close", "root_reap",
                        "io_thread_join", "pipe_close", "root_handle_close",
                    }:
                        error.add_note(f"windows_parser_cleanup_stage={stage}")
                        break
                context = context.__context__
        raise
    assert "hello" in text
    assert extract_text_isolated(
        source, limits=ParseLimits(max_text_chars=len(text), max_pdf_pages=1)
    ) == text
    with pytest.raises(ParseLimitError):
        extract_text_isolated(source, limits=ParseLimits(max_text_chars=2))
    with pytest.raises(ParseLimitError):
        extract_text_isolated(source, limits=ParseLimits(max_file_bytes=4))

    two_pages = tmp_path / "two-pages.pdf"
    document = fitz.open()
    document.new_page()
    document.new_page()
    document.save(two_pages)
    document.close()
    with pytest.raises(ParseLimitError):
        extract_text_isolated(two_pages, limits=ParseLimits(max_pdf_pages=1))

    malformed = tmp_path / "bad.pdf"
    malformed.write_bytes(b"not PDF")
    with pytest.raises(MalformedDocumentError) as caught:
        extract_text_isolated(malformed)
    assert str(malformed) not in str(caught.value)

    encrypted = tmp_path / "encrypted.pdf"
    document = fitz.open()
    document.new_page()
    document.save(
        encrypted,
        encryption=fitz.PDF_ENCRYPT_AES_256,
        owner_pw="owner",
        user_pw="reader",
    )
    document.close()
    with pytest.raises(MalformedDocumentError):
        extract_text_isolated(encrypted)


def test_real_missing_and_unsupported_safe_errors(tmp_path):
    missing = tmp_path / "private" / "secret.txt"
    with pytest.raises(FileNotFoundError) as caught:
        extract_text_isolated(missing)
    assert str(missing) not in str(caught.value)
    unsupported = tmp_path / "secret.exe"
    unsupported.write_bytes(b"private content")
    with pytest.raises(UnsupportedDocumentError) as caught:
        extract_text_isolated(unsupported)
    assert str(unsupported) not in str(caught.value)
    assert "private content" not in str(caught.value)


@pytest.mark.parametrize("bad", [True, False, 0, -1, 121, float("nan"), float("inf"), -float("inf"), "1", None])
def test_invalid_timeout_rejected_before_spawn(tmp_path, monkeypatch, bad):
    def forbidden(*_args, **_kwargs):
        pytest.fail("spawn attempted")

    monkeypatch.setattr(parser_process.subprocess, "Popen", forbidden)
    with pytest.raises(ValueError):
        extract_text_isolated(tmp_path / "source.txt", timeout_seconds=bad)


def test_invalid_path_limits_and_request_size_before_spawn(tmp_path, monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("spawn attempted")

    monkeypatch.setattr(parser_process.subprocess, "Popen", forbidden)
    with pytest.raises(TypeError):
        extract_text_isolated(tmp_path / "source.txt", limits={"max_files": 1})
    mutated = ParseLimits()
    object.__setattr__(mutated, "max_text_chars", True)
    with pytest.raises(ValueError):
        extract_text_isolated(tmp_path / "source.txt", limits=mutated)
    mutated = ParseLimits()
    object.__setattr__(mutated, "max_file_bytes", 0)
    with pytest.raises(ValueError):
        extract_text_isolated(tmp_path / "source.txt", limits=mutated)
    with pytest.raises(parser_process.InvalidSourceError):
        extract_text_isolated(123)
    with pytest.raises(ParseLimitError):
        extract_text_isolated("a" * 17_000 + ".txt")


def test_private_environment_pid_and_temp_cleanup(tmp_path, monkeypatch, owned_processes):
    monkeypatch.setenv("NEXAWEAVE_SECRET_SENTINEL", "private-token")
    monkeypatch.setenv("HTTPS_PROXY", "private-proxy")
    _synthetic_worker(
        tmp_path,
        monkeypatch,
        "sys.stdin.buffer.read()\n"
        "text = '|'.join((str(os.getpid()), os.getcwd(), "
        "str('NEXAWEAVE_SECRET_SENTINEL' in os.environ), "
        "str('HTTPS_PROXY' in os.environ), str('app' in sys.modules), "
        "str('PYTHONPATH' in os.environ)))\n"
        "sys.stdout.buffer.write(json.dumps({'version': 1, 'text': text}).encode())\n",
    )
    source = tmp_path / "source.txt"
    source.write_text("safe", encoding="utf-8")
    result = extract_text_isolated(source)
    pid, directory, secret, proxy, app_loaded, pythonpath = result.split("|")
    assert int(pid) != os.getpid()
    assert (secret, proxy, app_loaded, pythonpath) == ("False",) * 4
    assert not Path(directory).exists()
    _assert_owned_cleanup(owned_processes)


@pytest.mark.parametrize("body,error_type", [
    ("sys.stdin.buffer.read(); time.sleep(2)\n", ParserTimeoutError),
    (
        "sys.stdin.buffer.read(); sys.stdout.buffer.write(b'x' * 5000)\n",
        ParserProtocolError,
    ),
    ("sys.stdin.buffer.read(); sys.exit(2)\n", ParserFailedError),
    ("sys.stdin.buffer.read(); sys.stdout.buffer.write(b'\\xff')\n", ParserProtocolError),
    ("sys.stdin.buffer.read(); sys.stdout.buffer.write(b'not json')\n", ParserProtocolError),
    (
        "sys.stdin.buffer.read(); sys.stdout.buffer.write(b'{\"version\":1,\"version\":1,\"text\":\"x\"}')\n",
        ParserProtocolError,
    ),
    (
        "sys.stdin.buffer.read(); sys.stdout.buffer.write(b'{\"version\":1,\"text\":\"x\",\"extra\":1}')\n",
        ParserProtocolError,
    ),
    (
        "sys.stdin.buffer.read(); sys.stdout.buffer.write(b'{\"version\":2,\"text\":\"x\"}')\n",
        ParserProtocolError,
    ),
    (
        "sys.stdin.buffer.read(); sys.stdout.buffer.write(b'{\"version\":true,\"text\":\"x\"}')\n",
        ParserProtocolError,
    ),
    (
        "sys.stdin.buffer.read(); sys.stdout.buffer.write(b'{\"version\":1,\"text\":7}')\n",
        ParserProtocolError,
    ),
    (
        "sys.stdin.buffer.read(); sys.stdout.buffer.write(b'{\"version\":1,\"text\":\"xx\"}')\n",
        ParserProtocolError,
    ),
    (
        "sys.stdin.buffer.read(); sys.stdout.buffer.write(b'{\"version\":1,\"text\":\"x\"}{}')\n",
        ParserProtocolError,
    ),
    (
        "sys.stdin.buffer.read(); sys.stdout.buffer.write(b'{\"version\":1,\"text\":\"x\",\"error\":\"parser_failed\"}')\n",
        ParserProtocolError,
    ),
    (
        "sys.stdin.buffer.read(); sys.stdout.buffer.write(b'{\"version\":1,\"error\":\"unknown\"}')\n",
        ParserProtocolError,
    ),
    (
        "sys.stdin.buffer.read(); sys.stdout.buffer.write(b'{\"version\":1,\"text\":\"x\"}'); sys.exit(3)\n",
        ParserFailedError,
    ),
    (
        "sys.stdin.buffer.read(); sys.stdout.buffer.write(b'{\"version\":1,\"text\":\"x\"}'); "
        "sys.stdout.buffer.flush(); time.sleep(2)\n",
        ParserTimeoutError,
    ),
    (
        "sys.stdin.buffer.read(); sys.stderr.write('private content\\n'); sys.exit(3)\n",
        ParserFailedError,
    ),
])
def test_synthetic_failures_cleanup(
    tmp_path, monkeypatch, owned_processes, body, error_type
):
    _synthetic_worker(tmp_path, monkeypatch, body)
    source = tmp_path / "source.txt"
    source.write_text("x", encoding="utf-8")
    with pytest.raises(error_type) as caught:
        extract_text_isolated(
            source, limits=ParseLimits(max_text_chars=1), timeout_seconds=0.7
        )
    assert str(source) not in str(caught.value)
    assert "private content" not in str(caught.value)
    _assert_owned_cleanup(owned_processes)


def test_premature_stdin_close_is_classified_and_reaped(
    tmp_path, monkeypatch, owned_processes
):
    _synthetic_worker(tmp_path, monkeypatch, "os.close(0)\n")
    source = tmp_path / "source.txt"
    source.write_text("x", encoding="utf-8")
    with pytest.raises((ParserFailedError, ParserProtocolError)):
        extract_text_isolated(source, timeout_seconds=0.7)
    _assert_owned_cleanup(owned_processes)


def test_child_that_never_reads_large_request_is_reaped(
    tmp_path, monkeypatch, owned_processes
):
    _synthetic_worker(tmp_path, monkeypatch, "time.sleep(2)\n")
    source = tmp_path / "source.txt"
    source.write_bytes(b"x")
    # Three 3001-digit fields make the request larger than ordinary pipe
    # buffers while keeping the complete JSON request below 16 KiB.
    large = 10**3000
    limits = ParseLimits(
        max_file_bytes=large, max_pdf_pages=large, max_files=large
    )
    with pytest.raises(ParserTimeoutError):
        extract_text_isolated(source, limits=limits, timeout_seconds=0.7)
    _assert_owned_cleanup(owned_processes)


def test_cancellation_after_spawn_reaps_owned_child(
    tmp_path, monkeypatch, owned_processes
):
    _synthetic_worker(tmp_path, monkeypatch, "time.sleep(2)\n")
    source = tmp_path / "source.txt"
    source.write_bytes(b"x")
    real_start = threading.Thread.start

    def interrupt_after_start(thread):
        real_start(thread)
        if thread.name == "nexaweave-parser-reader":
            raise KeyboardInterrupt()

    monkeypatch.setattr(parser_process.threading.Thread, "start", interrupt_after_start)
    with pytest.raises(KeyboardInterrupt):
        extract_text_isolated(source, timeout_seconds=0.7)
    _assert_owned_cleanup(owned_processes)


def test_second_io_thread_start_failure_reaps_owned_child(
    tmp_path, monkeypatch, owned_processes
):
    _synthetic_worker(tmp_path, monkeypatch, "time.sleep(2)\n")
    source = tmp_path / "source.txt"
    source.write_bytes(b"x")
    real_start = threading.Thread.start

    def fail_reader_start(thread):
        if thread.name == "nexaweave-parser-reader":
            raise RuntimeError("reader start failed")
        real_start(thread)

    monkeypatch.setattr(parser_process.threading.Thread, "start", fail_reader_start)
    with pytest.raises(ParserFailedError):
        extract_text_isolated(source, timeout_seconds=0.7)
    _assert_owned_cleanup(owned_processes)


def test_worker_rejects_bad_requests_without_app_startup(tmp_path):
    worker = Path(parser_process.__file__).with_name("parser_worker.py")
    # The fixed worker is launched directly with -I; protocol validation runs
    # before it loads the sibling parser module.
    import subprocess

    cases = [
        b'{"version":1,"version":1,"file_path":"/x","limits":{}}',
        b'{"version":true,"file_path":"/x","limits":{}}',
        b'{"version":1,"file_path":"/x","limits":{},"extra":1}',
        b'{"version":1,"file_path":"/x","limits":{"unknown":1}}',
        b'{"version":1,"file_path":"/x","limits":{"max_files":1,"max_files":2}}',
        b'{"version":1,"file_path":"/x","limits":NaN}',
        b'[' * 200 + b']' * 200,
        b'x' * (16 * 1024 + 1),
    ]
    for request in cases:
        result = subprocess.run(
            [sys.executable, "-I", "-u", str(worker)],
            input=request,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd=tmp_path,
            timeout=5,
            check=True,
        )
        assert json.loads(result.stdout) == {
            "version": 1, "error": "parser_protocol_error"
        }


def test_fixed_worker_run_does_not_start_app_or_load_dotenv(tmp_path, monkeypatch):
    import subprocess

    worker = Path(parser_process.__file__).with_name("parser_worker.py")
    source = tmp_path / "source.txt"
    source.write_bytes("雪".encode("utf-8"))
    (tmp_path / ".env").write_text(
        "NEXAWEAVE_SECRET_SENTINEL=dotenv-secret\n", encoding="utf-8"
    )
    monkeypatch.setenv("NEXAWEAVE_SECRET_SENTINEL", "parent-secret")
    wrapper = tmp_path / "inspect fixed worker.py"
    wrapper.write_text(
        "import importlib.util, json, os, sys\n"
        f"spec = importlib.util.spec_from_file_location('_fixed_worker', {str(worker)!r})\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "sys.modules[spec.name] = module\n"
        "spec.loader.exec_module(module)\n"
        "result = module._run()\n"
        "blocked = ('app', 'flask', 'dotenv', 'openai', 'camel')\n"
        "imports = [name for name in sys.modules if any("
        "name == item or name.startswith(item + '.') for item in blocked)]\n"
        "report = {'result': result, 'imports': imports, "
        "'secret': os.environ.get('NEXAWEAVE_SECRET_SENTINEL')}\n"
        "sys.stdout.buffer.write(json.dumps(report, ensure_ascii=False).encode('utf-8'))\n",
        encoding="utf-8",
    )
    request = {
        "version": 1,
        "file_path": str(source.resolve()),
        "limits": vars(ParseLimits()),
    }
    completed = subprocess.run(
        [sys.executable, "-I", "-u", str(wrapper)],
        input=json.dumps(request, ensure_ascii=False).encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        cwd=tmp_path,
        env=parser_process._private_environment(str(tmp_path)),
        timeout=5,
        check=True,
    )
    report = json.loads(completed.stdout.decode("utf-8"))
    assert report == {
        "result": {"version": 1, "text": "雪"},
        "imports": [],
        "secret": None,
    }
