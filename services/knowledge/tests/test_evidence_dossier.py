"""Pure dossier assembly regressions. Authored by worker; execution belongs to Main."""
import asyncio
import hashlib
import io
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from nexaweave_knowledge.contracts import KnowledgeScope, Layer
from nexaweave_knowledge.dossier_cli import MAX_REQUEST_BYTES, main, parse_request
from nexaweave_knowledge.evidence_dossier import (
    DossierFailure, EvidenceDossierService, digest, plain_markdown)
from nexaweave_knowledge.evidence_research import ResearchFailure, claim_candidates, passage_coverage
from nexaweave_knowledge.report_contracts import DossierRequest, DossierSectionRequest, EvidenceDossier
from nexaweave_knowledge.research_contracts import Citation, ResearchFact, ResearchResult, ScopeCoverage


@pytest.fixture
def records():
    stamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    scope = KnowledgeScope(workspace_id=uuid4(), project_id=uuid4(), graph_id=uuid4(), layer=Layer.source)
    revision = uuid4()
    text = "A😀猫 evidence retained"
    def citation(start, end):
        excerpt = text[start:end]
        return Citation(evidence_id=uuid4(), project_id=scope.project_id, source_revision=revision,
            source_name="source", source_sha256=hashlib.sha256(text.encode()).hexdigest(),
            source_byte_length=len(text.encode()), source_codepoint_length=len(text), source_recorded_at=stamp,
            start=start, end=end, offset_unit="unicode_codepoint", excerpt=excerpt,
            excerpt_sha256=hashlib.sha256(excerpt.encode()).hexdigest())
    citations = (citation(1, 4), citation(3, 6))
    fact = ResearchFact(provider_id="edge_one", scope=scope, claim_class="source", name="WORKS_FOR",
        fact="Alice source claim", source_node_id="alice", target_node_id="company",
        episode_ids=("episode_one",), evidence_ids=tuple(c.evidence_id for c in citations),
        valid_at=stamp, created_at=stamp, overlap_tokens=1, query_tokens=1,
        citations=citations, unavailable_evidence_ids=())
    return scope, fact


def response(facts, *, historical=False, scopes=None):
    if scopes is None:
        scopes = tuple(dict.fromkeys(f.scope for f in facts))
    counts = tuple(ScopeCoverage(display_graph_id="display_" + str(i), scope=scope,
        pages=1, scanned=sum(f.scope == scope for f in facts), eligible=sum(f.scope == scope for f in facts),
        excluded=0, unknown=0, returned=sum(f.scope == scope for f in facts), truncated=False)
        for i, scope in enumerate(scopes))
    return ResearchResult(source_claims=tuple(f for f in facts if f.claim_class == "source"),
        simulation_observations=tuple(f for f in facts if f.claim_class == "simulation"),
        other_claims=tuple(f for f in facts if f.claim_class not in {"source", "simulation"}), scopes=counts,
        passage_coverage=passage_coverage(facts), competing_claim_candidates=claim_candidates(facts),
        linked_citations=sum(len(f.evidence_ids) for f in facts), resolved_citations=sum(len(f.citations) for f in facts),
        unavailable_citations=sum(len(f.unavailable_evidence_ids) for f in facts), historical=historical)


def request(count=2, *, ids=("display_0",), **updates):
    return DossierRequest(title="Retained evidence", display_graph_ids=ids,
        sections=tuple(DossierSectionRequest(heading=f"Section {i}", query=f"Query {i}", top_k=10)
                       for i in range(count)), **updates)


def service_with(*results):
    calls = []
    pending = iter(results)
    async def research(query):
        calls.append(query)
        item = next(pending)
        if isinstance(item, Exception):
            raise item
        return item
    service = EvidenceDossierService("owner", lambda: None, lambda: None)
    service._research = SimpleNamespace(research=research)
    return service, calls


@pytest.mark.parametrize("change", [
    {"schema_version": True}, {"schema_version": 2}, {"title": " "}, {"title": "x" * 257},
    {"sections": []}, {"sections": [{"heading": "h", "query": "q"}] * 7},
    {"display_graph_ids": ["display_0", "display_0"]}, {"display_graph_ids": []},
    {"principal": "foreign"}, {"scope": {}}, {"provider_url": "https://example.com"},
    {"export_path": "somewhere"}, {"model": "anything"}, {"claims": []},
    {"recorded_before": "2026-01-01T00:00:00"},
    {"sections": [{"heading": "h", "query": "q", "top_k": True}]},
    {"sections": [{"heading": "h", "query": "q", "top_k": 101}]},
    {"sections": [{"heading": "h", "query": " ", "sql": "SELECT 1"}]},
])
def test_strict_request_rejects_authority_and_invalid_bounds(change):
    data = request().model_dump(mode="json")
    data.update(change)
    with pytest.raises(DossierFailure, match="^invalid_request$"):
        parse_request(json.dumps(data).encode())


