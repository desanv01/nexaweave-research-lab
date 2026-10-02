"""Authored lifetime regressions. Main runs these; no provider/network calls."""

import ctypes
import importlib.util
import json
import os
import subprocess
import tempfile
import sys
import threading
import time
from pathlib import Path
from uuid import uuid4

import pytest


HELPER = Path(__file__).resolve().parents[1] / "app" / "utils" / "owned_process.py"


@pytest.fixture
def subject():
    spec = importlib.util.spec_from_file_location("standalone_owned_process", HELPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Pipe:
    closed = False

    def close(self):
        self.closed = True


class Process:
    pid = 123
    _handle = 456
    returncode = None

    def __init__(self):
        self.stdin, self.stdout, self.stderr = Pipe(), Pipe(), None
        self.killed = False

    def poll(self):
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -1

    terminate = kill

    def wait(self, timeout=None):
        if self.returncode is None:
            raise subprocess.TimeoutExpired("fixed", timeout)
        return self.returncode


class Job:
    def __init__(self):
        self.calls = []
        self.closed = False

    def assign(self, process):
        self.calls.append(("assign", process))

    def resume(self, process):
        self.calls.append(("resume", process))

    def terminate_and_wait(self, deadline):
        self.calls.append(("terminate", deadline))

    def close(self):
        self.closed = True


def windows_double(subject, monkeypatch, job):
    # Patch only this module's OS view; never mutate the shared os.name.
    from types import SimpleNamespace
    monkeypatch.setattr(subject, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(subject, "_WindowsJob", lambda: job)
    # Pure doubles have no actual Popen Handle.Close method.
    class Handle:
        closed = False

        def Close(self):
            self.closed = True

    process = Process()
    process._handle = Handle()
    return process


def test_suspended_assignment_precedes_resume_and_exited_root_still_terminates_job(subject, monkeypatch):
    job = Job()
    process = windows_double(subject, monkeypatch, job)
    calls = []

    def popen(args, **kwargs):
        calls.append(kwargs)
        assert not job.calls
        return process

    owner = subject.OwnedProcess()
    assert owner.start(popen, ["fixed"], creationflags=0) is process
    assert calls[0]["creationflags"] == subject.CREATE_SUSPENDED | subject.CREATE_NO_WINDOW
    assert [call[0] for call in job.calls] == ["assign", "resume"]
    process.returncode = 0
    owner.stop([])
    assert job.calls[-1][0] == "terminate" and job.closed
    assert owner.tree_empty and owner.closed
    assert process.stdin.closed and process.stdout.closed and process._handle.closed


@pytest.mark.parametrize("seam", ["assign", "resume"])
def test_handled_startup_failure_kills_suspended_root_closes_job_no_fallback(subject, monkeypatch, seam):
    job = Job()
    process = windows_double(subject, monkeypatch, job)
    launches = []

    def fail(_process):
        raise subject.OwnedProcessError()

    setattr(job, seam, fail)
    owner = subject.OwnedProcess()
    with pytest.raises(subject.OwnedProcessError):
        owner.start(lambda *a, **k: launches.append(k) or process, ["fixed"])
    assert len(launches) == 1 and process.killed and job.closed
    assert process.stdin.closed and process.stdout.closed and process._handle.closed


def test_job_creation_failure_never_spawns(subject, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(subject, "os", SimpleNamespace(name="nt"))

    def fail():
        raise subject.OwnedProcessError()

    monkeypatch.setattr(subject, "_WindowsJob", fail)
    with pytest.raises(subject.OwnedProcessError):
        subject.OwnedProcess().start(lambda *a, **k: pytest.fail("spawned"), ["fixed"])


def test_active_tree_cleanup_failure_is_reported_and_handle_closed(subject, monkeypatch):
    job = Job()
    process = windows_double(subject, monkeypatch, job)

    def stuck(deadline):
        raise subject.OwnedProcessError()

    job.terminate_and_wait = stuck
    owner = subject.OwnedProcess()
    owner.start(lambda *a, **k: process, ["fixed"])
    with pytest.raises(subject.OwnedProcessError) as caught:
        owner.stop([])
    assert caught.value.outcome_unknown and job.closed and process.killed
    assert not owner.tree_empty and not owner.closed


def test_live_io_thread_never_causes_unbounded_buffered_close(subject):
    class StuckThread:
        ident = 1

        def join(self, timeout):
            assert 0 <= timeout <= 0.01

        def is_alive(self):
            return True

    owner = subject.OwnedProcess()
    owner.process = Process()
    with pytest.raises(subject.OwnedProcessError):
        owner.stop([StuckThread()], timeout=0.01)
    assert not owner.process.stdout.closed and not owner.closed


class CleanupClock:
    def __init__(self):
        self.now = 100.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class TransientDirectory:
    """Explicit test double around the exact real TemporaryDirectory instance."""

    def __init__(self, tmp_path, errors):
        self.actual = tempfile.TemporaryDirectory(dir=tmp_path)
        self.name = self.actual.name
        self.errors = list(errors)
        self.calls = 0

    def cleanup(self):
        self.calls += 1
        if self.errors:
            code = self.errors.pop(0)
            error = OSError("private directory detail")
            error.winerror = code
            raise error
        self.actual.cleanup()


def cleanup_owner(subject, monkeypatch, directory, *, timeout=3.0):
    from types import SimpleNamespace
    clock = CleanupClock()
    monkeypatch.setattr(subject, 'time', clock)
    monkeypatch.setattr(subject, 'os', SimpleNamespace(name='nt'))
    owner = subject.OwnedProcess()
    owner.bind_private_directory(directory)
    owner._cwd = subject._path.normcase(subject._path.abspath(directory.name))
    owner.stop([], timeout=timeout)
    return owner, clock


@pytest.mark.parametrize('code', [5, 32])
def test_windows_private_directory_transient_retry_succeeds_with_remaining_budget(
    subject, tmp_path, monkeypatch, code
):
    directory = TransientDirectory(tmp_path, [code, code])
    owner, clock = cleanup_owner(subject, monkeypatch, directory)
    # Explicitly simulate completed real tree/root/pipe cleanup for this pure seam.
    owner.process = Process()
    owner.started = True
    owner.tree_empty = True
    try:
        clock.now += 2.5
        owner.cleanup_private_directory(directory)
        assert directory.calls == 3 and not Path(directory.name).exists()
        assert clock.sleeps == [0.01, 0.01]
        assert clock.now < owner.cleanup_deadline == 103.0
    finally:
        directory.actual.cleanup()


def test_windows_private_directory_transient_expiry_does_not_add_grace(
    subject, tmp_path, monkeypatch
):
    directory = TransientDirectory(tmp_path, [32] * 10)
    owner, clock = cleanup_owner(subject, monkeypatch, directory, timeout=0.025)
    try:
        with pytest.raises(subject.OwnedProcessError):
            owner.cleanup_private_directory(directory)
        assert directory.calls == 3 and Path(directory.name).exists()
        assert clock.now == owner.cleanup_deadline
        assert all(0 < delay <= 0.01 for delay in clock.sleeps)
        assert sum(clock.sleeps) <= 0.025
        before = directory.calls
        with pytest.raises(subject.OwnedProcessError):
            owner.cleanup_private_directory(directory)
        assert directory.calls == before + 1  # initial cleanup only, no fresh budget
        assert clock.now == owner.cleanup_deadline
    finally:
        directory.actual.cleanup()


def test_windows_private_directory_nontransient_never_retries(subject, tmp_path, monkeypatch):
    directory = TransientDirectory(tmp_path, [123])
    owner, clock = cleanup_owner(subject, monkeypatch, directory)
    try:
        with pytest.raises(OSError) as caught:
            owner.cleanup_private_directory(directory)
        assert caught.value.winerror == 123
        assert directory.calls == 1 and not clock.sleeps
    finally:
        directory.actual.cleanup()


@pytest.mark.parametrize('state', ['tree_unproved', 'owner_failed', 'no_stop'])
def test_windows_private_directory_unproven_owner_initial_attempt_only(
    subject, tmp_path, monkeypatch, state
):
    directory = TransientDirectory(tmp_path, [5])
    owner, clock = cleanup_owner(subject, monkeypatch, directory)
    owner.process = Process()
    owner.started = True
    if state == 'owner_failed':
        owner.tree_empty = True
        owner.closed = False
    elif state == 'no_stop':
        owner.tree_empty = True
        owner.cleanup_deadline = None
    try:
        with pytest.raises(OSError):
            owner.cleanup_private_directory(directory)
        assert directory.calls == 1 and not clock.sleeps
    finally:
        directory.actual.cleanup()


@pytest.mark.parametrize('foreign', ['object', 'cwd', 'name'])
def test_windows_private_directory_foreign_identity_is_never_removed(
    subject, tmp_path, monkeypatch, foreign
):
    directory = TransientDirectory(tmp_path, [32])
    other = TransientDirectory(tmp_path, [])
    owner, clock = cleanup_owner(subject, monkeypatch, directory)
    supplied = directory
    if foreign == 'object':
        supplied = other
    elif foreign == 'cwd':
        owner._cwd = subject._path.normcase(subject._path.abspath(other.name))
    else:
        directory.name = other.name
    try:
        with pytest.raises(subject.OwnedProcessError):
            owner.cleanup_private_directory(supplied)
        assert directory.calls == other.calls == 0 and not clock.sleeps
        assert Path(directory.actual.name).exists() and Path(other.actual.name).exists()
    finally:
        directory.actual.cleanup()
        other.actual.cleanup()


def test_windows_private_directory_handled_no_process_startup_can_retry(
    subject, tmp_path, monkeypatch
):
    directory = TransientDirectory(tmp_path, [32])
    owner, clock = cleanup_owner(subject, monkeypatch, directory)
    try:
        assert owner.process is None and not owner.started and not owner.tree_empty
        owner.cleanup_private_directory(directory)
        assert directory.calls == 2 and not Path(directory.name).exists()
        assert clock.sleeps == [0.01]
    finally:
        directory.actual.cleanup()


def test_repeated_stop_retains_first_cleanup_deadline(subject, monkeypatch):
    clock = CleanupClock()
    monkeypatch.setattr(subject, 'time', clock)
    owner = subject.OwnedProcess()
    owner.stop([])
    clock.now += 2.75
    owner.stop([])
    assert owner.cleanup_deadline == 103.0 and owner.cleanup_timeout == 3.0


def test_posix_private_directory_cleanup_error_never_retries(subject, tmp_path, monkeypatch):
    from types import SimpleNamespace
    directory = TransientDirectory(tmp_path, [32])
    owner, clock = cleanup_owner(subject, monkeypatch, directory)
    monkeypatch.setattr(subject, 'os', SimpleNamespace(name='posix'))
    try:
        with pytest.raises(OSError):
            owner.cleanup_private_directory(directory)
        assert directory.calls == 1 and not clock.sleeps
    finally:
        directory.actual.cleanup()


class Function:
    def __init__(self, body):
        self.body = body

    def __call__(self, *args):
        return self.body(*args)


def kernel_double(monkeypatch, subject, *, assignment=True, thread_owner=123,
                  resume_count=1, threads=(123,), set_limits=True):
    from types import SimpleNamespace
    closed, opened, resumed, flags = [], [], [], []
    position = [0]

    def entry(pointer):
        value = ctypes.cast(pointer, ctypes.POINTER(subject._ThreadEntry)).contents
        value.th32OwnerProcessID = threads[position[0]]
        value.th32ThreadID = 100 + position[0]

    def first(snapshot, pointer):
        if not threads:
            return 0
        entry(pointer)
        return 1

    def next_entry(snapshot, pointer):
        position[0] += 1
        if position[0] >= len(threads):
            return 0
        entry(pointer)
        return 1

    def limits(handle, kind, pointer, size):
        value = ctypes.cast(pointer, ctypes.POINTER(subject._ExtendedLimits)).contents
        flags.append(value.BasicLimitInformation.LimitFlags)
        return int(set_limits)

    api = SimpleNamespace(**{name: Function(body) for name, body in {
        "CreateJobObjectW": lambda security, name: 77,
        "SetInformationJobObject": limits,
        "AssignProcessToJobObject": lambda *a: int(assignment),
        "TerminateJobObject": lambda *a: 1,
        "QueryInformationJobObject": lambda *a: 1,
        "OpenProcess": lambda *a: 0,
        "IsProcessInJob": lambda *a: 0,
        "WaitForSingleObject": lambda *a: 0xffffffff,
        "CloseHandle": lambda handle: closed.append(handle) or 1,
        "CreateToolhelp32Snapshot": lambda *a: 88,
        "Thread32First": first, "Thread32Next": next_entry,
        "OpenThread": lambda access, inherit, tid: opened.append(tid) or 99,
        "GetProcessIdOfThread": lambda handle: thread_owner,
        "ResumeThread": lambda handle: resumed.append(handle) or resume_count,
    }.items()})
    monkeypatch.setattr(subject.ctypes, "WinDLL", lambda *a, **k: api, raising=False)
    monkeypatch.setattr(subject.ctypes, "get_last_error", lambda: 18, raising=False)
    return closed, opened, resumed, flags


def job_process_double(subject, monkeypatch, *, snapshots=((101, 102),),
                       foreign_pid=None, failed_open=None, failed_membership=None,
                       wait_result=0, assigned_overflow=False):
    closed, _, _, _ = kernel_double(monkeypatch, subject)
    job = subject._WindowsJob()
    clock = CleanupClock()
    monkeypatch.setattr(subject, 'time', clock)
    last_error = [0]
    queried, opened, membership, waited, terminated = [], [], [], [], []
    snapshot_index = [0]

    def query(handle, kind, pointer, size, returned):
        assert handle == 77
        if kind == 1:
            accounting = ctypes.cast(pointer, ctypes.POINTER(subject._Accounting)).contents
            accounting.ActiveProcesses = 0
            return 1
        assert kind == 3
        capacity = (size - 8) // ctypes.sizeof(subject.ULONG_PTR)
        buffer_type = subject._process_id_list_type(capacity)
        buffer = ctypes.cast(pointer, ctypes.POINTER(buffer_type)).contents
        pids = snapshots[min(snapshot_index[0], len(snapshots) - 1)]
        snapshot_index[0] += 1
        assigned = subject._MAX_JOB_PROCESSES + 1 if assigned_overflow else len(pids)
        buffer.NumberOfAssignedProcesses = assigned
        buffer.NumberOfProcessIdsInList = min(capacity, len(pids))
        for index, pid in enumerate(pids[:capacity]):
            buffer.ProcessIdList[index] = pid
        queried.append((kind, capacity, assigned))
        if assigned > capacity:
            last_error[0] = 234
            return 0
        return 1

    def open_process(access, inherit, pid):
        assert access == 0x00101000 and not inherit  # no termination rights
        opened.append(pid)
        if pid == failed_open:
            last_error[0] = 87
            return 0
        return pid + 10000

    def in_job(handle, job_handle, pointer):
        assert job_handle == 77
        pid = handle - 10000
        membership.append(pid)
        if pid == failed_membership:
            return 0
        ctypes.cast(pointer, ctypes.POINTER(ctypes.c_int)).contents.value = int(pid != foreign_pid)
        return 1

    def wait(handle, milliseconds):
        assert handle - 10000 != foreign_pid
        waited.append((handle - 10000, milliseconds))
        if wait_result == 258:
            clock.now += milliseconds / 1000
        return wait_result

    job.api.QueryInformationJobObject.body = query
    job.api.OpenProcess.body = open_process
    job.api.IsProcessInJob.body = in_job
    job.api.WaitForSingleObject.body = wait
    job.api.TerminateJobObject.body = lambda handle, code: terminated.append((handle, code)) or 1
    monkeypatch.setattr(subject.ctypes, 'get_last_error', lambda: last_error[0])
    return job, clock, queried, opened, membership, waited, terminated, closed


def test_job_process_list_growth_pins_only_verified_job_members_and_closes_before_job(
    subject, monkeypatch
):
    pids = tuple(range(101, 141))
    job, clock, queried, opened, membership, waited, terminated, closed = job_process_double(
        subject, monkeypatch, snapshots=(pids,))
    job.terminate_and_wait(clock.now + 3)
    assert [entry[1] for entry in queried] == [16, 40]
    assert tuple(opened) == tuple(membership) == pids
    assert tuple(pid for pid, _ in waited) == pids
    assert all(0 <= milliseconds <= 3000 for _, milliseconds in waited)
    assert terminated == [(77, 1)] and closed == [pid + 10000 for pid in pids]
    job.close()
    assert closed[-1] == 77


def test_job_process_list_entry_overflow_still_terminates_no_process_discovery(
    subject, monkeypatch
):
    job, clock, queried, opened, membership, waited, terminated, closed = job_process_double(
        subject, monkeypatch, assigned_overflow=True)
    with pytest.raises(subject.OwnedProcessError):
        job.terminate_and_wait(clock.now + 3)
    assert len(queried) == 1 and not opened and not membership and not waited
    assert terminated == [(77, 1)] and not closed
    job.close()
    assert closed == [77]


def test_job_process_list_changing_size_uses_bounded_growth_before_complete_capture(subject, monkeypatch):
    snapshots = tuple(tuple(range(101, 101 + size)) for size in (20, 40, 80))
    job, clock, queried, opened, membership, waited, terminated, closed = job_process_double(
        subject, monkeypatch, snapshots=snapshots)
    job.terminate_and_wait(clock.now + 3)
    assert [entry[1] for entry in queried] == [16, 32, 64, 128]
    assert len(opened) == len(membership) == len(waited) == len(closed) == 80
    assert terminated == [(77, 1)]
    job.close()


@pytest.mark.parametrize('failure', ['foreign', 'membership_api', 'open_still_listed'])
def test_job_process_list_partial_failure_closes_all_handles_never_waits_unverified(
    subject, monkeypatch, failure
):
    options = {'foreign_pid': 102} if failure == 'foreign' else (
        {'failed_membership': 102} if failure == 'membership_api' else {'failed_open': 102})
    job, clock, queried, opened, membership, waited, terminated, closed = job_process_double(
        subject, monkeypatch, **options)
    with pytest.raises(subject.OwnedProcessError):
        job.terminate_and_wait(clock.now + 3)
    assert opened == [101, 102] and terminated == [(77, 1)]
    assert all(pid == 101 for pid, _ in waited)  # 102 is never waited or individually killed
    expected = [10101] if failure == 'open_still_listed' else [10101, 10102]
    assert closed == expected
    job.close()
    assert closed == expected + [77]


def test_job_process_list_invalid_exited_pid_skipped_only_after_exact_job_requery(
    subject, monkeypatch
):
    job, clock, queried, opened, membership, waited, terminated, closed = job_process_double(
        subject, monkeypatch, snapshots=((101, 102), (101,)), failed_open=102)
    job.terminate_and_wait(clock.now + 3)
    assert len(queried) == 2 and opened == [101, 102] and membership == [101]
    assert [pid for pid, _ in waited] == [101] and closed == [10101]
    assert terminated == [(77, 1)]
    job.close()


def test_job_process_kernel_wait_expiry_is_failure_even_with_active_zero(subject, monkeypatch):
    job, clock, queried, opened, membership, waited, terminated, closed = job_process_double(
        subject, monkeypatch, wait_result=258)
    deadline = clock.now + 0.025
    with pytest.raises(subject.OwnedProcessError):
        job.terminate_and_wait(deadline)
    assert waited and all(0 <= milliseconds <= 25 for _, milliseconds in waited)
    assert clock.now <= deadline
    assert terminated == [(77, 1)] and closed == [10101, 10102]
    job.close()


def test_job_process_capture_expiry_still_terminates_and_opens_no_handles(subject, monkeypatch):
    job, clock, queried, opened, membership, waited, terminated, closed = job_process_double(
        subject, monkeypatch)
    with pytest.raises(subject.OwnedProcessError):
        job.terminate_and_wait(clock.now)
    assert not queried and not opened and not membership and not waited and not closed
    assert terminated == [(77, 1)]
    job.close()


def test_job_process_id_list_uses_eight_byte_header_and_pointer_sized_entries(subject):
    layout = subject._process_id_list_type(17)
    assert layout.ProcessIdList.offset == 8
    assert ctypes.sizeof(subject.ULONG_PTR) == ctypes.sizeof(ctypes.c_void_p)
    assert ctypes.sizeof(layout) == 8 + 17 * ctypes.sizeof(subject.ULONG_PTR)


@pytest.mark.parametrize("threads,thread_owner,resume_count", [
    ((123, 123), 123, 1), ((987,), 123, 1), ((123,), 987, 1), ((123,), 123, 0xffffffff),
])
def test_toolhelp_never_resumes_ambiguous_or_foreign_thread(subject, monkeypatch,
                                                         threads, thread_owner, resume_count):
    closed, opened, resumed, flags = kernel_double(
        monkeypatch, subject, threads=threads, thread_owner=thread_owner, resume_count=resume_count)
    job = subject._WindowsJob()
    with pytest.raises(subject.OwnedProcessError):
        job.resume(Process())
    job.close()
    assert 88 in closed and 77 in closed and flags == [0x2000]
    if len(threads) != 1 or threads[0] != 123:
        assert not opened and not resumed
    elif thread_owner != 123:
        assert not resumed and 99 in closed
    else:
        assert resumed == [99] and 99 in closed


def test_failed_job_configuration_closes_created_handle(subject, monkeypatch):
    closed, _, _, _ = kernel_double(monkeypatch, subject, set_limits=False)
    with pytest.raises(subject.OwnedProcessError):
        subject._WindowsJob()
    assert closed == [77]


@pytest.mark.parametrize("seam", ["snapshot", "enumeration", "open_thread"])
def test_toolhelp_partial_startup_closes_every_obtained_handle(subject, monkeypatch, seam):
    closed, opened, resumed, _ = kernel_double(monkeypatch, subject)
    job = subject._WindowsJob()
    if seam == "snapshot":
        job.api.CreateToolhelp32Snapshot.body = lambda *a: subject.INVALID_HANDLE_VALUE
    elif seam == "enumeration":
        monkeypatch.setattr(subject.ctypes, "get_last_error", lambda: 5)
    else:
        job.api.OpenThread.body = lambda *a: 0
    with pytest.raises(subject.OwnedProcessError):
        job.resume(Process())
    job.close()
    assert not resumed and 77 in closed
    assert (88 in closed) == (seam != "snapshot")
    assert 99 not in closed


def test_active_accounting_wait_has_deadline_even_if_root_exited(subject, monkeypatch):
    kernel_double(monkeypatch, subject)
    job = subject._WindowsJob()
    monkeypatch.setattr(job, 'active_count', lambda: 1)
    with pytest.raises(subject.OwnedProcessError):
        job.terminate_and_wait(time.monotonic() - 1)
    job.close()


def test_windows_structures_have_fixed_dword_and_pointer_sized_fields(subject):
    assert ctypes.sizeof(subject.DWORD) == 4
    assert ctypes.sizeof(subject._ThreadEntry) == 28
    assert ctypes.sizeof(subject._Accounting) == 48
    if ctypes.sizeof(ctypes.c_void_p) == 8:
        assert ctypes.sizeof(subject._BasicLimits) == 64
        assert ctypes.sizeof(subject._ExtendedLimits) == 144


@pytest.mark.skipif(os.name != "nt", reason="actual Windows Job Object kernel gate")
@pytest.mark.parametrize("root_exits", [False, True])
def test_real_windows_venv_descendant_cwd_and_stdout_lifetime(subject, tmp_path, root_exits):
    assert sys.prefix != sys.base_prefix, "Main must run the actual Windows venv interpreter gate"
    directory = tmp_path / "private cwd"
    directory.mkdir()
    marker = tmp_path / "owned pids.txt"
    grandchild = (
        "import os,time; from pathlib import Path; "
        f"p=Path({str(marker)!r}); q=p.with_suffix('.tmp'); "
        "q.write_text(str(os.getpid())); q.replace(p); time.sleep(30)"
    )
    script = tmp_path / "fixed root.py"
    script.write_text(
        "import subprocess,sys,time\n"
        f"child=subprocess.Popen([sys.executable,'-I','-u','-c',{grandchild!r}])\n"
        + ("sys.exit(0)\n" if root_exits else "time.sleep(30)\n"), encoding="utf-8")
    owner = subject.OwnedProcess()
    process = owner.start(subprocess.Popen, [sys.executable, "-I", "-u", str(script)],
                          cwd=directory, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, close_fds=True, shell=False)
    threads = []
    observer = None
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [subject.DWORD, ctypes.c_int, subject.DWORD]
    kernel.OpenProcess.restype = subject.HANDLE
    kernel.WaitForSingleObject.argtypes = [subject.HANDLE, subject.DWORD]
    kernel.WaitForSingleObject.restype = subject.DWORD
    kernel.CloseHandle.argtypes = [subject.HANDLE]
    kernel.CloseHandle.restype = ctypes.c_int
    try:
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert marker.exists()
        descendant_pid = int(marker.read_text())
        observer = kernel.OpenProcess(0x00100000, False, descendant_pid)  # SYNCHRONIZE only
        assert observer and kernel.WaitForSingleObject(observer, 0) == 258
        if root_exits:
            process.wait(timeout=5)
        assert owner.job.active_count() >= 1
        reader = threading.Thread(target=process.stdout.read, name="owned-regression-reader", daemon=True)
        threads.append(reader)
        reader.start()
        owner.stop(threads)
        assert owner.tree_empty and owner.closed and owner.job is None
        assert kernel.WaitForSingleObject(observer, 0) == 0
        assert process.poll() is not None and not reader.is_alive()
        assert process.stdin.closed and process.stdout.closed
        assert process._handle.closed
        directory.rmdir()
    finally:
        if not owner.closed:
            owner.stop(threads)
        if observer:
            assert kernel.CloseHandle(observer)


@pytest.mark.skipif(os.name == "nt", reason="POSIX unchanged root process behavior")
def test_real_posix_smoke_has_no_job_or_windows_flags(subject, tmp_path):
    owner = subject.OwnedProcess()
    process = owner.start(subprocess.Popen, [sys.executable, "-I", "-u", "-c", "print('safe')"],
                          cwd=tmp_path, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, close_fds=True, shell=False, creationflags=0)
    assert process.stdout.read() == b"safe\n"
    process.wait(timeout=3)
    owner.stop([])
    assert owner.job is None and owner.closed and process.stdout.closed

BACKEND_CLIENT = Path(__file__).resolve().parents[1] / "app" / "services" / "knowledge_transport.py"


@pytest.fixture
def transport():
    # This fixed source-load imports only transport and its trusted stdlib helper.
    # No mirofish_knowledge/Graphiti SDK/app startup is needed for collection.
    spec = importlib.util.spec_from_file_location("standalone_owned_knowledge_transport", BACKEND_CLIENT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def _knowledge_request(method="page", *, request_id=None, payload=None):
    return json.dumps({
        "version": 1, "request_id": request_id or str(uuid4()), "method": method,
        "scope": {"schema_version": 1, "workspace_id": str(uuid4()), "project_id": str(uuid4()),
                  "graph_id": str(uuid4()), "run_id": None, "branch_id": None, "layer": "source"},
        "payload": payload if payload is not None else {"kind": "node", "limit": 1},
    }, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _knowledge_child_script(tmp_path, body, name="fixture.py"):
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path.resolve()


def _knowledge_echo_script(tmp_path):
    return _knowledge_child_script(tmp_path, '''
import json, sys
incoming = sys.stdin.buffer.read()
request = json.loads(incoming[4:])
body = json.dumps({"version": 1, "request_id": request["request_id"],
                   "ok": True, "result": {}}).encode()
sys.stdout.buffer.write(len(body).to_bytes(4, "big") + body)
sys.stdout.buffer.flush()
''')


@pytest.mark.skipif(os.name != "nt", reason="real Windows venv/Job Object knowledge lifecycle")
@pytest.mark.parametrize("mode", ["success", "malformed", "overflow", "timeout", "root_exit"])
def test_windows_knowledge_descendant_cleanup_and_lock_reuse(tmp_path, monkeypatch, transport, mode):
    assert sys.prefix != sys.base_prefix, "actual Windows venv redirector gate required"
    owners = []
    real_owner = transport.OwnedProcess

    class CaptureOwner(real_owner):
        def __init__(self):
            super().__init__()
            owners.append(self)

        def start(self, popen, args, **kwargs):
            self.directory = Path(kwargs['cwd'])
            return super().start(popen, args, **kwargs)

    monkeypatch.setattr(transport, "OwnedProcess", CaptureOwner)
    marker = tmp_path / "grandchild pid.txt"
    grandchild = (
        "import os,time; from pathlib import Path; "
        f"p=Path({str(marker)!r}); q=p.with_suffix('.tmp'); "
        "q.write_text(str(os.getpid())); q.replace(p); time.sleep(30)"
    )
    body = (
        "import json,os,subprocess,sys,time\n"
        "from pathlib import Path\n"
        "incoming=sys.stdin.buffer.read()\n"
        "identifier=json.loads(incoming[4:])['request_id']\n"
        f"child=subprocess.Popen([sys.executable,'-I','-u','-c',{grandchild!r}], "
        + ("stdout=subprocess.DEVNULL" if mode in {"success", "malformed"} else "stdout=sys.stdout")
        + ")\n"
        "end=time.monotonic()+5\n"
        f"while not Path({str(marker)!r}).exists() and time.monotonic()<end: time.sleep(0.01)\n"
        f"assert Path({str(marker)!r}).exists()\n"
    )
    if mode == "success":
        body += (
            "reply=json.dumps({'version':1,'request_id':identifier,'ok':True,'result':{'cwd':os.getcwd()}}).encode()\n"
            "sys.stdout.buffer.write(len(reply).to_bytes(4,'big')+reply); sys.stdout.buffer.flush()\n"
        )
    elif mode == "malformed":
        body += "sys.stdout.buffer.write((2).to_bytes(4,'big')+b'{}'); sys.stdout.buffer.flush()\n"
    elif mode == "overflow":
        body += "sys.stdout.buffer.write((2097153).to_bytes(4,'big')); sys.stdout.buffer.flush(); time.sleep(30)\n"
    elif mode == "timeout":
        body += "time.sleep(30)\n"
    script = _knowledge_child_script(tmp_path, body)
    client = transport.KnowledgeProcessClient(sys.executable, script, timeout_seconds=7)
    for ordinal in range(2):
        if marker.exists():
            marker.unlink()
        if mode == "success":
            reply = json.loads(client.call(_knowledge_request()))
            assert reply['ok'] and not Path(reply['result']['cwd']).exists()
        else:
            with pytest.raises(transport.KnowledgeTransportFailure) as caught:
                client.call(_knowledge_request("ingest"))
            assert caught.value.outcome_unknown and str(caught.value) == "transport failure"
        assert marker.exists(), "actual grandchild must start before failure"
        owner = owners[-1]
        assert owner.tree_empty and owner.closed and owner.job is None
        assert owner.process.stdin.closed and owner.process.stdout.closed and owner.process._handle.closed
        assert not owner.directory.exists()
        assert not client._lock.locked()
        assert not any(thread.name.startswith('mirofish-knowledge-') for thread in threading.enumerate())


@pytest.mark.skipif(os.name != "nt", reason="Windows handled suspended startup failure")
@pytest.mark.parametrize("seam", ["assign", "resume"])
def test_windows_knowledge_startup_failure_is_owned_and_reusable(tmp_path, monkeypatch, transport, seam):
    marker = tmp_path / "must not execute.txt"
    script = _knowledge_child_script(tmp_path, f"open({str(marker)!r}, 'w').write('executed')")
    client = transport.KnowledgeProcessClient(sys.executable, script, timeout_seconds=3)
    launched = []
    real_popen = transport.subprocess.Popen
    job_type = transport._owned_process._WindowsJob
    original = getattr(job_type, seam)

    def capture(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        launched.append((process, Path(kwargs['cwd'])))
        return process

    def fail(self, process):
        raise transport._owned_process.OwnedProcessError()

    monkeypatch.setattr(transport.subprocess, 'Popen', capture)
    monkeypatch.setattr(job_type, seam, fail)
    with pytest.raises(transport.KnowledgeTransportFailure) as caught:
        client.call(_knowledge_request('ingest'))
    assert caught.value.outcome_unknown == (seam == 'resume')
    assert not marker.exists() and len(launched) == 1 and not client._lock.locked()
    assert launched[0][0].poll() is not None and launched[0][0]._handle.closed
    assert not launched[0][1].exists()
    monkeypatch.setattr(job_type, seam, original)
    # The existing client remains usable without retrying the failed operation.
    _knowledge_echo_script(tmp_path)
    assert json.loads(client.call(_knowledge_request()))['ok']
