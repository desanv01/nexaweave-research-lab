"""Adapter from protected Reddit checkpoint requests to the owned native child."""

import asyncio
import os
from dataclasses import dataclass, field, replace
from time import monotonic

from .native_owned_binding import NativeOwnedSessionFactory
from .native_run_contracts import NativeChildIdentity, NativeRunReceipt, NativeRunRequest, RunState, canonical_uuid
from .native_run_store import NativeRunRecord


def _base_request(request, run_id):
    return replace(request, schema_version=1, run_id=run_id, source_run_id=None,
                   checkpoint_sha256=None, intervention=None)


@dataclass
class NativeRedditCheckpointFactory:
    prepared: NativeOwnedSessionFactory
    request: NativeRunRequest
    checkpoint_dir: str
    output_dir: str
    producer_record: NativeRunRecord | None = None
    _evidence: str | None = field(default=None, init=False, repr=False)

    @property
    def producer(self):
        return self.producer_record.child if self.producer_record is not None else None

    @property
    def expected_producer_instance_id(self):
        return self.producer.instance_id if self.producer is not None else None

    @staticmethod
    def check_producer(request, record):
        """Link a continuation to the settled native producer before any file reads."""
        request = NativeRunRequest.from_wire(request)
        if request.schema_version == 1 and record is None:
            return
        if request.schema_version != 4 or type(record) is not NativeRunRecord:
            raise ValueError("invalid protected Reddit producer")
        if (type(record.request) is not NativeRunRequest or type(record.receipt) is not NativeRunReceipt
                or type(record.child) is not NativeChildIdentity or record.state is not RunState.completed
                or record.lease_until is not None or canonical_uuid(record.owner_id) != record.owner_id
                or NativeChildIdentity.from_wire(record.child) != record.child):
            raise ValueError("invalid protected Reddit producer")
        expected = _base_request(request, request.source_run_id)
        receipt = NativeRunReceipt.from_wire(record.receipt)
        if (record.request != expected or record.fingerprint != record.request.fingerprint
                or receipt.run_id != request.source_run_id or receipt.outcome != 'completed'
                or receipt.request_fingerprint != record.fingerprint
                or receipt.evidence_sha256 != request.checkpoint_sha256
                or receipt.attempt_id != record.attempt_id or receipt.instance_id != record.child.instance_id):
            raise ValueError("invalid protected Reddit producer")

    def check_request(self, request):
        """Scalar checks only. Used before the host opens preparation files."""
        request = NativeRunRequest.from_wire(request)
        if (request != self.request or type(self.prepared) is not NativeOwnedSessionFactory
                or request.platforms != ('reddit',) or request.max_rounds != 1
                or self.prepared.audited_scripted_interview is not None
                or type(self.prepared.timeout_seconds) not in (int, float)
                or not 0 < self.prepared.timeout_seconds <= 600):
            raise ValueError("invalid protected Reddit checkpoint binding")
        self.check_producer(request, self.producer_record)
        base = _base_request(request, request.run_id)
        self.prepared._check_prepared_request(base)
        return base

    def validate(self, request):
        base = self.check_request(request)
        from app.services.native_reddit_checkpoint import _inputs, _restore_manifest, _scope
        from app.services.native_twitter_restore import _disjoint_native_paths, _new_native_directory
        from app.services.native_recordings import _root

        deadline = monotonic() + self.prepared.timeout_seconds
        root, _, config_digest, profiles, _, actor_id, _ = _inputs(self.prepared, base, deadline)
        output = _new_native_directory(self.output_dir)
        if self.producer is None:
            checkpoint = _new_native_directory(self.checkpoint_dir)
        else:
            checkpoint = _root(self.checkpoint_dir)
            _restore_manifest(checkpoint, request.checkpoint_sha256, self.producer.process_id,
                              _scope(self.prepared, self.producer_record.request),
                              config_digest, profiles, actor_id, deadline)
        _disjoint_native_paths(root, checkpoint, output)
        if os.path.lexists(output) or (self.producer is None and os.path.lexists(checkpoint)):
            raise ValueError("protected Reddit checkpoint output already exists")

    def create_session(self, request):
        self.check_request(request)
        return _RedditCheckpointSession(self, request)

    def evidence(self, request):
        self.check_request(request)
        if self._evidence is None:
            raise ValueError("missing protected Reddit checkpoint evidence")
        return self._evidence


class _RedditCheckpointSession:
    def __init__(self, factory, request):
        self._factory, self._request = factory, request
        self._result = None

    def start(self):
        from app.services.native_reddit_checkpoint import produce_reddit_checkpoint, restore_reddit_checkpoint

        factory, request = self._factory, self._request
        base = factory.check_request(request)
        deadline = monotonic() + factory.prepared.timeout_seconds
        if factory.producer is None:
            self._result = asyncio.run(produce_reddit_checkpoint(
                factory.prepared, base, factory.output_dir,
                checkpoint_dir=factory.checkpoint_dir, deadline=deadline,
            ))
        else:
            proof = asyncio.run(restore_reddit_checkpoint(
                factory.prepared, base, producer_request=factory.producer_record.request,
                producer_pid=factory.producer.process_id, checkpoint_dir=factory.checkpoint_dir,
                expected_digest=request.checkpoint_sha256, child_dir=factory.output_dir, deadline=deadline,
            ))
            self._result = proof['database']['sha256']

    def close(self):
        # Both engine entry points close their OASIS/SQLite owners before
        # returning evidence. A failed start leaves the receipt unavailable.
        from .native_run_contracts import sha256

        if self._result is not None:
            self._factory._evidence = sha256(self._result)
