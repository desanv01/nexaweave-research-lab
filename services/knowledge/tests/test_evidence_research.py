"""Authored qualification source: real page seam, synthetic trusted stores/driver."""
import asyncio
import hashlib
import io
import importlib
import json
import threading
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

import nexaweave_knowledge.evidence_research as module
import nexaweave_knowledge.research_cli as cli
import nexaweave_knowledge.provider as provider_module
from nexaweave_knowledge.bindings import BindingRecord
from nexaweave_knowledge.contracts import FactResult, KnowledgeScope, Layer
from nexaweave_knowledge.operations import Busy, Tombstoned
from nexaweave_knowledge.provider import GraphitiKnowledgeProvider
from nexaweave_knowledge.research_contracts import ResearchRequest
from nexaweave_storage.source import ResolvedEvidence
from nexaweave_storage.store import NotFound, StorageError

NOW = datetime(2025, 1, 1, tzinfo=timezone.utc)


class Harness:
    def __init__(self):
        self.scope = KnowledgeScope(workspace_id=uuid4(), project_id=uuid4(), graph_id=uuid4(), layer=Layer.source)
        self.records = {"source": BindingRecord("owner", "source", self.scope, NOW)}
        self.rows = {self.scope.group_id: []}
        self.evidence = {}
        self.episode_times = {}
        self.missing_historical_episodes = set()
        self.events = []
        self.active = set()
        self.denied = {}
        self.closed = False
        self.opened = 0
        self.query_delay = 0
        self.query_failure = None
        self.owner = "owner"
        self.workspace = self.scope.workspace_id
        self.text = "A😀猫 bilingual " + "long retained text " * 1000
        self.revision = uuid4()
        self.service = module.EvidenceResearchService("owner", lambda: None, self.driver)
        self.service._bindings = SimpleNamespace(resolve=self.binding)
        self.service._ledger = SimpleNamespace(read_scope=self.guard)
        self.service._sources = SimpleNamespace(resolve_evidence=self.resolve)
        self.service._projects = SimpleNamespace(get=self.project)

    def binding(self, principal, display):
        self.events.append(("binding", display))
        record = self.records.get(display)
        if record is None or record.principal != principal:
            raise NotFound()
        return record

    def project(self, principal, project_id):
        self.events.append(("project", project_id))
        if self.owner != principal or project_id != self.scope.project_id:
            raise NotFound()
        return SimpleNamespace(principal=self.owner, project_id=project_id, workspace_id=self.workspace)

    @contextmanager
    def guard(self, scope):
        self.events.append(("guard", scope.group_id))
        if scope.group_id in self.denied:
            raise self.denied[scope.group_id]
        self.active.add(scope.group_id)
        try:
            yield
        finally:
            self.events.append(("release", scope.group_id))
            self.active.remove(scope.group_id)

    def resolve(self, principal, project, evidence):
        assert principal == "owner" and project == self.scope.project_id
        assert self.active == {record.scope.group_id for record in self.records.values()}
        self.events.append(("citation", evidence))
        item = self.evidence.get(evidence)
        if isinstance(item, Exception):
            raise item
        if item is None:
            raise NotFound()
        return item

    def passage(self, start=1, end=3, *, revision=None, recorded=NOW):
        evidence = uuid4()
        excerpt = self.text[start:end]
        self.evidence[evidence] = ResolvedEvidence("owner", self.scope.project_id,
            revision or self.revision, evidence, "retained", hashlib.sha256(self.text.encode()).hexdigest(),
            len(self.text.encode()), len(self.text), recorded, start, end, "unicode_codepoint",
            excerpt, hashlib.sha256(excerpt.encode()).hexdigest(), None)
        return evidence

    def layer(self, layer=Layer.simulation):
        scope = self.scope.model_copy(update={"layer": layer, "run_id": uuid4(), "branch_id": uuid4()})
        self.records["simulation"] = BindingRecord("owner", "simulation", scope, NOW)
        self.rows[scope.group_id] = []
        return scope

    def edge(self, *, scope=None, text="Alice works for 猫 company", evidence=(), number=None,
             created=NOW, valid=NOW, invalid=None, expired=None, subject=None, target=None, name="WORKS_FOR",
             episode_created=NOW):
        scope = scope or self.scope
        identifier = str(UUID(int=number)) if number is not None else str(uuid4())
        episode = str(uuid4())
        self.episode_times[episode] = episode_created
        self.rows[scope.group_id].append({
            "properties": {"uuid": identifier, "group_id": scope.group_id, "fact": text,
                           "name": name, "episodes": [episode], "created_at": created,
                           "valid_at": valid, "invalid_at": invalid, "expired_at": expired},
            "row_group": scope.group_id, "source_id": subject or str(uuid4()),
            "target_id": target or str(uuid4()), "source_group": scope.group_id,
            "target_group": scope.group_id, "fixture_evidence": tuple(map(str, evidence))})
        return identifier

    def driver(self):
        assert self.active == {record.scope.group_id for record in self.records.values()}
        self.opened += 1
        self.events.append(("driver", None))
        return self

    async def execute_query(self, query, *, parameters_, routing_):
        assert routing_ == "r"
        assert self.active == {record.scope.group_id for record in self.records.values()}
        self.events.append(("graph", query))
        assert not any(word in query for word in ("CREATE", "SET ", "DELETE", "embedding"))
        if self.query_delay:
            await asyncio.sleep(self.query_delay)
        if self.query_failure:
            raise self.query_failure
        rows = self.rows[parameters_["group_id"]]
        if "e.created_at AS created_at" in query:
            assert query == module._EPISODE_RECORDING_QUERY
            assert parameters_["limit"] == len(parameters_["ids"]) + 1 <= 101
            episodes = {episode for row in rows for episode in row["properties"]["episodes"]}
            rows = [{"uuid": episode, "row_group": parameters_["group_id"], "created_at": self.episode_times[episode]}
                    for episode in sorted(parameters_["ids"]) if episode in episodes
                    and episode not in self.missing_historical_episodes]
        elif "ORDER BY" in query:
            rows = sorted(rows, key=lambda row: row["properties"]["uuid"])
            rows = [row for row in rows if parameters_["cursor"] is None or row["properties"]["uuid"] > parameters_["cursor"]]
            rows = rows[:parameters_["limit"]]
        elif "MATCH (e:Episodic)" in query:
            episodes = {episode for row in rows for episode in row["properties"]["episodes"]}
            rows = [{"uuid": episode} for episode in parameters_["ids"] if episode in episodes]
        elif "MATCH (o:MiroFishIngest)" in query:
            rows = [{"uuid": episode, "evidence_ids": list(row["fixture_evidence"]), "asserted_valid_at": NOW}
                    for row in rows for episode in row["properties"]["episodes"] if episode in parameters_["ids"]]
        else:
            raise AssertionError("unexpected query")
        return rows, None, None

    async def close(self):
        assert self.active
        self.closed = True
        self.events.append(("close", None))


