"""Synthetic tests for the in-process command contract; no external services."""

import asyncio
import hashlib
import json
import warnings
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from nexaweave_knowledge.commands import KnowledgeCommandDispatcher
from nexaweave_knowledge.contracts import (FactResult, GraphPage, KnowledgeScope, Layer,
                                          OntologySpec, SearchResult, SourceEnvelope)
from nexaweave_knowledge.graph_reads import ResultTooLarge
from nexaweave_knowledge.ingestion import IngestionUncertain
from nexaweave_knowledge.operations import (Busy, CompletionReceipt, Conflict, NotFound,
                                           Tombstoned, request_fingerprint)
from nexaweave_knowledge.provider import UnsupportedCapability


def fixture_request():
    scope = KnowledgeScope(workspace_id=uuid4(), project_id=uuid4(), graph_id=uuid4(), layer=Layer.source)
    ontology = OntologySpec(revision=uuid4(), entity_types=({"name": "Person", "description": "Person"},), edge_types=())
    content = "Mira works in Kuala Lumpur. 市场观察。"
    source = SourceEnvelope(source_revision=uuid4(), source_sha256=hashlib.sha256(content.encode()).hexdigest(),
                            ontology_revision=ontology.revision, operation_id=uuid4(), source_kind="document",
                            content=content, source_name="Synthetic note", recorded_at=datetime.now(timezone.utc),
                            evidence_ids=(uuid4(),))
    return scope, source, ontology


