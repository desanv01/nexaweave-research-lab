"""Bind a protected paired checkpoint to its prepared inputs and owned producer."""

from dataclasses import dataclass, field, replace
import json
import os
from pathlib import Path
from time import monotonic

from .native_owned_binding import NativeOwnedSessionFactory, _INPUT_LIMIT, _regular_bound_file
from .native_run_contracts import NativeRunReceipt, NativeRunRequest, RunState
from .native_run_store import NativeRunRecord
from .native_seed_contracts import seed_bridge_index, validate_native_seed_plan


PLATFORMS = ("twitter", "reddit")


def _base_request(request, run_id):
    return replace(request, schema_version=1, run_id=run_id, source_run_id=None,
                   checkpoint_sha256=None, intervention=None)


@dataclass
class NativePairedCheckpointFactory:
    prepared: NativeOwnedSessionFactory
    request: NativeRunRequest
    checkpoint_dir: str
    output_dir: str
    knowledge_scope: dict
    producer_record: NativeRunRecord | None = None
    _evidence: str | None = field(default=None, init=False, repr=False)

    @property
    def expected_producer_instance_id(self):
        return None if self.producer_record is None else self.producer_record.child.instance_id

    def check_request(self, request):
        request = NativeRunRequest.from_wire(request)
        producer = self.producer_record
        if (request != self.request or type(self.prepared) is not NativeOwnedSessionFactory
                or request.platforms != PLATFORMS or not 2 <= request.max_rounds <= 24
                or request.schema_version != (1 if producer is None else 5)
                or self.prepared.audited_scripted_interview is not None
                or type(self.knowledge_scope) is not dict
                or self.knowledge_scope.get("project_id") != str(request.project_id)):
            raise ValueError("invalid protected paired checkpoint binding")
        base = _base_request(request, request.run_id)
        self.prepared._check_prepared_request(base)
        if producer is not None:
            if (type(producer) is not NativeRunRecord or producer.state is not RunState.completed
                    or producer.lease_until is not None or producer.child is None
                    or producer.request != _base_request(request, request.source_run_id)
                    or producer.fingerprint != producer.request.fingerprint):
                raise ValueError("invalid protected paired checkpoint producer")
            receipt = NativeRunReceipt.from_wire(producer.receipt)
            if (receipt.run_id != request.source_run_id or receipt.outcome != "completed"
                    or receipt.request_fingerprint != producer.fingerprint
                    or receipt.evidence_sha256 != request.checkpoint_sha256
                    or receipt.attempt_id != producer.attempt_id
                    or receipt.instance_id != producer.child.instance_id):
                raise ValueError("invalid protected paired checkpoint receipt")
        return base

    def _scope(self, request, *, for_child=False):
        producer = self.producer_record
        return {
            "principal": request.principal,
            "project_id": str(request.project_id),
            "project_revision": request.project_revision,
            "knowledge_scope": self.knowledge_scope,
            "producer_run_id": str(request.source_run_id or request.run_id),
            "producer_pid": producer.child.process_id if producer else (os.getpid() if for_child else None),
        }

    def validate(self, request):
        from app.services.native_paired_checkpoint_bundle import load_paired_checkpoint, paired_anchors
        from app.services.native_recordings import _root
        from app.services.native_twitter_restore import _disjoint_native_paths, _new_native_directory

        base = self.check_request(request)
        self.prepared.validate(base)
        root = self.prepared._root()
        config = json.loads(_regular_bound_file(root, "simulation_config.json", _INPUT_LIMIT))
        actors = config.get("agent_configs")
        if (type(actors) is not list or len(actors) != 2
                or any(type(actor) is not dict or type(actor.get("agent_id")) is not int for actor in actors)
                or sorted(actor["agent_id"] for actor in actors) != [0, 1]
                or "execution_controls" in config
                or seed_bridge_index(config, PLATFORMS, len(validate_native_seed_plan(config))) is not None):
            raise ValueError("unsupported protected paired checkpoint profile")
        output = _new_native_directory(self.output_dir)
        if self.producer_record is None:
            checkpoint = _new_native_directory(self.checkpoint_dir)
            if os.path.lexists(checkpoint):
                raise ValueError("protected paired checkpoint already exists")
        else:
            checkpoint = _root(self.checkpoint_dir)
        _disjoint_native_paths(root, checkpoint, output)
        if os.path.lexists(output):
            raise ValueError("protected paired output already exists")
        if self.producer_record is not None:
            anchors = paired_anchors(root, self.prepared.graph_id, request.simulation_id,
                                     request.seed, request.max_rounds, request.runtime_sha256,
                                     self._scope(request))
            completed, _, _ = load_paired_checkpoint(
                checkpoint, expected_digest=request.checkpoint_sha256, expected_anchors=anchors,
                deadline=monotonic() + self.prepared.timeout_seconds,
            )
            if completed != 1:
                raise ValueError("invalid protected paired checkpoint cut")

    def create_session(self, request):
        self.check_request(request)
        return _ProtectedPairedSession(self, request)

    def evidence(self, request):
        self.check_request(request)
        if self._evidence is None:
            raise ValueError("missing protected paired checkpoint receipt")
        return self._evidence


class _ProtectedPairedSession:
    def __init__(self, factory, request):
        self._factory, self._request = factory, request
        self._session = None

    def start(self):
        from app.services.native_paired_checkpoint_bundle import PREPARED_FILES
        from app.services.native_paired_simulation import NativePairedCheckpointSession

        factory, request = self._factory, self._request
        factory.validate(request)
        prepared = factory.prepared
        base = factory.check_request(request)
        output = Path(factory.output_dir)
        output.mkdir(mode=0o700, exist_ok=False)
        original = prepared._root()
        for name in PREPARED_FILES:
            raw = _regular_bound_file(original, name, _INPUT_LIMIT)
            with (output / name).open("xb") as stream:
                stream.write(raw)
        replace(prepared, simulation_dir=str(output)).validate(base)
        transfer = ({"capture": (factory.checkpoint_dir, 1)} if factory.producer_record is None
                    else {"restore": (factory.checkpoint_dir, request.checkpoint_sha256)})
        if factory.producer_record is not None and os.getpid() == factory.producer_record.child.process_id:
            raise ValueError("paired continuation requires a fresh process")
        self._session = NativePairedCheckpointSession(
            str(output), graph_id=prepared.graph_id, simulation_id=prepared.simulation_id,
            models=prepared.model_factory(), seed=prepared.seed, max_rounds=prepared.max_rounds,
            timeout_seconds=prepared.timeout_seconds, runtime_sha256=prepared.runtime_sha256,
            scope=factory._scope(request, for_child=True), **transfer,
        )
        self._session.start()

    def close(self):
        session = self._session
        if session is None:
            return
        session.close()
        factory = self._factory
        if factory.producer_record is None:
            factory._evidence = session.checkpoint_sha256
        else:
            output = replace(factory.prepared, simulation_dir=factory.output_dir)
            factory._evidence = output.evidence(self._request)
