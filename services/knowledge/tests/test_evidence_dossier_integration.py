"""Actual PostgreSQL/Neo4j and fresh local CLI qualification source for Main."""
import asyncio
import json
import os
from datetime import timedelta
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest
from psycopg.conninfo import conninfo_to_dict

from mirofish_knowledge.evidence_dossier import EvidenceDossierService, DossierFailure, digest, MAX_RESPONSE_BYTES
from mirofish_knowledge.operations import Ledger
from mirofish_knowledge.report_contracts import DossierRequest, DossierSectionRequest, EvidenceDossier
# Reuse accepted disposable-store guards, retained source fixture, and passive
# child observations. Only the real module entry point changes in this source.
from test_evidence_research_integration import retained_graph, factory, _CLI_CHILD

pytestmark = [pytest.mark.postgres, pytest.mark.neo4j]
_DOSSIER_CHILD = _CLI_CHILD.replace("mirofish_knowledge.research_cli", "mirofish_knowledge.dossier_cli")


def _request(f, **updates):
    return DossierRequest(title="Connected retained evidence", display_graph_ids=f["ids"],
        sections=(DossierSectionRequest(heading="First source and simulation", query="Alice 猫", top_k=10),
                  DossierSectionRequest(heading="Second ordered query", query="WORKS_FOR", top_k=10)),
        **updates)


def _assert_connected(dossier, f):
    assert not dossier.model_generated and not dossier.semantic_judge_used
    assert dossier.claim_support_status == "not_reviewed"
    assert dossier.consistency == "individually_guarded_queries_not_atomic_snapshot"
    assert dossier.summary.section_count == dossier.summary.query_count == 2
    assert dossier.summary.distinct_scoped_facts == 2
    assert len(dossier.claims) == 2 and len(dossier.references) == 4
    assert dossier.summary.reference_links == dossier.summary.resolved_references == 4
    assert dossier.summary.unavailable_references == 0
    assert dossier.summary.query_reference_links == dossier.summary.query_resolved_references == 8
    assert dossier.summary.query_unavailable_references == 0
    assert dossier.summary.scanned_per_query_sum == 4
    assert dossier.summary.unknown_per_query_sum == dossier.summary.truncated_query_scopes == 0
    assert [section.heading for section in dossier.sections] == ["First source and simulation", "Second ordered query"]
    for section in dossier.sections:
        assert len(section.source_claim_keys) == len(section.simulation_observation_keys) == 1
        assert section.other_claim_keys == ()
    assert dossier.sections[0].source_claim_keys == dossier.sections[1].source_claim_keys
    assert dossier.sections[0].simulation_observation_keys == dossier.sections[1].simulation_observation_keys
    for claim in dossier.claims:
        assert claim.scope in (f["source"], f["simulation"])
        assert claim.claim_class == claim.scope.layer.value
        index = 0 if claim.scope == f["source"] else 1
        assert claim.provider_id == f["edges"][index]
        assert claim.episode_ids == (f["episodes"][index],)
        assert claim.reference_integrity == "resolved" and claim.claim_support_status == "not_reviewed"
        assert set(claim.evidence_ids) == {p.evidence_id for p in f["retained"].passages}
        linked = [ref for ref in dossier.references if ref.claim_key == claim.key]
        assert {ref.key for ref in linked} == set(claim.reference_keys)
        assert {ref.citation.excerpt for ref in linked} == {"😀猫 ", " ev"}
        for ref in linked:
            assert ref.scope == claim.scope and ref.provider_id == claim.provider_id
            assert ref.status == "resolved" and ref.citation.project_id == claim.scope.project_id
            citation = ref.citation
            assert citation.source_revision == f["retained"].source_revision
            assert citation.source_sha256 == f["retained"].text_sha256
            assert citation.source_recorded_at == f["retained"].recorded_at
            assert citation.source_codepoint_length == f["retained"].codepoint_length
            assert citation.source_byte_length == f["retained"].byte_length
            passage = next(p for p in f["retained"].passages if p.evidence_id == ref.evidence_id)
            assert (citation.start, citation.end, citation.excerpt_sha256) == (passage.start, passage.end, passage.excerpt_sha256)
            assert citation.offset_unit == "unicode_codepoint"
    coverage, = dossier.summary.passage_coverage
    assert coverage.project_id == f["source"].project_id
    assert coverage.source_revision == f["retained"].source_revision
    assert coverage.source_sha256 == f["retained"].text_sha256
    assert coverage.retrieved_codepoints == 5
    assert coverage.retrieved_passage_fraction == 5 / f["retained"].codepoint_length
    assert [trace.query for trace in dossier.research_trace] == ["Alice 猫", "WORKS_FOR"]
    for trace in dossier.research_trace:
        assert trace.display_graph_ids == f["ids"]
        assert trace.linked_citations == trace.resolved_citations == 4
        assert trace.unavailable_citations == 0
        assert {s.scope for s in trace.scopes} == {f["source"], f["simulation"]}
        assert all(s.scanned == s.eligible == s.returned == 1 and s.unknown == s.excluded == 0
                   and not s.truncated for s in trace.scopes)
        assert trace.historical_semantics == "retained_edges_not_bitemporal_reconstruction"
    assert dossier.input_sha256 == digest(dossier.request.model_dump(mode="json"))
    assert dossier.trace_sha256 == digest([t.model_dump(mode="json") for t in dossier.research_trace])
    assert "Source claims" in dossier.markdown and "Simulation observations" in dossier.markdown
    assert "not real-world predictions" in dossier.markdown