def request(*ids, **kwargs):
    return ResearchRequest(display_graph_ids=ids or ("source",), text="Alice 猫", **kwargs)


@pytest.mark.asyncio
async def test_complete_real_page_binding_guard_citation_ranking_and_coverage(monkeypatch):
    h = Harness()
    first = h.passage(1, 4)
    second = h.passage(3, 6)
    weak = h.edge(text="Unrelated report", number=1)
    strong = h.edge(evidence=(first, second), number=2)
    # Any accidental paid/provider initialize/search path is an immediate failure.
    async def forbidden(*args, **kwargs):
        raise AssertionError("model route forbidden")
    monkeypatch.setattr(GraphitiKnowledgeProvider, "initialize", forbidden)
    monkeypatch.setattr(GraphitiKnowledgeProvider, "search", forbidden)
    def forbidden_client(*args, **kwargs):
        raise AssertionError("paid client construction forbidden")
    monkeypatch.setattr(provider_module, "AsyncOpenAI", forbidden_client)
    result = await h.service.research(request(top_k=2))
    assert [fact.provider_id for fact in result.source_claims] == [strong, weak]
    assert result.rank_basis == "lexical_token_overlap"
    assert {citation.excerpt for citation in result.source_claims[0].citations} == {"😀猫 ", " bi"}
    assert result.linked_citations == result.resolved_citations == 2
    assert result.unavailable_citations == 0
    assert result.passage_coverage[0].retrieved_codepoints == 5
    assert result.passage_coverage[0].retrieved_passage_fraction == 5 / len(h.text)
    assert result.passage_coverage[0].retrieved_passage_fraction < 0.001
    assert result.scopes[0].scanned == result.scopes[0].returned == 2
    assert not result.scopes[0].truncated and h.closed and not h.active
    assert [event[0] for event in h.events].index("driver") > [event[0] for event in h.events].index("guard")
    assert [event[0] for event in h.events].index("close") < [event[0] for event in h.events].index("release")
    with pytest.raises(ValidationError):
        result.source_claims[0].citations[0].excerpt = "mutate"


