"""One-owner spawned native child for the durable NativeRunCoordinator."""
from __future__ import annotations

import hashlib
import multiprocessing
import queue
import threading
from dataclasses import dataclass
from uuid import UUID, uuid4

from .native_process_worker import (MAX_MESSAGE, child_main, decode_message,
                                    encode_message)
from .native_run_contracts import (InvalidNativeRun, NativeChildIdentity,
                                   NativeObservation, NativeRunReceipt, NativeRunRequest,
                                   NativeRunUnavailable, canonical_uuid, sha256)

_READY_KEYS = frozenset({"kind", "run_id", "attempt_id", "instance_id",
                         "request_fingerprint", "process_id"})
_TERMINAL_KEYS = frozenset({"kind", "run_id", "attempt_id", "instance_id",
                            "request_fingerprint", "outcome", "evidence_sha256"})


def _observation(status, receipt=None):
    return NativeObservation(status, receipt)


class _FrameReader:
    """One bounded reader per owned pipe; no blocking recv on caller threads."""

    def __init__(self, pipe):
        self.pipe = pipe
        self.events = queue.Queue(maxsize=4)
        self.overflow = False
        self.thread = threading.Thread(target=self._read, daemon=True,
                                       name="native-owned-frame-reader")
        self.thread.start()

    def _push(self, event):
        try:
            self.events.put_nowait(event)
        except queue.Full:
            self.overflow = True

    def _read(self):
        try:
            while True:
                self._push(("data", self.pipe.recv_bytes(MAX_MESSAGE)))
                if self.overflow:
                    return
        except EOFError:
            self._push(("eof", None))
        except Exception:
            self._push(("error", None))

    def next(self, seconds):
        try:
            return self.events.get(timeout=seconds)
        except queue.Empty:
            return None

    def drain(self):
        values = []
        while True:
            try:
                values.append(self.events.get_nowait())
            except queue.Empty:
                return values


@dataclass
class _Owned:
    request: NativeRunRequest
    attempt: UUID
    child: NativeChildIdentity
    process: object
    pipe: object
    reader: _FrameReader
    sent_go: bool = False
    terminal: NativeRunReceipt | None = None
    settled: object | None = None
    invalid: bool = False