def wire(scope, method, payload, *, request_id=None, **extra):
    root = {"version": 1, "request_id": request_id or str(uuid4()), "method": method,
            "scope": scope.model_dump(mode="json"), "payload": payload, **extra}
    return json.dumps(root, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def reply(raw):
    return json.loads(raw.decode("utf-8"))


def fact(scope):
    identifier = str(uuid4())
    return FactResult(provider_id=identifier, scope=scope, kind="node", name="Mira 市场",
                      episode_ids=(str(uuid4()),), evidence_ids=(uuid4(),))


class Provider:
    def __init__(self):
        self.calls = []
        self.failure = None
        self.output_scope = None

    def _result(self, scope):
        if self.failure:
            raise self.failure
        return fact(self.output_scope or scope)

    async def page(self, scope, request):
        self.calls.append(("page", scope, request))
        return GraphPage(facts=(self._result(scope),), next_cursor=None)

    async def entity(self, scope, provider_id):
        self.calls.append(("entity", scope, provider_id))
        return SearchResult(facts=(self._result(scope),))

    async def search(self, scope, query):
        self.calls.append(("search", scope, query))
        return SearchResult(facts=(self._result(scope),))

    async def ingest(self, *_args):
        raise AssertionError("direct provider ingestion bypassed coordinator")


class Coordinator:
    def __init__(self):
        self.calls = []
        self.failure = None
        self.receipt_change = None

    async def ingest(self, scope, source, ontology):
        self.calls.append((scope, source, ontology))
        if self.failure:
            raise self.failure
        receipt = CompletionReceipt(scope.group_id, scope.episode_uuid(source.operation_id),
                                    request_fingerprint(scope, source, ontology), source.evidence_ids)
        return self.receipt_change(receipt) if self.receipt_change else receipt


class Policy:
    def __init__(self, grant=True):
        self.grant = grant
        self.calls = []

    async def __call__(self, principal, method, scope):
        self.calls.append((principal, method, scope))
        if isinstance(self.grant, Exception):
            raise self.grant
        return self.grant


@pytest.mark.asyncio
async def test_all_routes_unicode_roundtrip_and_receipt_only():
    scope, source, ontology = fixture_request()
    provider, coordinator, policy = Provider(), Coordinator(), Policy()
    dispatcher = KnowledgeCommandDispatcher(provider, coordinator, policy, allow_model_calls=True)
    cases = (
        ("page", {"kind": "node", "limit": 1}),
        ("entity", {"provider_id": str(uuid4())}),
        ("search", {"text": "Mira 市场", "top_k": 3}),
        ("ingest", {"source": source.model_dump(mode="json"), "ontology": ontology.model_dump(mode="json")}),
    )
    for method, payload in cases:
        request_id = str(uuid4())
        result = reply(await dispatcher.dispatch(wire(scope, method, payload, request_id=request_id), principal="reader-1"))
        assert set(result) == {"version", "request_id", "ok", "result"}
        assert result["version"] == 1 and result["request_id"] == request_id and result["ok"] is True
        if method == "ingest":
            assert set(result["result"]) == {"group_id", "episode_id", "fingerprint", "evidence_ids"}
            assert result["result"]["episode_id"] == str(scope.episode_uuid(source.operation_id))
            assert "attempt_id" not in result["result"] and len(coordinator.calls) == 1
        else:
            assert result["result"]["facts"][0]["name"] == "Mira 市场"
    assert [call[0] for call in provider.calls] == ["page", "entity", "search"]
    assert len(policy.calls) == 4 and all(call[2] == scope for call in policy.calls)


@pytest.mark.asyncio
async def test_malformed_requests_and_principal_do_no_work():
    scope, source, ontology = fixture_request()
    provider, coordinator, policy = Provider(), Coordinator(), Policy()
    dispatcher = KnowledgeCommandDispatcher(provider, coordinator, policy, allow_model_calls=True)
    valid = json.loads(wire(scope, "page", {"kind": "node"}))
    malformed = [
        b"\xff", b"{" + b" " * (512 * 1024),
        b'{"version":1,"version":1,"request_id":"x","method":"page","scope":{},"payload":{}}',
        b'{"version":NaN,"request_id":"x","method":"page","scope":{},"payload":{}}',
        wire(scope, "unknown", {}), wire(scope, "page", {"kind": "node"}, extra="bad"),
        json.dumps({**valid, "version": True}).encode(),
        json.dumps({**valid, "request_id": "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"}).encode(),
        wire(scope, "entity", {"provider_id": str(uuid4()).upper()}),
        wire(scope, "page", {"kind": "edge", "entity_type": "Person"}),
        wire(scope, "ingest", {"source": {**source.model_dump(mode="json"), "content": "wrong hash"},
                               "ontology": ontology.model_dump(mode="json")}),
        json.dumps({**valid, "payload": {"kind": "node", "nested": [0] * 1}}).encode(),
        json.dumps({**valid, "payload": {"kind": "node"}, "scope": {**valid["scope"], "project_id": "bad"}}).encode(),
        json.dumps({**valid, "scope": {**valid["scope"], "schema_version": True}}).encode(),
    ]
    nested = {"kind": "node"}
    for _ in range(34):
        nested = [nested]
    malformed.append(json.dumps({**valid, "payload": nested}).encode())
    malformed.append(
        ('{"version":1,"request_id":"%s","method":"page","scope":%s,'
         '"payload":{"kind":"node","kind":"edge"}}'
         % (str(uuid4()), json.dumps(valid["scope"]))).encode()
    )
    for raw in malformed:
        result = reply(await dispatcher.dispatch(raw, principal="reader-1"))
        assert result["ok"] is False and result["error"] == {"code": "invalid_request"}
    for principal in ("", " ", "\n", "é", "x" * 129):
        result = reply(await dispatcher.dispatch(wire(scope, "page", {"kind": "node"}), principal=principal))
        assert result["error"] == {"code": "unauthorized"}
    assert not policy.calls and not provider.calls and not coordinator.calls


@pytest.mark.asyncio
async def test_denial_and_model_gate_after_authorization():
    scope, source, ontology = fixture_request()
    for grant in (False, None, 1, "true", RuntimeError("private policy secret"),
                  asyncio.TimeoutError("private policy timeout")):
        provider, coordinator, policy = Provider(), Coordinator(), Policy(grant)
        dispatcher = KnowledgeCommandDispatcher(provider, coordinator, policy, allow_model_calls=True)
        result = reply(await dispatcher.dispatch(wire(scope, "page", {"kind": "node"}), principal="reader"))
        assert result["error"] == {"code": "unauthorized"}
        assert "private" not in str(result)
        assert len(policy.calls) == 1 and not provider.calls and not coordinator.calls
    provider, coordinator, policy = Provider(), Coordinator(), Policy()
    dispatcher = KnowledgeCommandDispatcher(provider, coordinator, policy)
    for method, payload in (("search", {"text": "query"}),
                            ("ingest", {"source": source.model_dump(mode="json"),
                                        "ontology": ontology.model_dump(mode="json")})):
        result = reply(await dispatcher.dispatch(wire(scope, method, payload), principal="reader"))
        assert result["error"] == {"code": "model_calls_disabled"}
    assert len(policy.calls) == 2 and not provider.calls and not coordinator.calls


@pytest.mark.asyncio
async def test_scope_swaps_and_receipt_forgery_fail_closed():
    scope, source, ontology = fixture_request()
    other, _, _ = fixture_request()
    provider, coordinator, policy = Provider(), Coordinator(), Policy()
    provider.output_scope = other
    dispatcher = KnowledgeCommandDispatcher(provider, coordinator, policy, allow_model_calls=True)
    result = reply(await dispatcher.dispatch(wire(scope, "page", {"kind": "node"}), principal="reader"))
    assert result["error"] == {"code": "internal_error"}
    for change in (
        lambda receipt: CompletionReceipt(other.group_id, receipt.episode_id, receipt.fingerprint, receipt.evidence_ids),
        lambda receipt: CompletionReceipt(receipt.group_id, uuid4(), receipt.fingerprint, receipt.evidence_ids),
        lambda receipt: CompletionReceipt(receipt.group_id, receipt.episode_id, receipt.fingerprint, (uuid4(),)),
    ):
        coordinator.receipt_change = change
        result = reply(await dispatcher.dispatch(wire(scope, "ingest", {"source": source.model_dump(mode="json"),
                                                                    "ontology": ontology.model_dump(mode="json")}), principal="reader"))
        assert result["error"] == {"code": "uncertain"}


@pytest.mark.asyncio
async def test_result_identity_score_count_and_wire_size_bounds():
    scope, _, _ = fixture_request()

    class OutputProvider(Provider):
        def __init__(self, result):
            super().__init__()
            self.result = result

        async def page(self, scope, request):
            return self.result

        async def search(self, scope, query):
            return self.result

    bad_uuid = fact(scope).model_copy(update={"provider_id": "not-uuid"})
    bad_endpoint = fact(scope).model_copy(update={"kind": "edge", "source_node_id": str(uuid4()),
                                                  "target_node_id": "bad"})
    bad_episode = fact(scope).model_copy(update={"episode_ids": ("bad",)})
    bad_score = fact(scope).model_copy(update={"score": float("nan")})
    for bad in (bad_uuid, bad_endpoint, bad_episode, bad_score):
        provider = OutputProvider(GraphPage(facts=(bad,)))
        outcome = reply(await KnowledgeCommandDispatcher(provider, Coordinator(), Policy()).dispatch(
            wire(scope, "page", {"kind": "node"}), principal="reader"))
        assert outcome["error"] == {"code": "internal_error"}

    many = SearchResult(facts=(fact(scope),) * 301)
    outcome = reply(await KnowledgeCommandDispatcher(OutputProvider(many), Coordinator(), Policy(),
                                                      allow_model_calls=True).dispatch(
        wire(scope, "search", {"text": "query"}), principal="reader"))
    assert outcome["error"] == {"code": "internal_error"}

    huge = fact(scope).model_copy(update={"name": "x" * (2 * 1024 * 1024)})
    outcome = reply(await KnowledgeCommandDispatcher(OutputProvider(GraphPage(facts=(huge,))),
                                                      Coordinator(), Policy()).dispatch(
        wire(scope, "page", {"kind": "node"}), principal="reader"))
    assert outcome["error"] == {"code": "result_too_large"}


@pytest.mark.asyncio
async def test_method_specific_result_dtos_reject_swapped_shapes():
    scope, _, _ = fixture_request()

    class SwappedProvider(Provider):
        async def page(self, scope, request):
            return SearchResult(facts=(fact(scope),))

        async def entity(self, scope, provider_id):
            return GraphPage(facts=(fact(scope),))

        async def search(self, scope, query):
            return GraphPage(facts=(fact(scope),))

    dispatcher = KnowledgeCommandDispatcher(SwappedProvider(), Coordinator(), Policy(), allow_model_calls=True)
    for method, payload in (("page", {"kind": "node"}),
                            ("entity", {"provider_id": str(uuid4())}),
                            ("search", {"text": "query"})):
        outcome = reply(await dispatcher.dispatch(wire(scope, method, payload), principal="reader"))
        assert outcome["error"] == {"code": "internal_error"}


@pytest.mark.asyncio
async def test_metadata_wire_output_and_bypassed_validation_fails_privately():
    scope, _, _ = fixture_request()
    expiry = datetime(2024, 3, 1, tzinfo=timezone.utc)

    class MetadataProvider(Provider):
        def __init__(self):
            super().__init__()
            self.bad = None
            self.inject_bad = False

        async def page(self, scope, request):
            item = fact(scope).model_copy(update={"labels": ("Entity", "人物"),
                                                   "summary": "研究摘要"})
            if self.inject_bad:
                item = item.model_copy(update=self.bad)
            return GraphPage(facts=(item,))

        async def entity(self, scope, provider_id):
            edge = FactResult(provider_id=str(uuid4()), scope=scope, kind="edge",
                              source_node_id=str(uuid4()), target_node_id=str(uuid4()),
                              invalid_at=datetime(2024, 2, 1, tzinfo=timezone.utc), expired_at=expiry)
            return SearchResult(facts=(edge,))

    provider = MetadataProvider()
    dispatcher = KnowledgeCommandDispatcher(provider, Coordinator(), Policy())
    result = reply(await dispatcher.dispatch(wire(scope, "page", {"kind": "node"}), principal="reader"))
    item = result["result"]["facts"][0]
    assert item["labels"] == ["Entity", "人物"] and item["summary"] == "研究摘要"
    edge_result = reply(await dispatcher.dispatch(
        wire(scope, "entity", {"provider_id": str(uuid4())}), principal="reader"))
    edge = edge_result["result"]["facts"][0]
    assert edge["expired_at"] == expiry.isoformat().replace("+00:00", "Z")
    assert edge["expired_at"] != edge["invalid_at"]
    for bad_metadata, private_value in (
        ({"labels": ("secret\nmodel-payload",)}, "model-payload"),
        ({"labels": "private-label-string"}, "private-label-string"),
        ({"labels": {"private-label-key": True}}, "private-label-key"),
        ({"labels": None}, None),
        ({"labels": False}, None),
        ({"summary": "private-summary" + "x" * 32769}, "private-summary"),
    ):
        provider.bad = bad_metadata
        provider.inject_bad = True
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always")
            failed = reply(await dispatcher.dispatch(wire(scope, "page", {"kind": "node"}), principal="reader"))
        assert failed["error"] == {"code": "internal_error"}
        if private_value is not None:
            assert private_value not in str(failed)
            assert all(private_value not in str(item.message) for item in captured)
        assert not captured


@pytest.mark.asyncio
async def test_fixed_domain_errors_and_private_details():
    scope, _, _ = fixture_request()
    mappings = ((Busy("private"), "busy"), (NotFound("private"), "not_found"),
                (Conflict("private"), "conflict"), (Tombstoned("private"), "tombstoned"),
                (UnsupportedCapability("private"), "unsupported"),
                (ResultTooLarge("private"), "result_too_large"),
                (RuntimeError("private provider key"), "internal_error"))
    for error, code in mappings:
        provider, coordinator, policy = Provider(), Coordinator(), Policy()
        provider.failure = error
        result = reply(await KnowledgeCommandDispatcher(provider, coordinator, policy).dispatch(
            wire(scope, "page", {"kind": "node"}), principal="reader"))
        assert result["error"] == {"code": code} and "private" not in str(result)
    provider, coordinator, policy = Provider(), Coordinator(), Policy()
    coordinator.failure = RuntimeError("private model payload")
    _, source, ontology = fixture_request()
    # Use one matching source/ontology pair to reach the coordinator.
    result = reply(await KnowledgeCommandDispatcher(provider, coordinator, policy, allow_model_calls=True).dispatch(
        wire(scope, "ingest", {"source": source.model_dump(mode="json"),
                               "ontology": ontology.model_dump(mode="json")}), principal="reader"))
    assert result["error"] == {"code": "uncertain"} and "private" not in str(result)

    coordinator.failure = asyncio.TimeoutError("private coordinator timeout after dispatch")
    result = reply(await KnowledgeCommandDispatcher(provider, coordinator, policy, allow_model_calls=True).dispatch(
        wire(scope, "ingest", {"source": source.model_dump(mode="json"),
                               "ontology": ontology.model_dump(mode="json")}), principal="reader"))
    assert result["error"] == {"code": "uncertain"} and "private" not in str(result)


@pytest.mark.asyncio
async def test_read_timeout_suppressed_cancellation_and_active_cap_restoration():
    scope, _, _ = fixture_request()
    entered, release = asyncio.Event(), asyncio.Event()

    class WaitingProvider(Provider):
        async def page(self, scope, request):
            self.calls.append(("page", scope, request))
            entered.set()
            await release.wait()
            return GraphPage(facts=(fact(scope),))

    provider, coordinator, policy = WaitingProvider(), Coordinator(), Policy()
    dispatcher = KnowledgeCommandDispatcher(provider, coordinator, policy,
                                             max_inflight=1, read_timeout_seconds=0.05)
    first = asyncio.create_task(dispatcher.dispatch(wire(scope, "page", {"kind": "node"}), principal="first"))
    await asyncio.wait_for(entered.wait(), timeout=1)
    second = reply(await dispatcher.dispatch(wire(scope, "page", {"kind": "node"}), principal="second"))
    assert second["error"] == {"code": "busy"}
    release.set()
    assert reply(await first)["ok"] is True
    third = reply(await dispatcher.dispatch(wire(scope, "page", {"kind": "node"}), principal="third"))
    assert third["ok"] is True
    assert [call[0] for call in policy.calls] == ["first", "third"]

    class LateProvider(Provider):
        async def page(self, scope, request):
            try:
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                return GraphPage(facts=(fact(scope),))

    late = KnowledgeCommandDispatcher(LateProvider(), Coordinator(), Policy(), read_timeout_seconds=0.01)
    outcome = reply(await late.dispatch(wire(scope, "page", {"kind": "node"}), principal="reader"))
    assert outcome["error"] == {"code": "timeout"}

    class LatePolicy(Policy):
        async def __call__(self, principal, method, scope):
            try:
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                return True

    timeout_policy_provider = Provider()
    outcome = reply(await KnowledgeCommandDispatcher(
        timeout_policy_provider, Coordinator(), LatePolicy(), read_timeout_seconds=0.01
    ).dispatch(wire(scope, "page", {"kind": "node"}), principal="reader"))
    assert outcome["error"] == {"code": "timeout"} and not timeout_policy_provider.calls


@pytest.mark.asyncio
async def test_caller_cancellation_propagates_and_slot_reopens():
    scope, _, _ = fixture_request()
    entered = asyncio.Event()

    class WaitingProvider(Provider):
        async def page(self, scope, request):
            entered.set()
            await asyncio.Event().wait()

    dispatcher = KnowledgeCommandDispatcher(WaitingProvider(), Coordinator(), Policy(), max_inflight=1)
    task = asyncio.create_task(dispatcher.dispatch(wire(scope, "page", {"kind": "node"}), principal="reader"))
    await asyncio.wait_for(entered.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert dispatcher._active == 0


@pytest.mark.asyncio
async def test_ingest_cancellation_reaches_coordinator_and_isolated_callers():
    first_scope, source, ontology = fixture_request()
    other_scope, _, _ = fixture_request()
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    class WaitingCoordinator(Coordinator):
        async def ingest(self, scope, source, ontology):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    policy = Policy()
    dispatcher = KnowledgeCommandDispatcher(Provider(), WaitingCoordinator(), policy,
                                             allow_model_calls=True, max_inflight=1)
    ingest_payload = {"source": source.model_dump(mode="json"), "ontology": ontology.model_dump(mode="json")}
    task = asyncio.create_task(dispatcher.dispatch(wire(first_scope, "ingest", ingest_payload), principal="writer-A"))
    await asyncio.wait_for(entered.wait(), timeout=1)
    assert reply(await dispatcher.dispatch(wire(other_scope, "page", {"kind": "node"}),
                                           principal="reader-B"))["error"] == {"code": "busy"}
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.wait_for(cancelled.wait(), timeout=1)
    assert dispatcher._active == 0
    outcome = reply(await dispatcher.dispatch(wire(other_scope, "page", {"kind": "node"}),
                                              principal="reader-B"))
    assert outcome["ok"] is True
    assert [(principal, scope) for principal, _, scope in policy.calls] == [
        ("writer-A", first_scope), ("reader-B", other_scope)]


@pytest.mark.asyncio
async def test_coordinator_suppressed_cancellation_cannot_return_success():
    scope, source, ontology = fixture_request()
    entered = asyncio.Event()

    class SuppressingCoordinator(Coordinator):
        async def ingest(self, scope, source, ontology):
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return await super().ingest(scope, source, ontology)

    coordinator = SuppressingCoordinator()
    dispatcher = KnowledgeCommandDispatcher(Provider(), coordinator, Policy(),
                                             allow_model_calls=True, max_inflight=1)
    payload = {"source": source.model_dump(mode="json"), "ontology": ontology.model_dump(mode="json")}
    task = asyncio.create_task(dispatcher.dispatch(wire(scope, "ingest", payload), principal="writer"))
    await asyncio.wait_for(entered.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert dispatcher._active == 0
    assert reply(await dispatcher.dispatch(wire(scope, "page", {"kind": "node"}),
                                           principal="reader"))["ok"] is True


def test_constructor_bounds():
    for kwargs in ({"authorize": None}, {"allow_model_calls": 1}, {"max_inflight": True},
                   {"max_inflight": 0}, {"max_inflight": 17},
                   {"read_timeout_seconds": True}, {"read_timeout_seconds": float("nan")},
                   {"read_timeout_seconds": 121}):
        with pytest.raises(ValueError):
            KnowledgeCommandDispatcher(Provider(), Coordinator(), kwargs.pop("authorize", Policy()), **kwargs)