@pytest.mark.asyncio
async def test_layers_run_branches_and_same_provider_identifier_are_preserved():
    h = Harness()
    simulation = h.layer()
    evidence = h.passage()
    same = h.edge(number=1, evidence=(evidence,))
    h.edge(scope=simulation, number=1, evidence=(evidence,), text="Alice simulation 猫")
    result = await h.service.research(request("source", "simulation", top_k=2))
    assert result.source_claims[0].provider_id == result.simulation_observations[0].provider_id == same
    assert result.simulation_observations[0].scope.run_id == simulation.run_id
    assert result.simulation_observations[0].scope.branch_id == simulation.branch_id
    assert len(result.scopes) == 2 and result.competing_claim_candidates == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("denial", ["foreign", "project", "workspace", "graph", "busy", "uncertain", "tombstoned", "owner", "project-workspace", "binding-change"])
async def test_all_authority_and_admission_denials_precede_graph(denial):
    h = Harness()
    simulation = h.layer()
    if denial == "foreign":
        h.records["simulation"] = replace(h.records["simulation"], principal="other")
    elif denial in {"project", "workspace", "graph"}:
        changed = simulation.model_copy(update={denial + "_id": uuid4()})
        h.records["simulation"] = replace(h.records["simulation"], scope=changed)
    elif denial in {"busy", "uncertain", "tombstoned"}:
        h.denied[simulation.group_id] = Tombstoned("secret") if denial == "tombstoned" else Busy("secret")
    elif denial == "owner":
        h.owner = "other"
    elif denial == "project-workspace":
        h.workspace = uuid4()
    else:
        initial = h.binding
        counter = 0
        def changed(principal, display):
            nonlocal counter
            counter += 1
            record = initial(principal, display)
            return replace(record, scope=record.scope.model_copy(update={"graph_id": uuid4()})) if counter > 2 else record
        h.service._bindings.resolve = changed
    with pytest.raises(module.ResearchFailure, match="^research_unavailable$"):
        await h.service.research(request("source", "simulation"))
    assert h.opened == 0 and not h.active and not h.closed


