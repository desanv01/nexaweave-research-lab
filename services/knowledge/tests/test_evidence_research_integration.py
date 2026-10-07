"""Guarded disposable Neo4j + PostgreSQL source for Main qualification only."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from neo4j import AsyncGraphDatabase
from psycopg.conninfo import conninfo_to_dict

from nexaweave_knowledge.bindings import ScopeBindingStore
from nexaweave_knowledge.contracts import KnowledgeScope, Layer
from nexaweave_knowledge.evidence_research import EvidenceResearchService, ResearchFailure
from nexaweave_knowledge.operations import Ledger
from nexaweave_knowledge.research_contracts import ResearchRequest
from nexaweave_storage import ProjectStore, SourceStore
from test_project_store import snapshot
# Reuse the existing approved loopback DSN guard and explicit migrations.
from test_source_bridge_postgres import factory

pytestmark = [pytest.mark.postgres, pytest.mark.neo4j]


# The child installs the accepted socket guard before application imports. A
# passive profiler observes calls/closure without replacing production objects.
_CLI_CHILD = """
import logging
import runpy
import sys
import threading
from tools.run_unit_tests import LoopbackOnlySockets
guard = LoopbackOnlySockets()
guard.install()
observed = set()
class MinimalFixtureNotification(logging.Filter):
    def filter(self, record):
        # Match the installed driver's typed notification argument, never text
        # substrings. Retain every other record, including ERROR/CRITICAL logs.
        if (record.name != 'neo4j.notifications'
                or record.levelno != logging.WARNING
                or record.msg != 'Received notification from DBMS server: %s'
                or type(record.args) is not tuple or len(record.args) != 1):
            return True
        printer = record.args[0]
        if (type(printer).__module__ != 'neo4j._debug._notification_printer'
                or type(printer).__name__ != 'NotificationPrinter'):
            return True
        try:
            notification = printer.notification
            known = (notification.is_notification
                     and notification.gql_status == '01N52'
                     and notification.raw_classification == 'UNRECOGNIZED'
                     and notification.classification.value == 'UNRECOGNIZED')
        except (AttributeError, TypeError, ValueError):
            return True
        if known:
            observed.add('allowed_fixture_notification_01N52')
            return False
        return True
notification_logger = logging.getLogger('neo4j.notifications')
notification_filter = MinimalFixtureNotification()
notification_logger.addFilter(notification_filter)
def observe(frame, event, arg):
    if event != 'call':
        return
    instance = frame.f_locals.get('self')
    module = type(instance).__module__
    name = frame.f_code.co_name
    defining_module = frame.f_globals.get('__name__', '')
    lineage = {(base.__module__, base.__name__) for base in type(instance).__mro__}
    # Graphiti's imported recipe/config DTOs may construct normally. Observe
    # the actual application class, including inherited/subclass methods.
    is_graphiti = ('graphiti_core.graphiti', 'Graphiti') in lineage
    if is_graphiti and name in {
        '__init__', 'search', 'search_', '_search', 'add_episode',
        'add_episode_bulk', 'add_triplet', 'build_communities',
    }:
        observed.add('model_or_search')
    client_namespaces = (
        'graphiti_core.llm_client', 'graphiti_core.embedder',
        'graphiti_core.cross_encoder', 'openai',
    )
    is_client = any(
        base_module.startswith(client_namespaces)
        and (base_name.endswith(('Client', 'Embedder'))
             or base_name in {'OpenAI', 'AsyncOpenAI', 'AzureOpenAI', 'AsyncAzureOpenAI'})
        for base_module, base_name in lineage
    )
    if is_client and name == '__init__':
        observed.add('model_or_search')
    if defining_module.startswith(client_namespaces) and name in {
        'generate_response', '_generate_response', 'create', 'embed',
        'embed_batch', 'rank', 'rerank', 'predict', 'request', '_request',
        'send', '_send',
    }:
        observed.add('model_or_search')
    if name == 'close' and defining_module == 'neo4j._async.driver':
        observed.add('driver_close')
    if name == 'close' and defining_module == 'psycopg.connection':
        observed.add('pg_close')
