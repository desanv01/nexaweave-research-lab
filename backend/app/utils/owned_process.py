"""Private subprocess lifetime ownership; stdlib only, no application imports.

Windows children start suspended and enter a kill-on-close Job before any child
code runs. This is lifetime control, not a sandbox or crash recovery guarantee.
"""

import ctypes
import os
from os import path as _path
import subprocess
import time


DWORD = ctypes.c_uint32
HANDLE = ctypes.c_void_p
SIZE_T = ctypes.c_size_t
ULONG_PTR = ctypes.c_size_t
ULONG64 = ctypes.c_uint64
LARGE_INTEGER = ctypes.c_int64
CREATE_SUSPENDED = 0x00000004
CREATE_NO_WINDOW = 0x08000000
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_MAX_JOB_PROCESSES = 65536


def _process_id_list_type(capacity):
    class ProcessIdList(ctypes.Structure):
        _fields_ = [("NumberOfAssignedProcesses", DWORD),
                    ("NumberOfProcessIdsInList", DWORD),
                    ("ProcessIdList", ULONG_PTR * capacity)]
    return ProcessIdList


class _BasicLimits(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", LARGE_INTEGER),
                ("PerJobUserTimeLimit", LARGE_INTEGER), ("LimitFlags", DWORD),
                ("MinimumWorkingSetSize", SIZE_T), ("MaximumWorkingSetSize", SIZE_T),
                ("ActiveProcessLimit", DWORD), ("Affinity", SIZE_T),
                ("PriorityClass", DWORD), ("SchedulingClass", DWORD)]


class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ULONG64) for name in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", _BasicLimits), ("IoInfo", _IoCounters),
                ("ProcessMemoryLimit", SIZE_T), ("JobMemoryLimit", SIZE_T),
                ("PeakProcessMemoryUsed", SIZE_T), ("PeakJobMemoryUsed", SIZE_T)]


class _Accounting(ctypes.Structure):
    _fields_ = [("TotalUserTime", LARGE_INTEGER), ("TotalKernelTime", LARGE_INTEGER),
                ("ThisPeriodTotalUserTime", LARGE_INTEGER),
                ("ThisPeriodTotalKernelTime", LARGE_INTEGER),
                ("TotalPageFaultCount", DWORD), ("TotalProcesses", DWORD),
                ("ActiveProcesses", DWORD), ("TotalTerminatedProcesses", DWORD)]


class _ThreadEntry(ctypes.Structure):
    _fields_ = [("dwSize", DWORD), ("cntUsage", DWORD), ("th32ThreadID", DWORD),
                ("th32OwnerProcessID", DWORD), ("tpBasePri", ctypes.c_int32),
                ("tpDeltaPri", ctypes.c_int32), ("dwFlags", DWORD)]


class OwnedProcessError(RuntimeError):
    def __init__(self, *, outcome_unknown=False, cleanup_stage=None):
        self.outcome_unknown = outcome_unknown
        self._cleanup_stage = cleanup_stage
        super().__init__("owned process failure")