@pytest.mark.parametrize("updates,query,status", [
    ({"created_at": NOW}, {"recorded_before": NOW}, "eligible"),
    ({"created_at": NOW + timedelta(microseconds=1)}, {"recorded_before": NOW}, "excluded"),
    ({"created_at": None}, {"recorded_before": NOW}, "unknown"),
    ({"created_at": NOW.replace(tzinfo=None)}, {"recorded_before": NOW}, "unknown"),
    ({"valid_at": NOW}, {"valid_at": NOW}, "eligible"),
    ({"valid_at": NOW + timedelta(microseconds=1)}, {"valid_at": NOW}, "excluded"),
    ({"valid_at": None}, {"valid_at": NOW}, "unknown"),
    ({"invalid_at": NOW, "expired_at": NOW}, {"valid_at": NOW, "recorded_before": NOW}, "excluded"),
    ({"invalid_at": NOW + timedelta(microseconds=1), "expired_at": NOW}, {"valid_at": NOW, "recorded_before": NOW}, "eligible"),
    ({"invalid_at": NOW, "expired_at": None}, {"recorded_before": NOW}, "unknown"),
    ({"expired_at": NOW - timedelta(days=1)}, {}, "excluded"),
    ({"valid_at": NOW.astimezone(timezone(timedelta(hours=8)))}, {"valid_at": NOW}, "eligible"),
])
def test_temporal_half_open_boundaries_and_unknown(updates, query, status):
    fact = FactResult(provider_id=str(uuid4()), scope=Harness().scope, kind="edge", created_at=NOW, valid_at=NOW)
    fact = fact.model_copy(update=updates)
    assert module.temporal_edge(fact, request(**query), NOW)[0] == status


@pytest.mark.asyncio
async def test_future_invalidation_is_hidden_when_entire_provenance_is_known():
    h = Harness()
    evidence = h.passage()
    h.edge(evidence=(evidence,), invalid=NOW - timedelta(days=1), expired=NOW + timedelta(days=2))
    result = await h.service.research(request(valid_at=NOW, recorded_before=NOW))
    fact = result.source_claims[0]
    assert fact.invalid_at is None and fact.expired_at is None
    assert fact.citations[0].evidence_id == evidence and fact.unavailable_evidence_ids == ()
    assert result.resolved_citations == 1 and result.unavailable_citations == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("provenance,status", [
    ("future-episode", "excluded"), ("future-source", "excluded"),
    ("unknown-episode", "unknown"), ("naive-episode", "unknown"),
    ("missing-episode", "unknown"), ("missing-evidence", "unknown"),
    ("empty-evidence", "unknown"),
])
async def test_old_edge_unproven_history_never_leaks_text_ids_and_top_k_refills(provenance, status):
    h = Harness()
    evidence = h.passage(revision=uuid4(), recorded=NOW + timedelta(days=1) if provenance == "future-source" else NOW)
    if provenance == "missing-evidence":
        del h.evidence[evidence]
    bad = h.edge(number=1, text="Alice 猫 SECRET FUTURE DERIVED FACT", evidence=() if provenance == "empty-evidence" else (evidence,),
        created=NOW - timedelta(days=10), valid=NOW - timedelta(days=10),
        episode_created=(NOW + timedelta(days=1) if provenance == "future-episode" else
                         None if provenance == "unknown-episode" else
                         NOW.replace(tzinfo=None) if provenance == "naive-episode" else NOW))
    bad_episode = h.rows[h.scope.group_id][0]["properties"]["episodes"][0]
    if provenance == "missing-episode":
        # A record disappearing between page decoration and the exact recording
        # lookup is unknown, even if the earlier check had seen it.
        h.missing_historical_episodes.add(bad_episode)
    good_evidence = h.passage(revision=uuid4())
    good = h.edge(number=2, text="Alice retained claim", evidence=(good_evidence,))
    result = await h.service.research(request(recorded_before=NOW, valid_at=NOW, top_k=1))
    assert [fact.provider_id for fact in result.source_claims] == [good]
    scope, = result.scopes
    assert scope.scanned == 2 and scope.eligible == scope.returned == 1
    assert scope.excluded == (status == "excluded") and scope.unknown == (status == "unknown")
    payload = result.model_dump_json()
    assert all(secret not in payload for secret in (bad, bad_episode, str(evidence), "SECRET FUTURE DERIVED FACT"))
    assert result.linked_citations == result.resolved_citations == 1
    assert result.unavailable_citations == 0 and h.closed