class NativeProcessDriver:
    """A driver instance never adopts a saved PID or another instance's pipe."""

    def __init__(self, session_factory, *, handshake_seconds=5.0,
                 observe_seconds=0.05, grace_seconds=1.0,
                 join_seconds=1.0, go_timeout_seconds=20.0):
        if (not callable(getattr(session_factory, "validate", None))
                or not callable(getattr(session_factory, "create_session", None))
                or not callable(getattr(session_factory, "evidence", None))
                or any(type(value) not in (int, float) or not 0 < value <= 30
                       for value in (handshake_seconds, observe_seconds,
                                     grace_seconds, join_seconds, go_timeout_seconds))):
            raise InvalidNativeRun()
        self._factory = session_factory
        self._handshake = handshake_seconds
        self._observe_wait = observe_seconds
        self._grace = grace_seconds
        self._join = join_seconds
        self._go_timeout = go_timeout_seconds
        self._owned: _Owned | None = None
        self._pending = None
        self._spent = False
        self._closed = False

    def _stop(self, process):
        if process is None or process.pid is None:
            return True
        process.join(0)
        if process.is_alive():
            process.terminate()
            process.join(self._join)
        if process.is_alive():
            process.kill()
            process.join(self._join)
        return not process.is_alive()

    def _release(self, process, pipe, reader):
        try:
            stopped = self._stop(process)
            if not stopped:
                return False
            if pipe is not None:
                pipe.close()
            if reader is not None:
                reader.thread.join(self._join)
                if reader.thread.is_alive():
                    return False
            if process is not None and process.pid is not None:
                process.close()
            return True
        except Exception:
            return False

    def launch(self, request: NativeRunRequest, attempt_id: UUID) -> NativeChildIdentity:
        request = NativeRunRequest.from_wire(request)
        attempt = canonical_uuid(attempt_id)
        if self._closed or self._spent:
            raise InvalidNativeRun()
        self._spent = True
        instance = uuid4()
        parent = None
        child_pipe = None
        process = None
        reader = None
        try:
            context = multiprocessing.get_context("spawn")
            parent, child_pipe = context.Pipe(duplex=True)
            process = context.Process(target=child_main, args=(
                child_pipe, encode_message(request.to_wire()), str(attempt),
                str(instance), self._factory, self._go_timeout), daemon=False)
            process.start()
            try:
                child_pipe.close()
            except Exception:
                pass
            reader = _FrameReader(parent)
            event = reader.next(self._handshake)
            if event is None or event[0] != "data" or reader.overflow:
                raise NativeRunUnavailable()
            ready = decode_message(event[1], _READY_KEYS)
            if (ready["kind"] != "ready" or ready["run_id"] != str(request.run_id)
                    or ready["attempt_id"] != str(attempt)
                    or ready["instance_id"] != str(instance)
                    or ready["request_fingerprint"] != request.fingerprint
                    or type(ready["process_id"]) is not int
                    or ready["process_id"] != process.pid or not process.is_alive()
                    or reader.drain()):
                raise InvalidNativeRun()
            fingerprint = hashlib.sha256(
                f"{request.fingerprint}:{attempt}:{instance}:{process.pid}".encode("ascii")
            ).hexdigest()
            identity = NativeChildIdentity(instance, process.pid, fingerprint)
            self._owned = _Owned(request, attempt, identity, process, parent, reader)
            return identity
        except BaseException as error:
            if parent is not None or process is not None:
                self._pending = (process, parent, reader)
            try:
                self.close()
            except Exception:
                pass
            try:
                if child_pipe is not None:
                    child_pipe.close()
            except Exception:
                pass
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            raise NativeRunUnavailable() from None

    def _handle(self, request, attempt_id, child):
        request = NativeRunRequest.from_wire(request)
        attempt = canonical_uuid(attempt_id)
        child = NativeChildIdentity.from_wire(child)
        owned = self._owned
        if owned is None or self._closed:
            return None
        if (owned.request != request or owned.attempt != attempt
                or owned.child != child or owned.process.pid != child.process_id):
            raise InvalidNativeRun()
        return owned

    def _take_terminal(self, owned, event):
        if event is None:
            return
        if event[0] != "data" or owned.terminal is not None:
            owned.invalid = True
            return
        try:
            value = decode_message(event[1], _TERMINAL_KEYS)
            if (value["kind"] != "terminal"
                    or value["run_id"] != str(owned.request.run_id)
                    or value["attempt_id"] != str(owned.attempt)
                    or value["instance_id"] != str(owned.child.instance_id)
                    or value["request_fingerprint"] != owned.request.fingerprint
                    or value["outcome"] not in ("completed", "failed")):
                raise ValueError
            owned.terminal = NativeRunReceipt.from_wire({
                "run_id": value["run_id"], "attempt_id": value["attempt_id"],
                "instance_id": value["instance_id"],
                "request_fingerprint": value["request_fingerprint"],
                "outcome": value["outcome"],
                "evidence_sha256": sha256(value["evidence_sha256"])})
        except Exception:
            owned.invalid = True

    def _qualified_terminal(self, owned):
        owned.process.join(self._join if owned.terminal else 0)
        if owned.process.exitcode is None:
            return _observation("running")
        owned.reader.thread.join(self._join)
        extra = owned.reader.drain()
        if (owned.reader.overflow or owned.reader.thread.is_alive()
                or any(kind != "eof" for kind, _ in extra)
                or owned.invalid or owned.process.exitcode != 0
                or owned.terminal is None):
            owned.invalid = True
            return _observation("unknown")
        owned.settled = _observation(owned.terminal.outcome, owned.terminal)
        return owned.settled

    def observe(self, request, attempt_id, child):
        owned = self._handle(request, attempt_id, child)
        if owned is None:
            return _observation("absent")
        if owned.settled is not None:
            return owned.settled
        if owned.invalid or owned.reader.overflow:
            owned.invalid = True
            return _observation("unknown")
        if not owned.sent_go:
            try:
                owned.pipe.send_bytes(encode_message({
                    "kind": "go", "run_id": str(owned.request.run_id),
                    "attempt_id": str(owned.attempt),
                    "instance_id": str(owned.child.instance_id)}))
                owned.sent_go = True
            except Exception:
                owned.invalid = True
                return _observation("unknown")
        try:
            if owned.terminal is None:
                self._take_terminal(owned, owned.reader.next(self._observe_wait))
            if owned.invalid:
                return _observation("unknown")
            if owned.terminal is not None:
                return self._qualified_terminal(owned)
            owned.process.join(0)
            if owned.process.exitcode is not None:
                owned.reader.thread.join(self._join)
                for event in owned.reader.drain():
                    if event[0] == "eof":
                        continue
                    self._take_terminal(owned, event)
                if owned.terminal is not None and not owned.invalid:
                    return self._qualified_terminal(owned)
                owned.invalid = True
                return _observation("unknown")
            return _observation("running")
        except Exception:
            owned.invalid = True
            return _observation("unknown")

    def cancel(self, request, attempt_id, child):
        owned = self._handle(request, attempt_id, child)
        if owned is None:
            return _observation("absent")
        if owned.settled is not None:
            return owned.settled
        try:
            if owned.invalid or owned.reader.overflow:
                self._stop(owned.process)
                owned.invalid = True
                return _observation("unknown")
            if owned.sent_go and owned.terminal is None:
                self._take_terminal(owned, owned.reader.next(0))
            if owned.invalid:
                self._stop(owned.process)
                return _observation("unknown")
            if owned.terminal is not None or (owned.sent_go and not owned.process.is_alive()):
                return self.observe(request, attempt_id, child)
            if not owned.sent_go and owned.process.is_alive():
                owned.pipe.send_bytes(encode_message({
                    "kind": "cancel", "run_id": str(owned.request.run_id),
                    "attempt_id": str(owned.attempt),
                    "instance_id": str(owned.child.instance_id)}))
                owned.process.join(self._grace)
            if not self._stop(owned.process):
                return _observation("unknown")
            evidence = hashlib.sha256(
                f"cancelled:{owned.request.fingerprint}:{owned.attempt}:"
                f"{owned.child.instance_id}".encode("ascii")).hexdigest()
            receipt = NativeRunReceipt(owned.request.run_id, owned.attempt,
                owned.child.instance_id, owned.request.fingerprint,
                "cancelled", evidence)
            owned.settled = _observation("cancelled", receipt)
            return owned.settled
        except Exception:
            owned.invalid = True
            return _observation("unknown")

    def close(self):
        if self._closed:
            return True
        target = self._owned
        if target is not None:
            if not self._release(target.process, target.pipe, target.reader):
                return False
            self._owned = None
        pending = self._pending
        if pending is not None:
            if not self._release(*pending):
                return False
            self._pending = None
        self._closed = True
        return True