@pytest.mark.asyncio
async def test_real_multiple_ordered_queries_scoped_citations_and_union(retained_graph):
    f = retained_graph
    service = EvidenceDossierService("owner", f["factory"], f["new_driver"], trusted_scope=f["source"])
    dossier = await service.build(_request(f))
    _assert_connected(dossier, f)
    # Repeated unchanged retained records give stable keys and full export.
    assert await service.build(_request(f)) == dossier


@pytest.mark.asyncio
async def test_real_historical_cutoffs_and_future_provenance_not_published(retained_graph):
    f = retained_graph
    service = EvidenceDossierService("owner", f["factory"], f["new_driver"], trusted_scope=f["source"])
    req = _request(f, recorded_before=f["stamp"], valid_at=f["stamp"])
    dossier = await service.build(req)
    _assert_connected(dossier, f)
    assert all(t.historical and t.valid_at == t.recorded_before == f["stamp"] for t in dossier.research_trace)
    future = f["stamp"] + timedelta(days=1)
    await f["writer"].execute_query(
        "MATCH (e:Episodic {uuid:$episode,group_id:$group_id}) SET e.created_at=datetime($stamp)",
        parameters_={"episode": f["episodes"][0], "group_id": f["source"].group_id, "stamp": future.isoformat()})
    filtered = await service.build(req)
    assert len(filtered.claims) == 1 and filtered.claims[0].scope == f["simulation"]
    assert filtered.summary.distinct_scoped_facts == 1
    assert filtered.summary.reference_links == 2 and filtered.summary.query_reference_links == 4
    assert all(next(s for s in t.scopes if s.scope == f["source"]).excluded == 1 for t in filtered.research_trace)
    payload = filtered.model_dump_json()
    assert f["edges"][0] not in payload and f["episodes"][0] not in payload and "猫 source" not in payload
    past = f["stamp"] - timedelta(days=1)
    empty = await service.build(_request(f, recorded_before=past, valid_at=f["stamp"]))
    assert empty.claims == empty.references == ()
    assert empty.summary.passage_coverage == ()
    assert empty.summary.distinct_scoped_facts == empty.summary.reference_links == 0
    assert all(s.excluded == 1 and s.returned == 0 for t in empty.research_trace for s in t.scopes)
    assert all(edge not in empty.model_dump_json() for edge in f["edges"])
    assert "No retained facts returned" in empty.markdown


@pytest.mark.asyncio
async def test_real_owner_exact_graph_active_uncertain_tombstone_denials(retained_graph):
    f = retained_graph
    calls = []
    def forbidden_driver():
        calls.append(1)
        raise AssertionError("driver must not be constructed after failed admission")
    req = _request(f)
    foreign = EvidenceDossierService("foreign", f["factory"], forbidden_driver, trusted_scope=f["source"])
    with pytest.raises(DossierFailure, match="^dossier_unavailable$"):
        await foreign.build(req)
    wrong_scope = f["source"].model_copy(update={"graph_id": uuid4()})
    wrong_graph = EvidenceDossierService("owner", f["factory"], forbidden_driver, trusted_scope=wrong_scope)
    with pytest.raises(DossierFailure, match="^dossier_unavailable$"):
        await wrong_graph.build(req)
    ledger, operation = Ledger(f["factory"]), uuid4()
    ledger.admit(f["simulation"], operation, "a" * 64)
    claim = ledger.claim(f["simulation"], operation)
    denied = EvidenceDossierService("owner", f["factory"], forbidden_driver, trusted_scope=f["source"])
    with pytest.raises(DossierFailure, match="^dossier_unavailable$"):
        await denied.build(req)
    ledger.mark_uncertain(f["simulation"], operation, claim.attempt_id, "fixture_uncertain")
    with pytest.raises(DossierFailure, match="^dossier_unavailable$"):
        await denied.build(req)
    ledger.tombstone_scope(f["simulation"])
    with pytest.raises(DossierFailure, match="^dossier_unavailable$"):
        await denied.build(req)
    assert calls == []