@pytest.mark.asyncio
async def test_every_link_must_be_recorded_not_just_one_old_episode_or_source():
    h = Harness()
    old = h.passage(revision=uuid4())
    future_source = h.passage(revision=uuid4(), recorded=NOW + timedelta(days=1))
    first = h.edge(number=1, text="Alice 猫 mixed episode SECRET", evidence=(old,))
    future_episode = str(uuid4())
    h.episode_times[future_episode] = NOW + timedelta(days=1)
    h.rows[h.scope.group_id][0]["properties"]["episodes"].append(future_episode)
    second = h.edge(number=2, text="Alice 猫 mixed source SECRET", evidence=(old, future_source))
    good = h.edge(number=3, text="Alice retained claim", evidence=(old,))
    result = await h.service.research(request(recorded_before=NOW, valid_at=NOW, top_k=1))
    assert [fact.provider_id for fact in result.source_claims] == [good]
    scope, = result.scopes
    assert (scope.scanned, scope.eligible, scope.excluded, scope.unknown, scope.returned) == (3, 1, 2, 0, 1)
    payload = result.model_dump_json()
    assert all(value not in payload for value in (first, second, future_episode, str(future_source), "SECRET"))


@pytest.mark.asyncio
async def test_empty_episode_provenance_fails_at_accepted_page_seam_without_leak():
    h = Harness()
    h.edge(text="SECRET UNPROVEN FACT", evidence=(h.passage(),))
    h.rows[h.scope.group_id][0]["properties"]["episodes"] = []
    with pytest.raises(module.ResearchFailure, match="^research_unavailable$"):
        await h.service.research(request(recorded_before=NOW))
    assert h.closed and not h.active


@pytest.mark.asyncio
async def test_scanned_temporal_counts_and_bounded_page_truncation():
    h = Harness()
    evidence = h.passage()
    for i in range(1, 502):
        h.edge(number=i, text="Alice 猫", evidence=(evidence,), created=None if i == 1 else NOW + timedelta(days=1) if i == 2 else NOW)
    result = await h.service.research(request(recorded_before=NOW, top_k=3))
    item = result.scopes[0]
    assert (item.pages, item.scanned, item.eligible, item.excluded, item.unknown, item.returned) == (5, 500, 498, 1, 1, 3)
    assert item.truncated
    assert [fact.provider_id for fact in result.source_claims] == [str(UUID(int=i)) for i in (3, 4, 5)]


@pytest.mark.asyncio
async def test_source_recording_equal_cutoff_and_timezone_equivalence_is_resolved():
    h = Harness()
    evidence = h.passage(recorded=NOW.astimezone(timezone(timedelta(hours=8))))
    h.edge(evidence=(evidence,))
    result = await h.service.research(request(recorded_before=NOW, valid_at=NOW))
    assert result.resolved_citations == 1 and result.unavailable_citations == 0
    assert result.source_claims[0].citations[0].source_recorded_at == NOW


@pytest.mark.asyncio
async def test_missing_evidence_is_explicit_and_corruption_is_fixed_safe_error():
    h = Harness()
    missing = uuid4()
    h.edge(evidence=(missing,))
    result = await h.service.research(request())
    assert result.source_claims[0].unavailable_evidence_ids == (missing,)
    assert result.linked_citations == result.unavailable_citations == 1
    h.evidence[missing] = StorageError()
    with pytest.raises(module.ResearchFailure, match="^research_unavailable$"):
        await h.service.research(request())
    assert h.closed and not h.active


