"""One authority-verification-owned read child; no cached access or graph data."""
from __future__ import annotations

import os
import asyncio
import math
import queue
import subprocess
import tempfile
import threading
import time

from .knowledge_transport import (KnowledgeProcessClient, KnowledgeBusy,
    KnowledgeInvalidRequest, KnowledgeTransportFailure, OwnedProcess,
    _json_object, _private_environment, _read_exact, _RESPONSE_MAX)


def _reader(pipe, events, accept_frame, accept_eof):
    try:
        while True:
            first = pipe.read(1)
            if first == b'':
                accept_eof()
                events.put(('eof', None), timeout=1)
                return
            header = first + _read_exact(pipe, 3)
            length = int.from_bytes(header, 'big')
            if not 0 < length <= _RESPONSE_MAX:
                raise ValueError
            body = _read_exact(pipe, length)
            ok = accept_frame(body)
            events.put(('reply', body), timeout=1)
            if not ok:
                # The failed page is terminal; parent closes the owned child.
                # Its ensuing EOF cannot replace the exact dispatcher code.
                return
    except queue.Full:
        # Existing queued extra frames already make the next exchange/EOF fail.
        return
    except Exception:
        try:
            events.put(('error', None), timeout=1)
        except queue.Full:
            pass


def _writer(pipe, commands, events, shutdown):
    try:
        while not shutdown.is_set():
            try:
                item = commands.get(timeout=.1)
            except queue.Empty:
                continue
            if item is None or shutdown.is_set():
                return
            number, frame, terminal = item
            view = memoryview(frame)
            while view:
                count = pipe.write(view)
                if not count:
                    raise OSError
                view = view[count:]
            pipe.flush()
            if terminal:
                pipe.close()
            events.put(('written', number), timeout=1)
            if terminal:
                return
    except (OSError, ValueError, TypeError, queue.Full):
        try:
            events.put(('error', None), timeout=1)
        except queue.Full:
            pass


def _task():
    try:
        return asyncio.current_task()
    except RuntimeError:
        return None


