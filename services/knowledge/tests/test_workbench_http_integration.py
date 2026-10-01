"""Actual HTTP -> installed private child -> retained PostgreSQL/Neo4j source.

Main supplies both locked interpreters and the NON-editable installed bootstrap.
No Flask test client, mock facade, replacement transport, or fake store is used.
Passive observations describe these owned processes, not OS-wide isolation.
"""
import asyncio
from datetime import timedelta
import http.client
import json
import os
from pathlib import Path
import subprocess
import time
from uuid import uuid4

import pytest
from psycopg.conninfo import conninfo_to_dict

from mirofish_knowledge.evidence_research import EvidenceResearchService
from mirofish_knowledge.research_contracts import ResearchRequest, ResearchResult
from mirofish_knowledge.report_contracts import EvidenceDossier
from mirofish_knowledge.operations import Ledger
from test_evidence_research_integration import retained_graph, factory, _CLI_CHILD
from test_evidence_dossier_integration import _request, _assert_connected

pytestmark = [pytest.mark.postgres, pytest.mark.neo4j]

_HTTP_CHILD = r'''
import builtins,json,os,sys,threading,subprocess
from pathlib import Path
sys.path.insert(0,os.environ['WORKBENCH_TEST_ROOT'])
sys.path.insert(0,str(Path(os.environ['WORKBENCH_TEST_ROOT'])/'backend'))
from tools.run_unit_tests import LoopbackOnlySockets
guard=LoopbackOnlySockets();guard.install()
original=builtins.__import__
blocked=('mirofish_knowledge','graphiti_core','openai','camel','oasis','torch','transformers','temporalio')
def imports(name,*args,**kwargs):
    if any(name==p or name.startswith(p+'.') for p in blocked):
        raise AssertionError('backend SDK import')
    return original(name,*args,**kwargs)
builtins.__import__=imports
event_lock=threading.Lock()
def emit(value):
    with event_lock:
        print(json.dumps(value,separators=(',',':')),flush=True)
def observe(frame,event,arg):
    module=frame.f_globals.get('__name__','')
    name=frame.f_code.co_name
    if event=='return' and module=='subprocess' and name=='__init__':
        child=frame.f_locals.get('self')
        if isinstance(child,subprocess.Popen):
            arguments=frame.f_locals.get('args',[])
            env=frame.f_locals.get('env',{})
            allowed={'HOME','TMPDIR','TMP','TEMP','GRAPHITI_TELEMETRY_ENABLED','PYTHONNOUSERSITE',
                     'PATH','LANG','SystemRoot','WINDIR','USERPROFILE'}
            safe=(len(arguments)==4 and arguments[1:3]==['-I','-u']
                  and Path(arguments[3]).name in {'read_bootstrap.py','evidence_bootstrap.py'}
                  and 'site-packages' in Path(arguments[3]).parts
                  and all(k in allowed or k in {
                      'KNOWLEDGE_PRINCIPAL','KNOWLEDGE_DISPLAY_GRAPH_ID','KNOWLEDGE_BOUND_SCOPE_JSON',
                      'KNOWLEDGE_PG_HOST','KNOWLEDGE_PG_PORT','KNOWLEDGE_PG_DATABASE','KNOWLEDGE_PG_USER',
                      'KNOWLEDGE_PG_PASSWORD','KNOWLEDGE_NEO4J_URI','KNOWLEDGE_NEO4J_USER',
                      'KNOWLEDGE_NEO4J_PASSWORD'} for k in env))
            emit({'event':'spawn','safe':safe,'pid':child.pid,
                  'bootstrap':Path(arguments[3]).name if len(arguments)==4 else None})
    if event=='return' and module=='app.services.knowledge_transport' and name=='_stop_owned':
        child=frame.f_locals.get('process')
        threads=frame.f_locals.get('threads',[])
        emit({'event':'cleanup','closed':child is not None and child.poll() is not None
              and child.stdin.closed and child.stdout.closed and all(not t.is_alive() for t in threads)})
sys.setprofile(observe);threading.setprofile(observe)
from app import create_app
from werkzeug.serving import make_server
server=make_server('127.0.0.1',0,create_app(),threaded=True)
emit({'event':'ready','port':server.server_port})
def stop():
    sys.stdin.buffer.read(1)
    server.shutdown()
threading.Thread(target=stop,daemon=True).start()
try:
    server.serve_forever(poll_interval=0.1)
finally:
    server.server_close()
    sys.setprofile(None);threading.setprofile(None)
    emit({'event':'shutdown','loopback_blocked':guard.blocked_attempts})
    guard.restore()
'''