@pytest.mark.asyncio
@pytest.mark.parametrize("corruption", ["principal", "project_id", "evidence_id", "excerpt", "excerpt_sha256", "end", "source_recorded_at"])
async def test_corrupt_or_foreign_resolver_output_fails_without_excerpt(corruption):
    h = Harness()
    evidence = h.passage()
    changes = {"principal": "foreign", "project_id": uuid4(), "evidence_id": uuid4(),
               "excerpt": "password secret", "excerpt_sha256": "0" * 64, "end": 999999,
               "source_recorded_at": NOW.replace(tzinfo=None)}
    h.evidence[evidence] = replace(h.evidence[evidence], **{corruption: changes[corruption]})
    h.edge(evidence=(evidence,))
    with pytest.raises(module.ResearchFailure) as error:
        await h.service.research(request())
    assert str(error.value) == "research_unavailable" and h.closed


@pytest.mark.asyncio
async def test_revision_metadata_conflict_is_safe_failure():
    h = Harness()
    first, second = h.passage(1, 3), h.passage(2, 4)
    h.evidence[second] = replace(h.evidence[second], source_sha256="0" * 64)
    h.edge(evidence=(first, second))
    with pytest.raises(module.ResearchFailure, match="^research_unavailable$"):
        await h.service.research(request())


@pytest.mark.asyncio
async def test_competing_candidates_have_resolved_citations_without_truth_judgment():
    h = Harness()
    subject, evidence = str(uuid4()), h.passage()
    a = h.edge(subject=subject, text="Alice works for A", evidence=(evidence,), number=1)
    b = h.edge(subject=subject, text="Alice works for B", evidence=(evidence,), number=2)
    h.edge(subject=subject, text="Alice knows C", name="KNOWS", number=3)
    result = await h.service.research(request(top_k=3))
    candidate, = result.competing_claim_candidates
    assert candidate.provider_ids == (a, b) and candidate.evidence_ids == (evidence,)
    assert candidate.interpretation == "candidate_for_review_no_truth_judgment"
    assert "truth" not in candidate.model_fields and "contradiction" not in candidate.model_fields


@pytest.mark.parametrize("raw", [b"{", b"[]", b"null", b"\xff", b"x" * 16385,
    b'{"display_graph_ids":["source"],"text":"x","top_k":NaN}',
    b'{"display_graph_ids":["source"],"text":"x","text":"y"}',
    b'{"display_graph_ids":["source"],"text":"x","model":"paid"}',
    b'{"display_graph_ids":["source"],"text":"x","scope":{}}',
    b'{"display_graph_ids":["source"],"text":"x","top_k":true}',
    b'{"display_graph_ids":["source"],"text":"x","top_k":1.0}',
    b'{"schema_version":true,"display_graph_ids":["source"],"text":"x"}',
    b'{"display_graph_ids":["source"],"text":"x","recorded_before":"2025-01-01T00:00:00"}',
    b'{"display_graph_ids":["source","source"],"text":"x"}',
])
def test_cli_rejects_invalid_json_before_settings(raw):
    output, calls = io.StringIO(), []
    assert cli.main(stdin=io.BytesIO(raw), stdout=output, settings_factory=lambda: calls.append(1)) == 1
    assert json.loads(output.getvalue()) == {"error": "invalid_request"} and calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("update", [{"top_k": True}, {"text": "x" * 2001}, {"display_graph_ids": ("source",) * 6},
                                    {"recorded_before": NOW.replace(tzinfo=None)}, {"schema_version": True}])
async def test_model_copy_validation_bypass_is_rejected_before_stores(update):
    h = Harness()
    with pytest.raises(module.ResearchFailure, match="^invalid_request$"):
        await h.service.research(request().model_copy(update=update))
    assert h.events == [] and not h.opened