@pytest.mark.parametrize("raw", [
    b'{"title":"a","title":"b"}', b'{"sections":[{"query":"q","query":"q"}]}',
    b'{"top_k":NaN}', b'{"top_k":Infinity}', b'{}{}', b'\xff', b'[]',
    b' ' * (MAX_REQUEST_BYTES + 1), b'[' * 1500,
], ids=["duplicate_title", "duplicate_nested_query", "nan", "infinity",
        "multiple_documents", "invalid_utf8", "array_root", "request_over_byte_limit",
        "excessive_nesting"])
def test_json_duplicates_nonfinite_and_byte_limit(raw):
    with pytest.raises(DossierFailure, match="^invalid_request$"):
        parse_request(raw)


@pytest.mark.asyncio
async def test_union_deduplication_order_digests_and_frozen_records(records):
    scope, fact = records
    result = response((fact,))
    service, calls = service_with(result, result)
    dossier = await service.build(request())
    assert [call.text for call in calls] == ["Query 0", "Query 1"]
    assert [section.heading for section in dossier.sections] == ["Section 0", "Section 1"]
    assert dossier.sections[0].source_claim_keys == dossier.sections[1].source_claim_keys
    assert dossier.summary.distinct_scoped_facts == 1
    assert dossier.summary.reference_links == dossier.summary.resolved_references == 2
    assert dossier.summary.query_reference_links == dossier.summary.query_resolved_references == 4
    assert dossier.summary.scanned_per_query_sum == 2
    coverage, = dossier.summary.passage_coverage
    assert coverage.retrieved_codepoints == 5
    assert coverage.retrieved_passage_fraction == 5 / fact.citations[0].source_codepoint_length
    assert dossier.input_sha256 == digest(dossier.request.model_dump(mode="json"))
    assert dossier.trace_sha256 == digest([t.model_dump(mode="json") for t in dossier.research_trace])
    data = dict(sections=[s.model_dump(mode="json") for s in dossier.sections],
        claims=[c.model_dump(mode="json") for c in dossier.claims],
        references=[r.model_dump(mode="json") for r in dossier.references], summary=dossier.summary.model_dump(mode="json"))
    assert dossier.records_sha256 == digest(data)
    assert dossier.research_trace[0].response_sha256 == digest(result.model_dump(mode="json"))
    assert EvidenceDossier.model_validate_json(dossier.model_dump_json()) == dossier
    assert not dossier.model_generated and not dossier.semantic_judge_used
    assert dossier.claim_support_status == "not_reviewed"
    with pytest.raises(ValidationError):
        dossier.claims[0].text = "changed"
    repeat, _ = service_with(result, result)
    assert await repeat.build(request()) == dossier


@pytest.mark.asyncio
async def test_same_provider_id_different_run_branch_stays_distinct(records):
    source, fact = records
    simulation = source.model_copy(update={"layer": Layer.simulation, "run_id": uuid4(), "branch_id": uuid4()})
    other = fact.model_copy(update={"scope": simulation, "claim_class": "simulation"})
    service, _ = service_with(response((fact, other)))
    dossier = await service.build(request(1, ids=("display_0", "display_1")))
    assert len(dossier.claims) == 2 and len(dossier.references) == 4
    assert len({c.key for c in dossier.claims}) == 2
    assert len({r.key for r in dossier.references}) == 4
    assert {c.scope for c in dossier.claims} == {source, simulation}
    assert len(dossier.sections[0].simulation_observation_keys) == 1
    assert dossier.summary.passage_coverage[0].retrieved_codepoints == 5