class KnowledgeReadSession(KnowledgeProcessClient):
    """Sequential fresh page RPCs, owned only within one parent verification."""
    def __init__(self, *args, deadline=None, **kwargs):
        super().__init__(*args, **kwargs)
        if (self._timeout > 120 or deadline is not None and
                (type(deadline) not in (int, float) or not math.isfinite(deadline))):
            raise ValueError('finite existing session bounds required')
        self._deadline = min(time.monotonic() + 120, deadline) if deadline is not None else time.monotonic() + 120
        self._owner = OwnedProcess()
        self._directory = None
        self._process = None
        self._threads = []
        self._commands = queue.Queue(maxsize=1)
        self._shutdown = threading.Event()
        self._events = queue.Queue(maxsize=3)
        self._ids = set()
        self._scope = None
        self._closed = False
        self._entered = False
        self._thread_id = None
        self._task_id = None
        self._protocol_lock = threading.Lock()
        self._expected_id = None
        self._closing = False
        self._protocol_failed = False

    def _accept_frame(self, body):
        with self._protocol_lock:
            try:
                if self._closing or self._expected_id is None:
                    raise ValueError
                self._validate_reply(body, self._expected_id)
                self._expected_id = None
                return _json_object(body)['ok']
            except Exception:
                self._protocol_failed = True
                raise

    def _accept_eof(self):
        with self._protocol_lock:
            if not self._closing or self._expected_id is not None:
                self._protocol_failed = True
                raise ValueError

    def __enter__(self):
        if self._entered or self._closed:
            raise KnowledgeBusy()
        self._entered = True
        self._thread_id = threading.get_ident()
        self._task_id = _task()
        return self

    def _tick(self):
        if self._cooperative_tick is not None:
            self._cooperative_tick()
        if time.monotonic() >= self._deadline:
            raise KnowledgeTransportFailure(outcome_unknown=self._owner.started)
        if self._protocol_failed:
            raise KnowledgeTransportFailure(outcome_unknown=self._owner.started)

    def _spawn(self):
        self._tick()
        self._directory = tempfile.TemporaryDirectory(prefix='nexaweave-read-session-')
        self._owner.bind_private_directory(self._directory)
        self._process = self._owner.start(subprocess.Popen,
            [self._python, '-I', '-u', self._script], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, shell=False,
            close_fds=True, cwd=self._directory.name,
            env=_private_environment(self._directory.name, self._python, self._extra_environment),
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self._threads = [threading.Thread(target=_writer, args=(self._process.stdin, self._commands, self._events, self._shutdown), daemon=True),
                         threading.Thread(target=_reader, args=(self._process.stdout, self._events, self._accept_frame, self._accept_eof), daemon=True)]
        for thread in self._threads:
            thread.start()

    def _exchange(self, number, frame, terminal=False):
        self._tick()
        deadline = min(self._deadline, time.monotonic() + self._timeout)
        self._commands.put_nowait((number, frame, terminal))
        written = received = False
        body = None
        while not (written and received):
            self._tick()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise KnowledgeTransportFailure(outcome_unknown=True)
            try:
                event, value = self._events.get(timeout=min(1, remaining))
            except queue.Empty:
                continue
            if event == 'written' and value == number and not written:
                written = True
            elif event == ('eof' if terminal else 'reply') and not received:
                received, body = True, value
            else:
                raise KnowledgeTransportFailure(outcome_unknown=True)
        self._tick()
        return body

    def call(self, raw):
        if threading.get_ident() != self._thread_id or _task() is not self._task_id:
            raise KnowledgeBusy()
        if not self._lock.acquire(blocking=False):
            raise KnowledgeBusy()
        try:
            if not self._entered or self._closed:
                raise KnowledgeInvalidRequest()
            request_id = self._validate_request(raw)
            request = _json_object(raw)
            if (request['method'] != 'page' or request_id in self._ids or len(self._ids) >= 800
                    or self._scope is not None and request['scope'] != self._scope):
                raise KnowledgeInvalidRequest()
            self._scope = request['scope']
            self._ids.add(request_id)
            with self._protocol_lock:
                if self._protocol_failed or self._expected_id is not None:
                    raise KnowledgeTransportFailure(outcome_unknown=self._owner.started)
                self._expected_id = request_id
            if self._process is None:
                self._spawn()
            body = self._exchange(len(self._ids), len(raw).to_bytes(4, 'big') + raw)
            self._validate_reply(body, request_id)
            if not _json_object(body)['ok']:
                # A dispatcher timeout can leave a worker thread active. Never
                # dispatch another page in that process, even for a safe error.
                self._finish(aborted=True)
            return body
        except BaseException as error:
            try:
                self._finish(aborted=True)
            except BaseException:
                # Teardown still blocks publication; preserve trusted stop
                # controls rather than converting them to a retryable error.
                from .knowledge_transport import KnowledgeCooperativeAbort
                if isinstance(error, (KeyboardInterrupt, SystemExit, KnowledgeCooperativeAbort)):
                    raise error
                raise
            raise
        finally:
            self._lock.release()

    def _finish(self, *, aborted):
        if self._closed:
            return
        self._closed = True
        failure = None
        self.cleanup_failures = []
        try:
            if not aborted and self._process is not None:
                with self._protocol_lock:
                    if self._expected_id is not None:
                        raise KnowledgeTransportFailure(outcome_unknown=True)
                    self._closing = True
                self._exchange(len(self._ids) + 1, b'\0\0\0\0', terminal=True)
                while self._process.poll() is None:
                    self._tick()
                    try:
                        self._process.wait(timeout=min(.1, max(.001, self._deadline-time.monotonic())))
                    except subprocess.TimeoutExpired:
                        pass
                if self._process.returncode != 0 or not self._events.empty():
                    raise KnowledgeTransportFailure(outcome_unknown=True)
        except BaseException as error:
            failure = error
        # Every writer observes shutdown, even if a pending command fills its
        # queue. Owned termination releases a concurrent blocked pipe write.
        self._shutdown.set()
        from .knowledge_transport import KnowledgeCooperativeAbort
        for cleanup in (lambda:self._owner.stop(self._threads),
                        lambda:self._owner.cleanup_private_directory(self._directory) if self._directory is not None else None):
            try:
                cleanup()
            except BaseException as error:
                self.cleanup_failures.append(type(error).__name__)
                if not isinstance(failure, (KeyboardInterrupt, SystemExit, KnowledgeCooperativeAbort)):
                    failure = error
        if failure is not None:
            raise failure
        if not aborted:
            # Cleanup may safely finish after a deadline, but cannot authorize
            # a successful parent return after that deadline or a late revoke.
            self._tick()

    def __exit__(self, kind, value, traceback):
        if threading.get_ident() != self._thread_id or _task() is not self._task_id:
            raise KnowledgeBusy()
        try:
            self._finish(aborted=kind is not None)
        except BaseException:
            from .knowledge_transport import KnowledgeCooperativeAbort
            if isinstance(value, (KeyboardInterrupt, SystemExit, KnowledgeCooperativeAbort)):
                raise value
            raise
        return False