@pytest.mark.asyncio
async def test_real_fresh_cli_json_success_history_denied_and_resource_close(retained_graph, tmp_path):
    from tools.run_unit_tests import _unit_environment
    f = retained_graph
    root = Path(__file__).resolve().parents[3]
    pg = conninfo_to_dict(os.environ["PROJECT_STORE_POSTGRES_TEST_DSN"])
    env = _unit_environment(tmp_path)
    env["PYTHONPATH"] = os.pathsep.join((str(root), str(root / "services" / "knowledge" / "src")))
    for key in ("LLM_API_KEY", "OPENAI_API_KEY", "ZEP_API_KEY", "DEEPSEEK_API_KEY", "LLM_BASE_URL"):
        env.pop(key, None)
    env.update(GRAPHITI_TELEMETRY_ENABLED="false", KNOWLEDGE_PRINCIPAL="owner",
        KNOWLEDGE_DISPLAY_GRAPH_ID=f["ids"][0], KNOWLEDGE_BOUND_SCOPE_JSON=f["source"].model_dump_json(),
        KNOWLEDGE_PG_HOST=pg["host"], KNOWLEDGE_PG_PORT=pg["port"], KNOWLEDGE_PG_DATABASE=pg["dbname"],
        KNOWLEDGE_PG_USER=pg["user"], KNOWLEDGE_PG_PASSWORD=pg["password"],
        KNOWLEDGE_NEO4J_URI="bolt://127.0.0.1:17687", KNOWLEDGE_NEO4J_USER="neo4j",
        KNOWLEDGE_NEO4J_PASSWORD=os.environ["KNOWLEDGE_TEST_PASSWORD"])

    async def invoke(req, expected_status):
        raw = json.dumps(req, ensure_ascii=False, allow_nan=False).encode("utf-8")
        try:
            completed = await asyncio.to_thread(subprocess.run, [sys.executable, "-c", _DOSSIER_CHILD],
                input=raw, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=tmp_path, env=env,
                timeout=90, check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired:
            pytest.fail("dossier_cli_child_timeout", pytrace=False)
        except OSError:
            pytest.fail("dossier_cli_child_os_error", pytrace=False)
        if completed.returncode != expected_status or completed.stderr:
            category = {91: "guard_violation", 92: "model_or_search_observed", 93: "driver_close_missing",
                        94: "pg_close_missing", 95: "driver_and_pg_close_missing", 96: "cli_exit_missing"}.get(
                            completed.returncode, "exit_status")
            stderr_category = "nonempty" if completed.stderr else "empty"
            pytest.fail(f"dossier_cli_child_{category}: expected_exit={expected_status} "
                        f"actual_exit={completed.returncode} stderr={stderr_category}", pytrace=False)
        if not 0 < len(completed.stdout) <= MAX_RESPONSE_BYTES or completed.stdout.count(b"\n") != 1:
            pytest.fail("dossier_cli_output_invalid", pytrace=False)
        try:
            return json.loads(completed.stdout.decode("utf-8"))
        except (ValueError, UnicodeError):
            pytest.fail("dossier_cli_output_invalid", pytrace=False)

    request = _request(f, recorded_before=f["stamp"], valid_at=f["stamp"])
    data = await invoke(request.model_dump(mode="json"), 0)
    try:
        dossier = EvidenceDossier.model_validate_json(json.dumps(data, ensure_ascii=False))
    except ValueError:
        pytest.fail("dossier_cli_contract_invalid", pytrace=False)
    _assert_connected(dossier, f)
    assert all(t.historical for t in dossier.research_trace)
    denied = request.model_dump(mode="json")
    denied["display_graph_ids"] = ["denied_" + uuid4().hex]
    assert await invoke(denied, 1) == {"error": "dossier_unavailable"}
    # Real early cutoff returns no current snippets or retained edge IDs.
    early = request.model_dump(mode="json")
    early["recorded_before"] = (f["stamp"] - timedelta(days=1)).isoformat()
    history = await invoke(early, 0)
    assert history["claims"] == history["references"] == []
    assert history["summary"]["reference_links"] == 0
    assert all(edge not in json.dumps(history) for edge in f["edges"])
    # Child profiler observes close calls; successful local guard admission is
    # additional smoke coverage, not proof of exclusive lock-release semantics.
    ledger = Ledger(f["factory"])
    with ledger.read_scope(f["source"]), ledger.read_scope(f["simulation"]):
        pass