@pytest.mark.asyncio
async def test_unavailable_and_no_links_are_explicit(records):
    scope, fact = records
    missing = uuid4()
    mixed = fact.model_copy(update={"evidence_ids": (*fact.evidence_ids, missing), "unavailable_evidence_ids": (missing,)})
    empty = fact.model_copy(update={"provider_id": "empty", "evidence_ids": (), "citations": ()})
    service, _ = service_with(response((mixed, empty)))
    dossier = await service.build(request(1))
    assert {c.reference_integrity for c in dossier.claims} == {"partly_unavailable", "no_evidence_links"}
    assert dossier.summary.unavailable_references == 1
    ref, = [r for r in dossier.references if r.status == "unavailable"]
    assert ref.evidence_id == missing and ref.citation is None
    assert "unavailable" in dossier.markdown


@pytest.mark.asyncio
async def test_candidate_is_retained_without_semantic_judgment(records):
    scope, fact = records
    competitor = fact.model_copy(update={"provider_id": "second", "target_node_id": "other", "fact": "Alice other claim"})
    service, _ = service_with(response((fact, competitor)))
    dossier = await service.build(request(1))
    candidate, = dossier.research_trace[0].competing_claim_candidates
    assert set(candidate.provider_ids) == {"edge_one", "second"}
    assert candidate.interpretation == "candidate_for_review_no_truth_judgment"
    assert "Candidate for review; no truth judgment." in dossier.markdown
    assert all(c.claim_support_status == "not_reviewed" for c in dossier.claims)


@pytest.mark.asyncio
async def test_html_links_controls_and_exact_excerpt(records):
    scope, fact = records
    malicious = '<script>alert(1)</script> [x](javascript:alert(2)) https://example.com\n# heading\x00\u202e'
    citation = fact.citations[0].model_copy(update={"start": 0, "end": len(malicious),
        "source_codepoint_length": len(malicious), "source_byte_length": len(malicious.encode()),
        "source_sha256": hashlib.sha256(malicious.encode()).hexdigest(), "source_name": malicious[:256],
        "excerpt": malicious, "excerpt_sha256": hashlib.sha256(malicious.encode()).hexdigest()})
    changed = fact.model_copy(update={"fact": malicious, "citations": (citation,), "evidence_ids": (citation.evidence_id,)})
    service, _ = service_with(response((changed,)))
    req = request(1).model_copy(update={"title": malicious, "sections": (DossierSectionRequest(heading=malicious, query="q"),)})
    dossier = await service.build(req)
    assert dossier.references[0].citation.excerpt == malicious
    assert dossier.claims[0].text == malicious
    for token in ("<script>", "](javascript:", "https://", "\x00", "\u202e", "\n# heading"):
        assert token not in dossier.markdown
    assert "&#60;script&#62;" in dossier.markdown
    assert "U+0000" in dossier.markdown and "U+202E" in dossier.markdown
    assert plain_markdown("&lt;script&gt;") == "&#38;lt&#59;script&#38;gt&#59;"


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["claim", "citation", "revision", "binding", "foreign", "missing", "counts", "candidate"])
async def test_conflicts_and_invalid_result_fail_closed(records, mutation):
    scope, fact = records
    first = response((fact,))
    changed = fact
    if mutation == "claim":
        changed = fact.model_copy(update={"fact": "changed retained text"})
    elif mutation == "citation":
        citation = fact.citations[0].model_copy(update={"source_name": "changed source"})
        changed = fact.model_copy(update={"citations": (citation, fact.citations[1])})
    elif mutation == "revision":
        citation = fact.citations[1].model_copy(update={"source_sha256": "b" * 64})
        changed = fact.model_copy(update={"citations": (fact.citations[0], citation)})
    elif mutation in {"binding", "foreign"}:
        other = scope.model_copy(update={"project_id": uuid4()})
        changed = fact.model_copy(update={"scope": other})
    elif mutation == "missing":
        changed = fact.model_copy(update={"evidence_ids": (*fact.evidence_ids, uuid4())})
    second = response((changed,)) if mutation != "revision" else first.model_copy(update={"source_claims": (changed,)})
    if mutation == "foreign":
        second = second.model_copy(update={"scopes": first.scopes})
    elif mutation == "counts":
        second = second.model_copy(update={"resolved_citations": 0})
    elif mutation == "candidate":
        other = fact.model_copy(update={"provider_id": "other", "fact": "different"})
        candidates = claim_candidates((fact, other))
        second = second.model_copy(update={"competing_claim_candidates": candidates})
    service, _ = service_with(first, second)
    with pytest.raises(DossierFailure, match="^dossier_unavailable$"):
        await service.build(request())