class _WindowsJob:
    def __init__(self, *, job_commit_limit_bytes=None):
        self.handle = None
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "CreateJobObjectW": ([HANDLE, ctypes.c_wchar_p], HANDLE),
            "SetInformationJobObject": ([HANDLE, ctypes.c_int, HANDLE, DWORD], ctypes.c_int),
            "AssignProcessToJobObject": ([HANDLE, HANDLE], ctypes.c_int),
            "TerminateJobObject": ([HANDLE, ctypes.c_uint], ctypes.c_int),
            "QueryInformationJobObject": ([HANDLE, ctypes.c_int, HANDLE, DWORD, ctypes.POINTER(DWORD)], ctypes.c_int),
            "OpenProcess": ([DWORD, ctypes.c_int, DWORD], HANDLE),
            "IsProcessInJob": ([HANDLE, HANDLE, ctypes.POINTER(ctypes.c_int)], ctypes.c_int),
            "WaitForSingleObject": ([HANDLE, DWORD], DWORD),
            "CloseHandle": ([HANDLE], ctypes.c_int),
            "CreateToolhelp32Snapshot": ([DWORD, DWORD], HANDLE),
            "Thread32First": ([HANDLE, ctypes.POINTER(_ThreadEntry)], ctypes.c_int),
            "Thread32Next": ([HANDLE, ctypes.POINTER(_ThreadEntry)], ctypes.c_int),
            "OpenThread": ([DWORD, ctypes.c_int, DWORD], HANDLE),
            "GetProcessIdOfThread": ([HANDLE], DWORD),
            "ResumeThread": ([HANDLE], DWORD),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.api, name)
            function.argtypes = arguments
            function.restype = result
        # NULL security attributes produce a noninheritable, unnamed handle.
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise OwnedProcessError()
        try:
            limits = _ExtendedLimits()
            flags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if job_commit_limit_bytes is not None:
                flags |= _JOB_OBJECT_LIMIT_JOB_MEMORY
                limits.JobMemoryLimit = job_commit_limit_bytes
            limits.BasicLimitInformation.LimitFlags = flags
            if not self.api.SetInformationJobObject(
                    self.handle, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                    ctypes.byref(limits), ctypes.sizeof(limits)):
                raise OwnedProcessError()
        except BaseException:
            self.close()
            raise

    def assign(self, process):
        if not self.api.AssignProcessToJobObject(self.handle, int(process._handle)):
            raise OwnedProcessError()

    def verify_commit_limit(self, expected_bytes):
        limits = _ExtendedLimits()
        returned = DWORD()
        flags = _JOB_OBJECT_LIMIT_JOB_MEMORY | _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if (not self.api.QueryInformationJobObject(
                self.handle, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION, ctypes.byref(limits),
                ctypes.sizeof(limits), ctypes.byref(returned))
                or returned.value != ctypes.sizeof(limits)
                or (limits.BasicLimitInformation.LimitFlags & flags) != flags
                or limits.JobMemoryLimit != expected_bytes):
            raise OwnedProcessError()
        return limits

    def terminal_commit_accounting(self, expected_bytes):
        limits = self.verify_commit_limit(expected_bytes)
        process_peak, job_peak = limits.PeakProcessMemoryUsed, limits.PeakJobMemoryUsed
        if not 0 < process_peak <= job_peak <= expected_bytes:
            raise OwnedProcessError()
        return limits.JobMemoryLimit, process_peak, job_peak

    def resume(self, process):
        # Popen closes CreateProcess's primary thread handle. Inspect a bounded
        # thread snapshot, but open/resume only the exact still-suspended PID.
        snapshot = self.api.CreateToolhelp32Snapshot(0x00000004, 0)  # SNAPTHREAD
        if snapshot in (None, 0, INVALID_HANDLE_VALUE):
            raise OwnedProcessError()
        thread = None
        try:
            entry = _ThreadEntry()
            entry.dwSize = ctypes.sizeof(entry)
            present = self.api.Thread32First(snapshot, ctypes.byref(entry))
            candidates = []
            deadline = time.monotonic() + 3.0
            count = 0
            while present:
                count += 1
                if count > 65536 or time.monotonic() >= deadline:
                    raise OwnedProcessError()
                if entry.th32OwnerProcessID == process.pid:
                    candidates.append(entry.th32ThreadID)
                entry.dwSize = ctypes.sizeof(entry)
                present = self.api.Thread32Next(snapshot, ctypes.byref(entry))
            if ctypes.get_last_error() != 18 or len(candidates) != 1:  # NO_MORE_FILES
                raise OwnedProcessError()
            thread = self.api.OpenThread(0x0002 | 0x0800, False, candidates[0])
            if not thread or self.api.GetProcessIdOfThread(thread) != process.pid:
                raise OwnedProcessError()
            # Any other suspension count is ambiguous; handled failure kills
            # the assigned job. No unowned fallback or breakaway is permitted.
            if self.api.ResumeThread(thread) != 1:
                raise OwnedProcessError(outcome_unknown=True)
        finally:
            try:
                if thread:
                    self._close_handle(thread)
            finally:
                self._close_handle(snapshot)

    def active_count(self):
        accounting = _Accounting()
        if not self.api.QueryInformationJobObject(
                self.handle, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None):
            raise OwnedProcessError()
        return accounting.ActiveProcesses

    def _process_ids(self, deadline):
        capacity = 16
        while True:
            if time.monotonic() >= deadline:
                raise OwnedProcessError()
            buffer = _process_id_list_type(capacity)()
            returned = DWORD()
            success = self.api.QueryInformationJobObject(
                self.handle, 3, ctypes.byref(buffer), ctypes.sizeof(buffer), ctypes.byref(returned))
            # JobObjectBasicProcessIdList is information class 3. Its ProcessIdList
            # field begins at byte 8 (two DWORDs); the entries are ULONG_PTR.
            error = ctypes.get_last_error() if not success else 0
            assigned = buffer.NumberOfAssignedProcesses
            listed = buffer.NumberOfProcessIdsInList
            if assigned > _MAX_JOB_PROCESSES or listed > capacity:
                raise OwnedProcessError()
            if success and assigned == listed:
                pids = tuple(buffer.ProcessIdList[:listed])
                if len(set(pids)) != listed or any(not 0 < pid <= 0xffffffff for pid in pids):
                    raise OwnedProcessError()
                return pids
            if (not success and error != 234) or assigned <= capacity:
                raise OwnedProcessError()
            # Fixed growth/entry bound: no unbounded allocation on a changing job.
            capacity = max(capacity * 2, assigned)
            if capacity > _MAX_JOB_PROCESSES:
                raise OwnedProcessError()

    def _pin_processes(self, deadline, handles):
        for pid in self._process_ids(deadline):
            if time.monotonic() >= deadline:
                raise OwnedProcessError()
            # Never request termination rights. A pinned handle prevents PID
            # reuse after opening; membership must still confirm this exact job.
            handle = self.api.OpenProcess(0x00100000 | 0x1000, False, pid)
            if not handle:
                error = ctypes.get_last_error()
                # An unopenable exited PID may vanish from the job's current
                # list. Only INVALID_PARAMETER plus confirmed absence permits
                # skipping it; access errors/continued presence fail closed.
                if error == 87 and pid not in self._process_ids(deadline):
                    continue
                raise OwnedProcessError()
            entry = [handle, False]
            handles.append(entry)  # close even if membership validation fails
            member = ctypes.c_int()
            if not self.api.IsProcessInJob(handle, self.handle, ctypes.byref(member)) or not member.value:
                raise OwnedProcessError()
            entry[1] = True

    def _wait_process(self, handle, deadline):
        while True:
            remaining = max(0, deadline - time.monotonic())
            milliseconds = min(0xfffffffe, int(remaining * 1000))
            result = self.api.WaitForSingleObject(handle, milliseconds)
            if result == 0:
                return
            if result != 258 or time.monotonic() >= deadline:
                raise OwnedProcessError()
            # DWORD milliseconds round down; consume any submillisecond
            # remainder without adding a separate timeout or busy-spinning.
            if milliseconds == 0:
                time.sleep(min(0.001, max(0, deadline - time.monotonic())))

    def terminate_and_wait(self, deadline):
        # Pin a complete bounded job snapshot before termination. Active-zero
        # alone can precede the process object's kernel signal/pending I/O exit.
        handles = []
        failure = None
        self._cleanup_stage = None
        stage = "job_snapshot_pin"
        try:
            try:
                self._pin_processes(deadline, handles)
            except BaseException as error:
                failure = error
                self._cleanup_stage = stage
            # Always terminate, including failed capture or an exited root.
            stage = "job_terminate"
            if not self.api.TerminateJobObject(self.handle, 1):
                raise OwnedProcessError()
            stage = "job_process_wait"
            for handle, verified in handles:
                if not verified:
                    continue  # never wait a foreign/reused/unproved handle
                self._wait_process(handle, deadline)
            stage = "job_active_zero"
            while self.active_count():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise OwnedProcessError()
                time.sleep(min(0.01, remaining))
        except BaseException as error:
            self._cleanup_stage = self._cleanup_stage or stage
            if failure is None or isinstance(error, (KeyboardInterrupt, SystemExit)):
                failure = error
        finally:
            # Every obtained process handle closes before owner.stop closes the
            # job, including handles whose membership could not be proved.
            for handle, _verified in handles:
                try:
                    self._close_handle(handle)
                except BaseException as error:
                    self._cleanup_stage = self._cleanup_stage or "job_process_handle_close"
                    if failure is None or isinstance(error, (KeyboardInterrupt, SystemExit)):
                        failure = error
        if failure is not None:
            raise failure

    def _close_handle(self, handle):
        if not self.api.CloseHandle(handle):
            raise OwnedProcessError()

    def close(self):
        if self.handle:
            handle, self.handle = self.handle, None
            self._close_handle(handle)


