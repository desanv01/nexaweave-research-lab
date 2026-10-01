"""Source tests for the one-shot parser process. Main executes these."""

import codecs
import json
import os
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


def _assert_owned_cleanup(owned_processes):
    assert owned_processes
    assert all(process.poll() is not None for process in owned_processes)
    assert not [
        thread for thread in threading.enumerate()
        if thread.name.startswith("mirofish-parser-")
    ]


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


def test_real_pdf_malformed_encrypted_and_limits(tmp_path):
    import fitz

    source = tmp_path / "tiny.pdf"
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "hello")
    document.save(source)
    document.close()
    text = extract_text_isolated(source)
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
    monkeypatch.setenv("MIROFISH_SECRET_SENTINEL", "private-token")
    monkeypatch.setenv("HTTPS_PROXY", "private-proxy")
    _synthetic_worker(
        tmp_path,
        monkeypatch,
        "sys.stdin.buffer.read()\n"
        "text = '|'.join((str(os.getpid()), os.getcwd(), "
        "str('MIROFISH_SECRET_SENTINEL' in os.environ), "
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
        if thread.name == "mirofish-parser-reader":
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
        if thread.name == "mirofish-parser-reader":
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
        "MIROFISH_SECRET_SENTINEL=dotenv-secret\n", encoding="utf-8"
    )
    monkeypatch.setenv("MIROFISH_SECRET_SENTINEL", "parent-secret")
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
        "'secret': os.environ.get('MIROFISH_SECRET_SENTINEL')}\n"
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