sys.setprofile(observe)
threading.setprofile(observe)
status = 96  # CLI did not terminate through its expected SystemExit path.
try:
    sys.argv = ['nexaweave_knowledge.research_cli']
    try:
        runpy.run_module('nexaweave_knowledge.research_cli', run_name='__main__')
    except SystemExit as exit:
        status = exit.code
finally:
    sys.setprofile(None)
    threading.setprofile(None)
    notification_logger.removeFilter(notification_filter)
    guard.restore()
if guard.blocked_attempts:
    status = 91
elif 'model_or_search' in observed:
    status = 92
elif status == 0:
    missing = {'driver_close', 'pg_close'} - observed
    if missing == {'driver_close', 'pg_close'}:
        status = 95
    elif missing == {'driver_close'}:
        status = 93
    elif missing == {'pg_close'}:
        status = 94
raise SystemExit(status)
"""


@pytest.fixture
async def retained_graph(factory):
    if os.getenv("KNOWLEDGE_INTEGRATION") != "1":
        pytest.skip("disposable Neo4j integration disabled")
    password = os.getenv("KNOWLEDGE_TEST_PASSWORD")
    if not password or password.lower() in {"neo4j", "password", "changeme", "test"}:
        pytest.fail("approved disposable Neo4j password required")
    workspace, project, graph, revision = uuid4(), uuid4(), uuid4(), uuid4()
    ProjectStore(factory).create("owner", workspace, project, "proj_1", snapshot())
    text = "A😀猫 evidence " + "retained long document " * 1000
    first, second = uuid4(), uuid4()
    retained = SourceStore(factory).ingest_text("owner", project, revision, "research passage", text,
        [{"evidence_id": str(first), "start": 1, "end": 4},
         {"evidence_id": str(second), "start": 3, "end": 6}])
    source = KnowledgeScope(workspace_id=workspace, project_id=project, graph_id=graph, layer=Layer.source)
    simulation = source.model_copy(update={"layer": Layer.simulation, "run_id": uuid4(), "branch_id": uuid4()})
    source_display, simulation_display = "source_" + graph.hex, "simulation_" + graph.hex
    bindings = ScopeBindingStore(factory)
    bindings.bind("owner", source_display, source)
    bindings.bind("owner", simulation_display, simulation)

    def new_driver():
        return AsyncGraphDatabase.driver("bolt://127.0.0.1:17687", auth=("neo4j", password),
                                         connection_timeout=3, connection_acquisition_timeout=3)

    writer = new_driver()
    edge_ids, episode_ids = [], []
    stamp = datetime.now(timezone.utc)
    try:
        for scope, fact_text in ((source, "Alice works for 猫 source"), (simulation, "Alice works for 猫 simulation")):
            edge, episode, subject, target = map(str, (uuid4(), uuid4(), uuid4(), uuid4()))
            edge_ids.append(edge)
            episode_ids.append(episode)
            await writer.execute_query(
                "CREATE (a:Entity {uuid:$subject,group_id:$group_id,name:'Alice'}), "
                "(b:Entity {uuid:$target,group_id:$group_id,name:'猫'}), "
                "(e:Episodic {uuid:$episode,group_id:$group_id,created_at:datetime($stamp)}), "
                "(o:MiroFishIngest {uuid:$episode,group_id:$group_id,status:'complete',evidence_ids:$evidence}) "
                "CREATE (a)-[:RELATES_TO {uuid:$edge,group_id:$group_id,name:'WORKS_FOR', "
                "fact:$fact,episodes:[$episode],created_at:datetime($stamp),valid_at:datetime($stamp)}]->(b)",
                parameters_={"subject": subject, "target": target, "group_id": scope.group_id,
                    "episode": episode, "edge": edge, "fact": fact_text,
                    "evidence": [str(first), str(second)], "stamp": stamp.isoformat()})
        yield dict(factory=factory, source=source, simulation=simulation, retained=retained,
                   ids=(source_display, simulation_display), new_driver=new_driver,
                   writer=writer, edges=edge_ids, episodes=episode_ids, stamp=stamp)
    finally:
        try:
            await writer.execute_query("MATCH (n) WHERE n.group_id IN $groups DETACH DELETE n",
                parameters_={"groups": [source.group_id, simulation.group_id]})
        finally:
            await writer.close()


@pytest.mark.asyncio
async def test_real_connected_layers_exact_retained_passages_and_historical_cutoff(retained_graph):
    f = retained_graph
    service = EvidenceResearchService("owner", f["factory"], f["new_driver"])
    request = ResearchRequest(display_graph_ids=f["ids"], text="Alice 猫", top_k=10)
    result = await service.research(request)
    assert len(result.source_claims) == len(result.simulation_observations) == 1
    assert result.simulation_observations[0].scope == f["simulation"]
    assert result.source_claims[0].scope == f["source"]
    for claim in (*result.source_claims, *result.simulation_observations):
        assert {citation.excerpt for citation in claim.citations} == {"😀猫 ", " ev"}
        assert all(citation.source_revision == f["retained"].source_revision for citation in claim.citations)
        assert all(citation.source_sha256 == f["retained"].text_sha256 for citation in claim.citations)
    assert result.linked_citations == result.resolved_citations == 4
    coverage, = result.passage_coverage
    assert coverage.retrieved_codepoints == 5
    assert coverage.retrieved_passage_fraction == 5 / f["retained"].codepoint_length
    assert len(result.scopes) == 2 and all(item.scanned == 1 and not item.truncated for item in result.scopes)

    equal = await service.research(request.model_copy(update={"recorded_before": f["stamp"], "valid_at": f["stamp"]}))
    assert equal.resolved_citations == 4 and all(item.eligible == 1 for item in equal.scopes)

    # An old retained edge can acquire a future episode. Neither fact text nor
    # episode/evidence IDs may survive that historical provenance exclusion.
    future = f["stamp"] + timedelta(days=1)
    await f["writer"].execute_query(
        "MATCH (e:Episodic {uuid:$episode,group_id:$group_id}) SET e.created_at=datetime($stamp)",
        parameters_={"episode": f["episodes"][0], "group_id": f["source"].group_id, "stamp": future.isoformat()})
    later_episode = await service.research(request.model_copy(update={"recorded_before": f["stamp"], "valid_at": f["stamp"], "top_k": 1}))
    assert later_episode.source_claims == () and len(later_episode.simulation_observations) == 1
    source_counts = next(item for item in later_episode.scopes if item.scope == f["source"])
    assert source_counts.excluded == 1 and source_counts.eligible == source_counts.returned == 0
    payload = later_episode.model_dump_json()
    assert f["edges"][0] not in payload and f["episodes"][0] not in payload and "猫 source" not in payload
    await f["writer"].execute_query(
        "MATCH (e:Episodic {uuid:$episode,group_id:$group_id}) SET e.created_at=datetime($stamp)",
        parameters_={"episode": f["episodes"][0], "group_id": f["source"].group_id, "stamp": f["stamp"].isoformat()})

    # Recording cutoff precedes graph row creation, even if valid time is later.
    cutoff = f["stamp"] - timedelta(days=1)
    history = await service.research(request.model_copy(update={"recorded_before": cutoff, "valid_at": f["stamp"]}))
    assert history.source_claims == history.simulation_observations == ()
    assert all(item.excluded == 1 for item in history.scopes)

    # A current retained excerpt must not appear in a recording snapshot before
    # source retention. Move only synthetic graph timestamps, not retained data.
    earlier = f["retained"].recorded_at - timedelta(days=2)
    await f["writer"].execute_query(
        "MATCH ()-[r:RELATES_TO]->() WHERE r.group_id IN $groups "
        "SET r.created_at=datetime($stamp),r.valid_at=datetime($stamp)",
        parameters_={"groups": [f["source"].group_id, f["simulation"].group_id], "stamp": earlier.isoformat()})
    await f["writer"].execute_query(
        "MATCH (e:Episodic) WHERE e.group_id IN $groups SET e.created_at=datetime($stamp)",
        parameters_={"groups": [f["source"].group_id, f["simulation"].group_id], "stamp": earlier.isoformat()})
    historical = await service.research(request.model_copy(update={"recorded_before": earlier + timedelta(days=1), "valid_at": earlier}))
    assert historical.source_claims == historical.simulation_observations == ()
    assert historical.linked_citations == historical.unavailable_citations == historical.resolved_citations == 0
    assert all(item.excluded == 1 and item.eligible == item.returned == 0 for item in historical.scopes)
    payload = historical.model_dump_json()
    assert "😀" not in payload and all(identifier not in payload for identifier in (*f["edges"], *f["episodes"]))
    assert all(str(passage.evidence_id) not in payload for passage in f["retained"].passages)


@pytest.mark.asyncio
async def test_real_foreign_active_uncertain_and_tombstoned_denials_before_driver(retained_graph):
    f = retained_graph
    calls = []
    def forbidden_driver():
        calls.append(1)
        raise AssertionError("graph access after failed admission")
    request = ResearchRequest(display_graph_ids=f["ids"], text="Alice")
    with pytest.raises(ResearchFailure, match="^research_unavailable$"):
        await EvidenceResearchService("foreign", f["factory"], forbidden_driver).research(request)
    ledger, scope, operation = Ledger(f["factory"]), f["simulation"], uuid4()
    ledger.admit(scope, operation, "a" * 64)
    claim = ledger.claim(scope, operation)
    service = EvidenceResearchService("owner", f["factory"], forbidden_driver)
    with pytest.raises(ResearchFailure, match="^research_unavailable$"):
        await service.research(request)
    ledger.mark_uncertain(scope, operation, claim.attempt_id, "fixture_uncertain")
    with pytest.raises(ResearchFailure, match="^research_unavailable$"):
        await service.research(request)
    ledger.tombstone_scope(scope)
    with pytest.raises(ResearchFailure, match="^research_unavailable$"):
        await service.research(request)
    assert calls == []


@pytest.mark.asyncio
async def test_real_subprocess_json_cli_history_and_denied_display(retained_graph, tmp_path):
    from tools.run_unit_tests import _unit_environment

    f = retained_graph
    root = Path(__file__).resolve().parents[3]
    # The accepted factory has already checked the exact disposable DSN. Copy
    # only its validated fields, never the parent's environment or provider keys.
    pg = conninfo_to_dict(os.environ['PROJECT_STORE_POSTGRES_TEST_DSN'])
    env = _unit_environment(tmp_path)
    env['PYTHONPATH'] = os.pathsep.join((str(root), str(root / 'services' / 'knowledge' / 'src')))
    for key in ('LLM_API_KEY', 'OPENAI_API_KEY', 'ZEP_API_KEY', 'DEEPSEEK_API_KEY', 'LLM_BASE_URL'):
        env.pop(key, None)
    env.update(
        GRAPHITI_TELEMETRY_ENABLED='false',
        KNOWLEDGE_PRINCIPAL='owner',
        KNOWLEDGE_DISPLAY_GRAPH_ID=f['ids'][0],
        KNOWLEDGE_BOUND_SCOPE_JSON=f['source'].model_dump_json(),
        KNOWLEDGE_PG_HOST=pg['host'], KNOWLEDGE_PG_PORT=pg['port'],
        KNOWLEDGE_PG_DATABASE=pg['dbname'], KNOWLEDGE_PG_USER=pg['user'],
        KNOWLEDGE_PG_PASSWORD=pg['password'],
        KNOWLEDGE_NEO4J_URI='bolt://127.0.0.1:17687',
        KNOWLEDGE_NEO4J_USER='neo4j',
        KNOWLEDGE_NEO4J_PASSWORD=os.environ['KNOWLEDGE_TEST_PASSWORD'],
    )

    async def invoke(request, expected_status):
        raw = json.dumps(request, ensure_ascii=False, allow_nan=False).encode('utf-8')
        try:
            completed = await asyncio.to_thread(
                subprocess.run, [sys.executable, '-c', _CLI_CHILD],
                input=raw, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                cwd=tmp_path, env=env, timeout=60, check=False,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except subprocess.TimeoutExpired:
            pytest.fail('research_cli_child_timeout', pytrace=False)
        except OSError:
            pytest.fail('research_cli_child_os_error', pytrace=False)
        # Never include subprocess objects, stderr or configuration in failures.
        # Only fixed categories and integer return codes are diagnostic data.
        if completed.returncode != expected_status or completed.stderr:
            category = {
                91: 'guard_violation', 92: 'model_or_search_observed',
                93: 'driver_close_missing', 94: 'pg_close_missing',
                95: 'driver_and_pg_close_missing', 96: 'cli_exit_missing',
            }.get(completed.returncode, 'exit_status')
            stderr_category = 'empty'
            if completed.stderr:
                # Presence detection does not trust/print a warning message.
                # Keep even deprecations failing until Main identifies a known
                # accepted import warning; never suppress runtime warnings here.
                stderr_category = ('deprecation_warning_present'
                                   if b'DeprecationWarning' in completed.stderr
                                   else 'nonempty')
            pytest.fail(
                f'research_cli_child_{category}: expected_exit={expected_status} '
                f'actual_exit={completed.returncode} stderr={stderr_category}',
                pytrace=False)
        if not 0 < len(completed.stdout) <= 2 * 1024 * 1024 or completed.stdout.count(b'\n') != 1:
            pytest.fail('research_cli_output_invalid', pytrace=False)
        try:
            return json.loads(completed.stdout.decode('utf-8'))
        except (ValueError, UnicodeError):
            pytest.fail('research_cli_output_invalid', pytrace=False)

    request = dict(display_graph_ids=list(f['ids']), text='Alice 猫', top_k=10,
                   valid_at=f['stamp'].isoformat(), recorded_before=f['stamp'].isoformat())
    result = await invoke(request, 0)
    assert result['historical'] is True
    assert result['historical_semantics'] == 'retained_edges_not_bitemporal_reconstruction'
    assert result['rank_basis'] == 'lexical_token_overlap'
    assert result['other_claims'] == []
    assert len(result['source_claims']) == len(result['simulation_observations']) == 1
    for key, scope, edge, episode in (
        ('source_claims', f['source'], f['edges'][0], f['episodes'][0]),
        ('simulation_observations', f['simulation'], f['edges'][1], f['episodes'][1]),
    ):
        claim, = result[key]
        assert claim['scope'] == scope.model_dump(mode='json')
        assert claim['claim_class'] == scope.layer.value
        assert claim['provider_id'] == edge and claim['episode_ids'] == [episode]
        assert claim['unavailable_evidence_ids'] == []
        assert set(claim['evidence_ids']) == {str(p.evidence_id) for p in f['retained'].passages}
        assert {c['excerpt'] for c in claim['citations']} == {'😀猫 ', ' ev'}
        for citation in claim['citations']:
            assert citation['source_revision'] == str(f['retained'].source_revision)
            assert citation['source_sha256'] == f['retained'].text_sha256
            assert citation['project_id'] == str(scope.project_id)
            passage = next(p for p in f['retained'].passages if str(p.evidence_id) == citation['evidence_id'])
            assert (citation['start'], citation['end']) == (passage.start, passage.end)
            assert citation['excerpt_sha256'] == passage.excerpt_sha256
            assert datetime.fromisoformat(citation['source_recorded_at'].replace('Z', '+00:00')) == f['retained'].recorded_at
            assert citation['offset_unit'] == 'unicode_codepoint'
    assert result['linked_citations'] == result['resolved_citations'] == 4
    assert result['unavailable_citations'] == 0
    coverage, = result['passage_coverage']
    assert coverage['source_revision'] == str(f['retained'].source_revision)
    assert coverage['retrieved_codepoints'] == 5
    assert coverage['retained_codepoints'] == f['retained'].codepoint_length
    assert coverage['retrieved_passage_fraction'] == 5 / f['retained'].codepoint_length
    assert {s['display_graph_id'] for s in result['scopes']} == set(f['ids'])
    assert all(s['scanned'] == s['eligible'] == s['returned'] == 1
               and s['excluded'] == s['unknown'] == 0 and not s['truncated'] for s in result['scopes'])

    denied = await invoke({**request, 'display_graph_ids': ['denied_' + uuid4().hex]}, 1)
    assert denied == {'error': 'research_unavailable'}
    # Normal child exit observed driver/PG close. This additional admission
    # smoke check is not proof of exclusive lock release (read locks are shared).
    ledger = Ledger(f['factory'])
    with ledger.read_scope(f['source']), ledger.read_scope(f['simulation']):
        pass