class OwnedProcess:
    """Internal owner for the caller's fixed subprocess and its I/O threads.

    Callers supply their existing Popen hook, preserving capture tests. Production
    chooses ownership from os.name; only tests replace _WindowsJob explicitly.
    """

    def __init__(self):
        self.process = None
        self.job = None
        self.started = False
        self.tree_empty = False
        self.closed = False
        self.cleanup_deadline = None
        self.cleanup_timeout = None
        self._verified_job_commit_limit_bytes = None
        self.terminal_job_memory = None  # (limit, process peak, Job peak), in bytes
        self._private_directory = None
        self._directory_name = None
        self._cwd = None

    def bind_private_directory(self, directory):
        # Retain the actual TemporaryDirectory object, never a deletion path
        # supplied by a request. Callers bind immediately after creating it.
        self._private_directory = directory
        self._directory_name = _path.normcase(_path.abspath(directory.name))

    def cleanup_private_directory(self, directory):
        name = _path.normcase(_path.abspath(directory.name))
        if (directory is not self._private_directory or name != self._directory_name
                or (self._cwd is not None and self._cwd != name)
                or (self.process is not None and self._cwd is None)):
            raise OwnedProcessError(outcome_unknown=self.started)
        while True:
            try:
                # The initial cleanup is attempted even when ownership cleanup
                # failed. Only a proved owner may retry a transient Win32 lock.
                directory.cleanup()
                if os.name == "nt" and _path.lexists(directory.name):
                    raise OwnedProcessError(outcome_unknown=self.started)
                return
            except OSError as error:
                if (os.name != "nt" or getattr(error, "winerror", None) not in (5, 32)
                        or not self.closed
                        or not (self.tree_empty or (self.process is None and not self.started))
                        or self.cleanup_deadline is None):
                    raise
                remaining = min(self.cleanup_timeout,
                                max(0, self.cleanup_deadline - time.monotonic()))
                if remaining <= 0:
                    raise OwnedProcessError(outcome_unknown=self.started) from None
                time.sleep(min(0.01, remaining))
                if time.monotonic() >= self.cleanup_deadline:
                    raise OwnedProcessError(outcome_unknown=self.started) from None

    def start(self, popen, args, *, job_commit_limit_bytes=None, **kwargs):
        self._cwd = _path.normcase(_path.abspath(kwargs["cwd"])) if "cwd" in kwargs else None
        try:
            if job_commit_limit_bytes is not None and (
                    os.name != "nt" or type(job_commit_limit_bytes) is not int
                    or not 0 < job_commit_limit_bytes <= SIZE_T(-1).value):
                raise OwnedProcessError()
            if os.name == "nt":
                self.job = (_WindowsJob() if job_commit_limit_bytes is None else
                            _WindowsJob(job_commit_limit_bytes=job_commit_limit_bytes))
                kwargs["creationflags"] = kwargs.get("creationflags", 0) | CREATE_SUSPENDED | CREATE_NO_WINDOW
            self.process = popen(args, **kwargs)
            if self.job is not None:
                self.job.assign(self.process)
                if job_commit_limit_bytes is not None:
                    self.job.verify_commit_limit(job_commit_limit_bytes)
                    self._verified_job_commit_limit_bytes = job_commit_limit_bytes
                # From this point a resume attempt can execute child code even
                # if its result or thread-handle cleanup subsequently fails.
                self.started = True
                self.job.resume(self.process)
            self.started = True
            return self.process
        except BaseException as error:
            self.started = self.started or getattr(error, "outcome_unknown", False)
            try:
                self.stop([])
            except Exception:
                if not isinstance(error, (KeyboardInterrupt, SystemExit)):
                    raise OwnedProcessError(outcome_unknown=self.started) from None
            raise

    def stop(self, threads, *, timeout=3.0):
        # Partial-startup stop and caller-finally stop share one original budget.
        if self.cleanup_deadline is None:
            self.cleanup_deadline = time.monotonic() + timeout
            self.cleanup_timeout = timeout
        deadline = self.cleanup_deadline

        def remaining_timeout():
            # Floating-point addition/subtraction can otherwise exceed the
            # caller's supplied budget by a few ulps immediately after setup.
            return min(timeout, self.cleanup_timeout, max(0, deadline - time.monotonic()))

        failure_stage = None
        accounting_interrupt = None
        process = self.process
        try:
            if self.job is not None:
                try:
                    self.job.terminate_and_wait(deadline)
                except Exception:
                    failure_stage = getattr(self.job, "_cleanup_stage", None) or "job_teardown"
                else:
                    self.tree_empty = True
                    if self._verified_job_commit_limit_bytes is not None and remaining_timeout() > 0:
                        try:
                            accounting = self.job.terminal_commit_accounting(
                                self._verified_job_commit_limit_bytes,
                            )
                            if remaining_timeout() > 0:
                                self.terminal_job_memory = accounting
                        except Exception:
                            # Successful teardown is independent of accounting availability.
                            pass
                        except BaseException as error:
                            accounting_interrupt = error
                finally:
                    try:
                        self.job.close()
                    except Exception:
                        failure_stage = failure_stage or "job_close"
                    self.job = None
            if process is not None:
                try:
                    if process.poll() is None:
                        if os.name != "nt":
                            process.terminate()
                            try:
                                process.wait(timeout=min(0.5, remaining_timeout()))
                            except subprocess.TimeoutExpired:
                                process.kill()
                        else:
                            process.kill()
                    process.wait(timeout=remaining_timeout())
                except (OSError, subprocess.SubprocessError):
                    failure_stage = failure_stage or "root_reap"
            # Termination releases pipe reads/writes. Join before closing buffered
            # streams: close() can otherwise wait forever on a reader's lock.
            for thread in threads:
                if thread.ident is not None:
                    thread.join(timeout=remaining_timeout())
                    if thread.is_alive():
                        failure_stage = failure_stage or "io_thread_join"
            io_stopped = all(thread.ident is None or not thread.is_alive() for thread in threads)
            if process is not None and io_stopped:
                for pipe in (process.stdin, process.stdout, process.stderr):
                    if pipe is not None:
                        try:
                            pipe.close()
                        except (OSError, ValueError):
                            failure_stage = failure_stage or "pipe_close"
                if os.name == "nt" and process.returncode is not None:
                    try:
                        process._handle.Close()
                    except OSError:
                        failure_stage = failure_stage or "root_handle_close"
            self.closed = failure_stage is None
        finally:
            if accounting_interrupt is not None:
                raise accounting_interrupt
            if failure_stage is not None:
                raise OwnedProcessError(outcome_unknown=self.started, cleanup_stage=failure_stage) from None