def test_cli_complete_service_result_and_safe_driver_error(monkeypatch):
    h = Harness()
    h.edge(evidence=(h.passage(),))
    settings = SimpleNamespace(principal="owner", connection=lambda: None, driver=h.driver, scope=h.scope)
    monkeypatch.setattr(cli, "EvidenceResearchService", lambda *args, **kwargs: h.service)
    raw = b'{"display_graph_ids":["source"],"text":"Alice"}'
    output = io.StringIO()
    assert cli.main(stdin=io.BytesIO(raw), stdout=output, settings_factory=lambda: settings) == 0
    assert len(json.loads(output.getvalue())["source_claims"]) == 1 and h.closed
    h.query_failure = RuntimeError("secret postgres://password source passage")
    output = io.StringIO()
    assert cli.main(stdin=io.BytesIO(raw), stdout=output, settings_factory=lambda: settings) == 1
    assert output.getvalue() == '{"error":"research_unavailable"}\n' and h.closed


def test_cli_configuration_failure_is_fixed(monkeypatch):
    output = io.StringIO()
    def bad():
        raise ValueError("password URI")
    assert cli.main(stdin=io.BytesIO(b'{"display_graph_ids":["source"],"text":"x"}'),
                    stdout=output, settings_factory=bad) == 1
    assert json.loads(output.getvalue()) == {"error": "invalid_configuration"}


def test_cli_import_does_not_probe_settings_or_construct_driver(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("eager settings/provider operation")
    monkeypatch.setattr(cli.ReadSettings, "from_environment", forbidden)
    monkeypatch.setattr(cli.ReadSettings, "driver", forbidden)
    importlib.reload(cli)


@pytest.mark.asyncio
async def test_trusted_cli_scope_anchor_denies_another_graph_before_driver():
    h = Harness()
    h.service._trusted_scope = h.scope.model_copy(update={"graph_id": uuid4()})
    with pytest.raises(module.ResearchFailure, match="^research_unavailable$"):
        await h.service.research(request())
    assert h.opened == 0 and not h.active


@pytest.mark.asyncio
async def test_duplicate_scope_alias_denied_before_driver():
    h = Harness()
    h.records["alias"] = BindingRecord("owner", "alias", h.scope, NOW)
    with pytest.raises(module.ResearchFailure, match="^research_unavailable$"):
        await h.service.research(request("source", "alias"))
    assert h.opened == 0


@pytest.mark.asyncio
async def test_deadline_closes_driver_and_host_event_loop_remains_responsive(monkeypatch):
    h = Harness()
    h.edge()
    h.query_delay = 0.5
    monkeypatch.setattr(module, "DEADLINE_SECONDS", 0.1)
    beat = asyncio.Event()
    async def heartbeat():
        await asyncio.sleep(0.01)
        beat.set()
    ticking = asyncio.create_task(heartbeat())
    with pytest.raises(module.ResearchFailure, match="^research_deadline$"):
        await h.service.research(request())
    await ticking
    for _ in range(20):
        if not h.service._inflight:
            break
        await asyncio.sleep(0.01)
    assert beat.is_set() and h.closed and not h.active


@pytest.mark.asyncio
async def test_sync_pg_is_off_host_loop_and_busy_prevents_extra_workers():
    h = Harness()
    h.edge()
    entered, release = threading.Event(), threading.Event()
    original = h.binding
    def blocking(principal, display):
        entered.set()
        assert release.wait(2)
        return original(principal, display)
    h.service._bindings.resolve = blocking
    running = asyncio.create_task(h.service.research(request()))
    try:
        for _ in range(100):
            if entered.is_set():
                break
            await asyncio.sleep(0.005)
        assert entered.is_set()
        with pytest.raises(module.ResearchFailure, match="^research_busy$"):
            await h.service.research(request())
    finally:
        release.set()
    await running
    assert h.opened == 1 and h.closed


@pytest.mark.asyncio
async def test_response_budget_failure_is_bounded_and_closes_driver(monkeypatch):
    h = Harness()
    h.edge(evidence=(h.passage(),))
    monkeypatch.setattr(module, "MAX_RESPONSE_BYTES", 100)
    with pytest.raises(module.ResearchFailure, match="^result_too_large$"):
        await h.service.research(request())
    assert h.closed and not h.active