@pytest.mark.asyncio
async def test_failed_section_does_not_return_partial_and_service_reusable(records):
    scope, fact = records
    service, calls = service_with(response((fact,)), ResearchFailure(), response((fact,)))
    with pytest.raises(DossierFailure, match="^dossier_unavailable$"):
        await service.build(request())
    assert len(calls) == 2
    complete = await service.build(request(1))
    assert complete.summary.query_count == 1


@pytest.mark.asyncio
async def test_overall_deadline_and_busy_without_new_background_work(records, monkeypatch):
    import nexaweave_knowledge.evidence_dossier as module
    scope, fact = records
    service, calls = service_with(response((fact,)))
    entered = asyncio.Event()
    async def blocked(query):
        entered.set()
        await asyncio.Event().wait()
    service._research.research = blocked
    monkeypatch.setattr(module, "DEADLINE_SECONDS", 0.05)
    task = asyncio.create_task(service.build(request(1)))
    await entered.wait()
    with pytest.raises(DossierFailure, match="^dossier_busy$"):
        await service.build(request(1))
    with pytest.raises(DossierFailure, match="^dossier_deadline$"):
        await task
    assert not service._inflight


@pytest.mark.asyncio
async def test_no_silent_clipping_when_final_markdown_exceeds_limit(records, monkeypatch):
    import nexaweave_knowledge.evidence_dossier as module
    scope, fact = records
    # Raw research fits, but records plus trace plus escaped Markdown do not.
    result = response((fact,))
    monkeypatch.setattr(module, "MAX_RESPONSE_BYTES", len(result.model_dump_json().encode()) + 50)
    service, calls = service_with(result)
    with pytest.raises(DossierFailure, match="^result_too_large$"):
        await service.build(request(1))
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_aware_cutoffs_forwarded_identically_to_all_queries(records):
    scope, fact = records
    service, calls = service_with(response((fact,), historical=True), response((fact,), historical=True))
    stamp = fact.created_at
    dossier = await service.build(request(valid_at=stamp, recorded_before=stamp))
    assert all(c.valid_at == c.recorded_before == stamp for c in calls)
    assert all(t.historical and t.valid_at == t.recorded_before == stamp for t in dossier.research_trace)


@pytest.mark.asyncio
async def test_empty_queries_and_unknown_truncated_scan_counts_are_honest(records):
    scope, fact = records
    populated = response((fact,))
    counts = populated.scopes[0].model_copy(update={"pages": 5, "scanned": 9,
        "eligible": 2, "excluded": 3, "unknown": 4, "truncated": True})
    populated = populated.model_copy(update={"scopes": (counts,)})
    empty = response((), scopes=(scope,))
    service, _ = service_with(populated, empty)
    dossier = await service.build(request())
    assert dossier.summary.scanned_per_query_sum == 9
    assert dossier.summary.unknown_per_query_sum == 4
    assert dossier.summary.truncated_query_scopes == 1
    assert dossier.research_trace[0].scopes[0] == counts
    assert dossier.sections[1].source_claim_keys == ()
    assert "unknown 4" in dossier.markdown and "truncated true" in dossier.markdown
    assert "No retained facts returned by this query." in dossier.markdown
    assert dossier.summary.distinct_scoped_facts == 1


@pytest.mark.asyncio
async def test_cumulative_research_budget_stops_before_next_query(records, monkeypatch):
    import nexaweave_knowledge.evidence_dossier as module
    scope, fact = records
    result = response((fact,))
    raw_size = len(result.model_dump_json().encode())
    monkeypatch.setattr(module, "MAX_RESPONSE_BYTES", raw_size + 50)
    service, calls = service_with(result, result, result)
    with pytest.raises(DossierFailure, match="^result_too_large$"):
        await service.build(request(3))
    assert len(calls) == 2


def test_cli_invalid_input_precedes_config_and_scrubs_errors():
    def forbidden():
        raise AssertionError("configuration must not be read")
    output = io.StringIO()
    assert main(stdin=io.BytesIO(b'{}'), stdout=output, settings_factory=forbidden) == 1
    assert json.loads(output.getvalue()) == {"error": "invalid_request"}
    def bad_settings():
        raise RuntimeError("secret DSN path provider key")
    output = io.StringIO()
    assert main(stdin=io.BytesIO(request().model_dump_json().encode()), stdout=output, settings_factory=bad_settings) == 1
    assert output.getvalue() == '{"error":"invalid_configuration"}\n'
