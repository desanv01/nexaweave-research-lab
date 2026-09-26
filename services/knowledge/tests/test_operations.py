"""Pure validation checks; the PostgreSQL behavior lives in the opt-in module."""

import hashlib
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from mirofish_knowledge.contracts import KnowledgeScope, Layer, OntologySpec, SourceEnvelope
from mirofish_knowledge.operations import CompletionReceipt, Ledger, _error_code, _fingerprint, _receipt, request_fingerprint
from mirofish_knowledge.provider import _request_fingerprint


def _request():
    scope = KnowledgeScope(workspace_id=uuid4(), project_id=uuid4(), graph_id=uuid4(), layer=Layer.source)
    ontology = OntologySpec(revision=uuid4(), entity_types=({"name": "Person", "description": "A person"},), edge_types=())
    content = "Synthetic source"
    source = SourceEnvelope(source_revision=uuid4(), source_sha256=hashlib.sha256(content.encode()).hexdigest(),
                            ontology_revision=ontology.revision, operation_id=uuid4(), source_kind="document",
                            content=content, source_name="fixture", recorded_at=datetime.now(timezone.utc))
    return scope, source, ontology


def test_fingerprint_matches_existing_provider_and_rejects_unvalidated_copies():
    scope, source, ontology = _request()
    assert request_fingerprint(scope, source, ontology) == _request_fingerprint(scope, source, ontology)
    with pytest.raises(ValueError):
        request_fingerprint(scope, source.model_copy(update={"recorded_at": datetime.now()}), ontology)
    with pytest.raises(ValueError):
        request_fingerprint(scope, source, ontology.model_copy(update={"revision": uuid4()}))


def test_receipt_validation_is_bounded_and_identity_bound():
    scope, source, ontology = _request()
    fingerprint = request_fingerprint(scope, source, ontology)
    receipt = CompletionReceipt(scope.group_id, scope.episode_uuid(source.operation_id), fingerprint, (uuid4(),))
    assert _receipt(receipt, scope, source.operation_id, fingerprint) == receipt
    for changed in (
        CompletionReceipt("wrong", receipt.episode_id, fingerprint),
        CompletionReceipt(scope.group_id, uuid4(), fingerprint),
        CompletionReceipt(scope.group_id, receipt.episode_id, "0" * 64),
        CompletionReceipt(scope.group_id, receipt.episode_id, fingerprint, (uuid4(),) * 101),
    ):
        with pytest.raises(ValueError):
            _receipt(changed, scope, source.operation_id, fingerprint)
    with pytest.raises(ValueError):
        _receipt({**receipt.json_value(), "secret": "payload"}, scope, source.operation_id, fingerprint)


def test_input_validation_precedes_connection():
    scope, source, ontology = _request()
    ledger = Ledger(lambda: (_ for _ in ()).throw(AssertionError("connected")))
    with pytest.raises(ValueError):
        ledger.admit(scope, source.operation_id, "BAD")
    with pytest.raises(ValueError):
        ledger.mark_uncertain(scope, source.operation_id, uuid4(), "private: traceback")
    with pytest.raises(ValueError):
        ledger.claim(scope.model_copy(update={"layer": "invalid"}), source.operation_id)
    with pytest.raises(ValueError):
        ledger.complete(scope, source.operation_id, uuid4(), {"group_id": "wrong", "episode_id": str(scope.episode_uuid(source.operation_id)), "fingerprint": "a" * 64, "evidence_ids": []})
    with pytest.raises(ValueError):
        ledger.complete(scope, source.operation_id, uuid4(),
                        CompletionReceipt(scope.group_id, scope.episode_uuid(source.operation_id), "a" * 64, None))
    with pytest.raises(ValueError):
        ledger.complete(scope, source.operation_id, uuid4(),
                        CompletionReceipt(scope.group_id, scope.episode_uuid(source.operation_id), "a" * 64, []))
    with pytest.raises(ValueError):
        _fingerprint("A" * 64)
    with pytest.raises(ValueError):
        _error_code("Bad Code")