def _events(path):
    try:
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    except (OSError, ValueError):
        return []


def _exchange(port, method, path, body=None, *, token=None, origin=None):
    headers = {}
    if token is not None:
        headers["Authorization"] = "Bearer " + token
    if origin is not None:
        headers["Origin"] = origin
    raw = None if body is None else json.dumps(body, ensure_ascii=False, allow_nan=False).encode()
    if raw is not None:
        headers["Content-Type"] = "application/json"
        headers["Content-Length"] = str(len(raw))
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=125)
    try:
        connection.request(method, path, body=raw, headers=headers)
        response = connection.getresponse()
        data = response.read(4 * 1024 * 1024 + 1025)
        assert len(data) <= 4 * 1024 * 1024 + 1024
        return response.status, dict(response.getheaders()), json.loads(data)
    finally:
        connection.close()


@pytest.mark.asyncio
async def test_actual_http_installed_pipe_retained_evidence_history_and_denial(retained_graph, tmp_path):
    f = retained_graph
    # Only this test's disposable groups receive complete node provenance. The
    # shared evidence fixture intentionally needs no MENTIONS for edge research.
    # Bind each episode/edge and both endpoints to the SAME owned group; never
    # create unscoped links or weaken production enumeration's provenance guard.
    source_nodes = None
    source_evidence = None
    for scope, episode, edge in zip((f["source"], f["simulation"]), f["episodes"], f["edges"], strict=True):
        records, _, _ = await f["writer"].execute_query(
            "MATCH (e:Episodic {uuid:$episode,group_id:$group_id}), "
            "(o:MiroFishIngest {uuid:$episode,group_id:$group_id,status:'complete'}), "
            "(a:Entity {group_id:$group_id})-[r:RELATES_TO {uuid:$edge,group_id:$group_id}]->"
            "(b:Entity {group_id:$group_id}) "
            "WHERE $episode IN r.episodes "
            "MERGE (e)-[:MENTIONS]->(a) MERGE (e)-[:MENTIONS]->(b) "
            "RETURN a.uuid AS source_id,b.uuid AS target_id,o.evidence_ids AS evidence_ids",
            parameters_={"episode": episode, "edge": edge, "group_id": scope.group_id})
        assert len(records) == 1
        assert len(records[0]["evidence_ids"]) == len(set(records[0]["evidence_ids"])) == 2
        if scope == f["source"]:
            source_nodes = (records[0]["source_id"], records[0]["target_id"])
            source_evidence = set(records[0]["evidence_ids"])
    assert source_nodes is not None and source_evidence is not None
    backend_python = os.environ.get("MIROFISH_WORKBENCH_BACKEND_PYTHON")
    knowledge_python = os.environ.get("KNOWLEDGE_PYTHON")
    bootstrap = os.environ.get("KNOWLEDGE_BOOTSTRAP_SCRIPT")
    if not backend_python or not knowledge_python or not bootstrap:
        pytest.fail("Main must supply locked backend/knowledge interpreters and installed bootstrap", pytrace=False)
    assert Path(backend_python).is_absolute() and Path(backend_python).is_file()
    assert Path(knowledge_python).is_absolute() and Path(knowledge_python).is_file()
    assert "site-packages" in Path(bootstrap).parts and Path(bootstrap).name == "read_bootstrap.py"
    assert Path(bootstrap).with_name("evidence_bootstrap.py").is_file()
    from tools.run_unit_tests import _unit_environment
    env = _unit_environment(tmp_path)
    for key in list(env):
        if key.startswith(("KNOWLEDGE_", "LLM_", "OPENAI_", "DEEPSEEK_", "ZEP_")) or key.upper() in {
                "PYTHONPATH", "PYTHONHOME", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
                "PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE", "PGPASSFILE", "PGOPTIONS"}:
            env.pop(key, None)
    pg = conninfo_to_dict(os.environ["PROJECT_STORE_POSTGRES_TEST_DSN"])
    token = "0123456789abcdef" * 4
    env.update(WORKBENCH_TEST_ROOT=str(Path(__file__).resolve().parents[3]),
        MIROFISH_APP_MODE="graphiti_readonly", PYTHON_DOTENV_DISABLED="1", FLASK_HOST="127.0.0.1",
        FLASK_DEBUG="0", MIROFISH_ALLOWED_ORIGINS="http://localhost:3000", KNOWLEDGE_READ_TOKEN=token,
        KNOWLEDGE_PYTHON=knowledge_python, KNOWLEDGE_BOOTSTRAP_SCRIPT=bootstrap,
        KNOWLEDGE_PRINCIPAL="owner", KNOWLEDGE_DISPLAY_GRAPH_ID=f["ids"][0],
        KNOWLEDGE_BOUND_SCOPE_JSON=f["source"].model_dump_json(), KNOWLEDGE_PG_HOST=pg["host"],
        KNOWLEDGE_PG_PORT=pg["port"], KNOWLEDGE_PG_DATABASE=pg["dbname"], KNOWLEDGE_PG_USER=pg["user"],
        KNOWLEDGE_PG_PASSWORD=pg["password"], KNOWLEDGE_NEO4J_URI="bolt://127.0.0.1:17687",
        KNOWLEDGE_NEO4J_USER="neo4j", KNOWLEDGE_NEO4J_PASSWORD=os.environ["KNOWLEDGE_TEST_PASSWORD"])
    # Separate passive installed-bootstrap probes observe knowledge-internal
    # socket/model/driver/PG closure without altering the default HTTP pipe.
    # -I loads the actual non-editable package; only the test guard is admitted
    # from the repository, then that import path is immediately removed.
    probe = ("import os,sys\nsys.path.insert(0,os.environ['WORKBENCH_TEST_ROOT'])\n" +
        _CLI_CHILD.replace("mirofish_knowledge.research_cli", "mirofish_knowledge.evidence_bootstrap")
        .replace("guard = LoopbackOnlySockets()", "sys.path.remove(os.environ['WORKBENCH_TEST_ROOT'])\nguard = LoopbackOnlySockets()"))
    for operation, request_payload in (
        ("research", ResearchRequest(display_graph_ids=f["ids"], text="Alice 猫",
                                    valid_at=f["stamp"], recorded_before=f["stamp"]).model_dump(mode="json")),
        ("dossier", _request(f, valid_at=f["stamp"], recorded_before=f["stamp"]).model_dump(mode="json"))):
        request_id = str(uuid4())
        raw = json.dumps({"version": 1, "request_id": request_id, "method": operation,
            "scope": f["source"].model_dump(mode="json"), "payload": request_payload},
            ensure_ascii=False, allow_nan=False).encode()
        completed = await asyncio.to_thread(subprocess.run, [knowledge_python, "-I", "-u", "-c", probe],
            input=len(raw).to_bytes(4, "big") + raw, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=tmp_path, env=env, timeout=125, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if completed.returncode != 0 or completed.stderr:
            pytest.fail("installed evidence probe socket/model/close/exit observation failed", pytrace=False)
        framed = completed.stdout
        assert 4 < len(framed) <= 4 * 1024 * 1024 + 1028
        assert int.from_bytes(framed[:4], "big") == len(framed[4:])
        result = json.loads(framed[4:])
        assert result["ok"] is True and result["request_id"] == request_id
        if operation == "dossier":
            _assert_connected(EvidenceDossier.model_validate_json(json.dumps(result["result"])), f)
        else:
            assert ResearchResult.model_validate_json(json.dumps(result["result"])).resolved_citations == 4
    output_path, error_path = tmp_path / "http-events.jsonl", tmp_path / "http-errors.log"
    process = None
    calls = 0  # Evidence calls each own one child; graph GET owns two page children.
    try:
        with output_path.open("wb") as output, error_path.open("wb") as errors:
            process = subprocess.Popen([backend_python, "-I", "-u", "-c", _HTTP_CHILD],
                stdin=subprocess.PIPE, stdout=output, stderr=errors, cwd=tmp_path, env=env,
                shell=False, close_fds=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            deadline = time.monotonic() + 20
            ready = []
            while time.monotonic() < deadline and process.poll() is None:
                ready = [e for e in _events(output_path) if e.get("event") == "ready"]
                if ready:
                    break
                await asyncio.sleep(0.05)
            if not ready:
                pytest.fail("workbench HTTP readiness failed (private diagnostics retained)", pytrace=False)
            port = ready[0]["port"]
            async def http(method, path, body=None, **headers):
                return await asyncio.to_thread(_exchange, port, method, path, body, **headers)
            route = "/api/graph/research/" + f["ids"][0]
            research = ResearchRequest(display_graph_ids=f["ids"], text="Alice 猫", top_k=10,
                valid_at=f["stamp"], recorded_before=f["stamp"])
            payload = research.model_dump(mode="json")
            assert (await http("POST", route, payload))[0] == 401
            assert (await http("POST", route, payload, token=token, origin="http://evil.example"))[0] == 403
            assert not [e for e in _events(output_path) if e.get("event") == "spawn"]
            status, headers, body = await http("GET", "/api/graph/data/" + f["ids"][0],
                token=token, origin="http://localhost:3000")
            assert status == 200 and headers["Access-Control-Allow-Origin"] == "http://localhost:3000"
            assert body["success"] is True and set(body) == {"success", "data"}
            graph = body["data"]
            assert graph["graph_id"] == f["ids"][0]
            assert graph["node_count"] == len(graph["nodes"]) == 2
            assert graph["edge_count"] == len(graph["edges"]) == 1
            assert {node["uuid"] for node in graph["nodes"]} == set(source_nodes)
            assert {node["name"] for node in graph["nodes"]} == {"Alice", "猫"}
            for node in graph["nodes"]:
                assert node["episodes"] == [f["episodes"][0]]
                assert set(node["evidence_ids"]) == source_evidence
            edge, = graph["edges"]
            assert edge["uuid"] == f["edges"][0]
            assert edge["source_node_uuid"] == source_nodes[0] and edge["source_node_name"] == "Alice"
            assert edge["target_node_uuid"] == source_nodes[1] and edge["target_node_name"] == "猫"
            assert edge["name"] == edge["fact_type"] == "WORKS_FOR"
            assert edge["fact"] == "Alice works for 猫 source"
            assert edge["episodes"] == [f["episodes"][0]] and set(edge["evidence_ids"]) == source_evidence
            assert f["edges"][1] not in json.dumps(graph) and f["episodes"][1] not in json.dumps(graph)
            status, headers, body = await http("POST", route, payload, token=token, origin="http://localhost:3000")
            calls += 1
            assert status == 200 and headers["Access-Control-Allow-Origin"] == "http://localhost:3000"
            actual = ResearchResult.model_validate_json(json.dumps(body["data"]))
            expected = await EvidenceResearchService("owner", f["factory"], f["new_driver"],
                trusted_scope=f["source"]).research(research)
            assert actual == expected and actual.resolved_citations == 4
            assert set(map(str, actual.source_claims[0].evidence_ids)) == source_evidence
            for claim in (*actual.source_claims, *actual.simulation_observations):
                assert {c.excerpt for c in claim.citations} == {"😀猫 ", " ev"}
                assert all(c.source_sha256 == f["retained"].text_sha256 for c in claim.citations)
            dossier_route = "/api/graph/dossier/" + f["ids"][0]
            dossier = _request(f, valid_at=f["stamp"], recorded_before=f["stamp"])
            status, _, body = await http("POST", dossier_route, dossier.model_dump(mode="json"), token=token)
            calls += 1
            assert status == 200
            _assert_connected(EvidenceDossier.model_validate_json(json.dumps(body["data"])), f)
            early = dict(payload, recorded_before=(f["stamp"] - timedelta(days=1)).isoformat())
            status, _, body = await http("POST", route, early, token=token)
            calls += 1
            assert status == 200 and body["data"]["source_claims"] == body["data"]["simulation_observations"] == []
            assert all(edge not in json.dumps(body) for edge in f["edges"])
            denied = dict(payload, display_graph_ids=[f["ids"][0], "denied_persisted_selection"])
            status, _, body = await http("POST", route, denied, token=token)
            calls += 1
            assert status == 503 and body == {"success": False, "error": {"code": "evidence_unavailable"}}
            process.stdin.write(b"x"); process.stdin.flush(); process.stdin.close()
            await asyncio.to_thread(process.wait, timeout=10)
            assert process.returncode == 0
    finally:
        if process is not None:
            if process.poll() is None:
                process.terminate()
                try:
                    await asyncio.to_thread(process.wait, timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    await asyncio.to_thread(process.wait, timeout=5)
            if process.stdin is not None and not process.stdin.closed:
                process.stdin.close()
    events = _events(output_path)
    spawned = [e for e in events if e.get("event") == "spawn"]
    cleaned = [e for e in events if e.get("event") == "cleanup"]
    assert calls == 4
    assert len(spawned) == len(cleaned) == calls + 2 == 6
    assert len({e["pid"] for e in spawned}) == 6 and all(e["safe"] for e in spawned)
    assert sum(e["bootstrap"] == "read_bootstrap.py" for e in spawned) == 2
    assert sum(e["bootstrap"] == "evidence_bootstrap.py" for e in spawned) == calls
    assert all(e["closed"] for e in cleaned)
    assert {"event": "shutdown", "loopback_blocked": 0} in events
    # Re-admit both actual ledger scopes and retrieve unchanged retained passages.
    with Ledger(f["factory"]).read_scope(f["source"]), Ledger(f["factory"]).read_scope(f["simulation"]):
        pass
    preserved = await EvidenceResearchService("owner", f["factory"], f["new_driver"],
        trusted_scope=f["source"]).research(research)
    assert preserved == expected
