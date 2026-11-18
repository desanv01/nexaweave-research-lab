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

from nexaweave_knowledge.evidence_research import EvidenceResearchService
from nexaweave_knowledge.research_contracts import ResearchRequest, ResearchResult
from nexaweave_knowledge.report_contracts import EvidenceDossier
from nexaweave_knowledge.operations import Ledger
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
blocked=('nexaweave_knowledge','graphiti_core','openai','camel','oasis','torch','transformers','temporalio')
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
        owner=frame.f_locals.get('owner');child=getattr(owner,'process',None)
        threads=frame.f_locals.get('threads',[])
        cleanup={'event':'cleanup','closed':owner is not None and owner.closed
              and (os.name!='nt' or (owner.tree_empty and owner.job is None))
              and child is not None and child.poll() is not None
              and child.stdin.closed and child.stdout.closed and all(not t.is_alive() for t in threads)}
        exit_code=getattr(child,'returncode',None)
        if os.environ.get('WORKBENCH_TEST_EVENT_LOST_ACK')=='0' and type(exit_code) is int and -(2**31)<=exit_code<2**32:
            cleanup['exit_code']=exit_code
        emit(cleanup)
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
    backend_python = os.environ.get("NEXAWEAVE_WORKBENCH_BACKEND_PYTHON")
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
        NEXAWEAVE_APP_MODE="graphiti_readonly", PYTHON_DOTENV_DISABLED="1", FLASK_HOST="127.0.0.1",
        FLASK_DEBUG="0", NEXAWEAVE_ALLOWED_ORIGINS="http://localhost:3000", KNOWLEDGE_READ_TOKEN=token,
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
        _CLI_CHILD.replace("nexaweave_knowledge.research_cli", "nexaweave_knowledge.evidence_bootstrap")
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


_EVENT_HTTP_CHILD = (_HTTP_CHILD
    .replace("blocked=('nexaweave_knowledge','graphiti_core'", "blocked=('neo4j','graphiti_core'")
    .replace("'read_bootstrap.py','evidence_bootstrap.py'}", "'native_observation_event_bootstrap.py'}")
    .replace("len(arguments)==4 and arguments[1:3]", "len(arguments)>=4 and arguments[4:] in ([],['--verify-uncertain']) and arguments[1:3]")
    .replace("'bootstrap':Path(arguments[3]).name if len(arguments)==4 else None",
             "'bootstrap':Path(arguments[3]).name if len(arguments)>=4 else None,'verify':arguments[4:]==['--verify-uncertain']")
    .replace("server=make_server", """
from app.services.native_observations_event_runtime import EventRuntime
from nexaweave_knowledge.contracts import KnowledgeScope
from nexaweave_knowledge.operations import Ledger
from uuid import UUID
import psycopg
original_write=EventRuntime.write
def observe_event_write(self, payload):
    reply=original_write(self,payload)
    extra=self.environment
    def connect():
        return psycopg.connect(host=extra['KNOWLEDGE_PG_HOST'],port=int(extra['KNOWLEDGE_PG_PORT']),
            dbname=extra['KNOWLEDGE_PG_DATABASE'],user=extra['KNOWLEDGE_PG_USER'],
            password=extra['KNOWLEDGE_PG_PASSWORD'],connect_timeout=3)
    scope=KnowledgeScope.model_validate_json(json.dumps(payload['scope']))
    row=Ledger(connect).get(scope,UUID(payload['operation_id']))
    assert row.state.value=='running' and str(row.current_attempt)==payload['attempt_id']
    assert reply['marker']['attempt_id']==payload['attempt_id']
    emit({'event':'graph_ack','attempt_id':payload['attempt_id']})
    if os.environ.get('WORKBENCH_TEST_EVENT_LOST_ACK')=='1':
        raise OSError('test lost committed graph acknowledgement')
    return reply
EventRuntime.write=observe_event_write
original_status=Ledger.read_staged_event
def observe_event_status(self,*args,**kwargs):
    tripwire=Path('revoke-before-event-status')
    if not tripwire.is_file():
        return original_status(self,*args,**kwargs)
    tripwire.unlink()
    source=KnowledgeScope.model_validate_json(os.environ['KNOWLEDGE_BOUND_SCOPE_JSON'])
    with self._connect() as conn:
        changed=conn.execute('UPDATE mf_knowledge.scopes SET tombstoned=true WHERE group_id=%s AND NOT tombstoned',
                             (source.group_id,))
        assert changed.rowcount==1
    try:
        emit({'event':'status_authority_revoked'})
        return original_status(self,*args,**kwargs)
    finally:
        with self._connect() as conn:
            conn.execute('UPDATE mf_knowledge.scopes SET tombstoned=false WHERE group_id=%s',(source.group_id,))
Ledger.read_staged_event=observe_event_status
server=make_server"""))


async def _invalid_event_writer(backend, directory, env, path, request, token):
    directory.mkdir()
    output_path = directory / "server-events.jsonl"
    invalid_env = dict(env, NEXAWEAVE_OBSERVATION_NEO4J_URI="bolt://127.0.0.1:65536")
    with output_path.open("wb") as output, (directory / "errors.log").open("wb") as errors:
        process = subprocess.Popen([str(backend), "-I", "-u", "-c", _EVENT_HTTP_CHILD],
            stdin=subprocess.PIPE, stdout=output, stderr=errors, cwd=directory, env=invalid_env,
            shell=False, close_fds=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            deadline = time.monotonic() + 20
            ready = []
            while time.monotonic() < deadline and process.poll() is None:
                ready = [event for event in _events(output_path) if event.get("event") == "ready"]
                if ready:
                    break
                await asyncio.sleep(0.05)
            assert ready, "invalid writer settings must leave HTTP available"
            status, _, body = await asyncio.to_thread(_exchange, ready[0]["port"], "POST", path, request, token=token)
            assert status == 503 and body == {"success": False, "error": {"code": "observations_unavailable"}}
            process.stdin.write(b"x")
            process.stdin.flush()
            process.stdin.close()
            assert await asyncio.to_thread(process.wait, timeout=10) == 0
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    await asyncio.to_thread(process.wait, timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    await asyncio.to_thread(process.wait, timeout=5)
            if not process.stdin.closed:
                process.stdin.close()
    events = _events(output_path)
    assert not [event for event in events if event.get("event") in {"spawn", "graph_ack"}]
    assert {"event": "shutdown", "loopback_blocked": 0} in events


async def _event_dispatch_failure(body, saved, output_path, writer, scope, episode, operation):
    """Report only fixed vocabulary, bounded counts, and exact marker flags."""
    error = body.get("error") if isinstance(body, dict) else None
    code = error.get("code") if isinstance(error, dict) else None
    allowed_codes = ("invalid_request", "not_found", "unauthorized", "origin_denied", "conflict",
                     "tombstoned", "busy", "result_too_large", "observations_unavailable",
                     "evidence_invalid", "invalid_reply", "internal_error")
    state = saved.state.value
    summary = {
        "reported_error": code if code in allowed_codes else "other",
        "ledger_state": state if state in ("pending", "running", "completed", "uncertain",
                                           "cancelled", "failed_no_effect") else "other",
        "ledger_uncertainty": saved.error_code if saved.error_code in (None, "event_ack_uncertain") else "other",
        "safe_spawn_count": None, "closed_cleanup_count": None, "graph_ack_count": None,
        "marker_present": None, "marker_attempt_matches": None,
    }
    try:
        with output_path.open("rb") as stream:
            raw = stream.read(16385)
        if len(raw) <= 16384:
            lines = raw.splitlines()
            if len(lines) <= 32:
                events = [json.loads(line) for line in lines]
                if all(isinstance(event, dict) for event in events):
                    cleanups = [event for event in events if event.get("event") == "cleanup"]
                    summary["safe_spawn_count"] = sum(event.get("event") == "spawn" and event.get("safe") is True
                                                      for event in events)
                    summary["closed_cleanup_count"] = sum(event.get("closed") is True for event in cleanups)
                    summary["graph_ack_count"] = sum(event.get("event") == "graph_ack" for event in events)
                    if len(cleanups) == 1:
                        exit_code = cleanups[0].get("exit_code")
                        if type(exit_code) is int and -(2**31) <= exit_code < 2**32:
                            summary["child_exit_code"] = exit_code
    except (OSError, ValueError, RecursionError):
        pass

    try:
        records, _, _ = await asyncio.wait_for(writer.execute_query(
            "MATCH (m:MiroFishIngest {uuid:$episode,group_id:$group,operation_id:$operation}) "
            "WITH m LIMIT 2 RETURN count(m)>0 AS present, "
            "count(m)=1 AND count(CASE WHEN m.attempt_id=$attempt THEN 1 END)=1 AS attempt_matches",
            parameters_={"group": scope.group_id, "episode": episode, "operation": str(operation),
                         "attempt": str(saved.current_attempt) if saved.current_attempt is not None else None}),
            timeout=3)
        if len(records) == 1:
            summary["marker_present"] = records[0]["present"] is True
            summary["marker_attempt_matches"] = records[0]["attempt_matches"] is True
    except Exception:
        pass  # Leave unread flags unknown and preserve the original assertion.
    return json.dumps(summary, sort_keys=True)


def _record_status_selector(request):
    return dict({key: value for key, value in request.items() if key != "expected_fingerprint"},
                schema_version=3)


async def _assert_event_status(send, request, scope, operation, row):
    status, body = await send("event-status", request)
    completed = row.state.value == "completed"
    fields = dict(scope=scope.model_dump(mode="json"), operation_id=str(operation),
                  state=row.state.value, completion_barrier_open=completed,
                  attempt_id=str(row.current_attempt) if row.current_attempt is not None else None,
                  receipt=row.receipt.json_value() if completed else None)
    assert status == 200 and body == {"success": True, "data": dict(request, **fields)}
    locator = _record_status_selector(request)
    status, body = await send("event-status", locator)
    assert status == 200 and body == {
        "success": True, "data": dict(locator, fingerprint=row.fingerprint, **fields),
    }


async def _recover_event_ack(send, writer, ledger, scope, operation, episode, request, uncertain):
    """Exercise the same request and attempt with absent, altered and committed evidence."""
    from uuid import uuid5

    params = {"group": scope.group_id, "episode": episode, "attempt": str(uncertain.current_attempt),
              "foreign": str(operation)}

    async def graph(query):
        rows, _, _ = await writer.execute_query(query, parameters_=params)
        return rows

    async def snapshot():
        nodes = await graph(
            "MATCH (n {group_id:$group}) RETURN labels(n) AS labels, properties(n) AS props "
            "ORDER BY n.uuid, labels")
        links = await graph(
            "MATCH (a {group_id:$group})-[r]->(b {group_id:$group}) "
            "RETURN a.uuid AS src, b.uuid AS dst, type(r) AS kind, properties(r) AS props "
            "ORDER BY src, dst, kind")
        return nodes, links

    baseline = await snapshot()
    await _assert_event_status(send, request, scope, operation, uncertain)
    assert ledger.get(scope, operation) == uncertain and await snapshot() == baseline
    marker = "MATCH (m:MiroFishIngest {uuid:$episode,group_id:$group}) "
    post = "MATCH (:Episodic {uuid:$episode,group_id:$group})-[:MENTIONS]->(p:NativePost {group_id:$group}) "
    for change, restore in (
        (marker + "REMOVE m:MiroFishIngest SET m:FixtureHiddenEventAck",
         "MATCH (m:FixtureHiddenEventAck {uuid:$episode,group_id:$group}) "
         "REMOVE m:FixtureHiddenEventAck SET m:MiroFishIngest"),
        (marker + "SET m.attempt_id=$foreign", marker + "SET m.attempt_id=$attempt"),
        (post + "SET p.summary='fixture altered post'", post + "SET p.summary='restored action'"),
    ):
        try:
            await graph(change)
            altered = await snapshot()
            assert altered != baseline
            status, failure = await send("dispatch-event", request)
            assert status == 503 and failure == {"success": False, "error": {"code": "observations_unavailable"}}
            assert ledger.get(scope, operation) == uncertain
            assert await snapshot() == altered
        finally:
            await graph(restore)
        assert await snapshot() == baseline
    expected_receipt = {
        "group_id": scope.group_id, "episode_id": episode, "fingerprint": uncertain.fingerprint,
        "evidence_ids": [str(uuid5(operation, "mirofish:restored-twitter-action-evidence:v1"))],
    }
    status, recovered = await send("dispatch-event", request)
    assert status == 200 and recovered["data"]["receipt"] == expected_receipt
    saved = ledger.get(scope, operation)
    assert saved.state.value == "completed" and saved.current_attempt == uncertain.current_attempt
    assert saved.error_code is None and saved.receipt.json_value() == expected_receipt
    await _assert_event_status(send, request, scope, operation, saved)
    assert await send("dispatch-event", request) == (200, recovered)
    assert ledger.get(scope, operation) == saved and await snapshot() == baseline
    return saved


@pytest.mark.asyncio
async def test_actual_protected_restored_event_dispatch_replay_and_lost_ack(retained_graph, tmp_path, monkeypatch):
    from hashlib import sha256
    from uuid import UUID

    root = Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(root / "backend"))
    monkeypatch.syspath_prepend(str(root / "backend/tests"))
    from app.services.native_observations_client import stage_operation_id
    from app.services.native_observations_host import NativeObservationsHost
    from test_native_observations_authority import seed_receipt_event
    from tools.run_unit_tests import _unit_environment

    f = retained_graph
    backend = Path(os.environ["NEXAWEAVE_WORKBENCH_AUTHORITY_PYTHON"])
    bootstrap = Path(os.environ["KNOWLEDGE_BOOTSTRAP_SCRIPT"])
    script = bootstrap.with_name("native_observation_event_bootstrap.py")
    pg = conninfo_to_dict(os.environ["PROJECT_STORE_POSTGRES_TEST_DSN"])
    token = "0123456789abcdef" * 4

    for lost_ack in (False, True):
        directory = tmp_path / ("lost-ack" if lost_ack else "dispatch")
        directory.mkdir()
        launch, locator, target_scope, _ = seed_receipt_event(f["factory"], directory / "artifacts")
        operation = stage_operation_id(locator["launch_id"], locator["record_index"])
        fingerprint = NativeObservationsHost(launch_host=launch)._event(locator)[2]
        episode = str(target_scope.episode_uuid(operation))
        display = launch.display_graph_id
        ledger = Ledger(f["factory"])
        env = _unit_environment(directory)
        env.update(WORKBENCH_TEST_ROOT=str(Path(__file__).resolve().parents[3]),
            WORKBENCH_TEST_EVENT_LOST_ACK=str(int(lost_ack)),
            NEXAWEAVE_APP_MODE="research_local", PYTHON_DOTENV_DISABLED="1", FLASK_HOST="127.0.0.1",
            FLASK_DEBUG="0", NEXAWEAVE_ALLOWED_ORIGINS="http://localhost:3000", KNOWLEDGE_READ_TOKEN=token,
            KNOWLEDGE_PYTHON=os.environ["KNOWLEDGE_PYTHON"], KNOWLEDGE_BOOTSTRAP_SCRIPT=str(bootstrap),
            KNOWLEDGE_PRINCIPAL="owner", KNOWLEDGE_DISPLAY_GRAPH_ID=display,
            KNOWLEDGE_BOUND_SCOPE_JSON=json.dumps(launch.scope_dto),
            KNOWLEDGE_PG_HOST=pg["host"], KNOWLEDGE_PG_PORT=pg["port"], KNOWLEDGE_PG_DATABASE=pg["dbname"],
            KNOWLEDGE_PG_USER=pg["user"], KNOWLEDGE_PG_PASSWORD=pg["password"],
            KNOWLEDGE_NEO4J_URI="bolt://127.0.0.1:17687", KNOWLEDGE_NEO4J_USER="neo4j",
            KNOWLEDGE_NEO4J_PASSWORD=os.environ["KNOWLEDGE_TEST_PASSWORD"],
            NEXAWEAVE_OBSERVATION_EVENTS_ENABLED="1",
            NEXAWEAVE_OBSERVATION_ARTIFACT_ROOT=str(launch.preparation.root),
            NEXAWEAVE_OBSERVATION_RUNTIME_SHA256=launch.runtime_sha256,
            NEXAWEAVE_OBSERVATION_CHILD_SHA256=sha256(script.read_bytes()).hexdigest(),
            NEXAWEAVE_OBSERVATION_NEO4J_URI="bolt://127.0.0.1:17687", NEXAWEAVE_OBSERVATION_NEO4J_USER="neo4j",
            NEXAWEAVE_OBSERVATION_NEO4J_PASSWORD=os.environ["KNOWLEDGE_TEST_PASSWORD"])
        output_path = directory / "server-events.jsonl"
        process = None
        try:
            with output_path.open("wb") as output, (directory / "errors.log").open("wb") as errors:
                process = subprocess.Popen([str(backend), "-I", "-u", "-c", _EVENT_HTTP_CHILD],
                    stdin=subprocess.PIPE, stdout=output, stderr=errors, cwd=directory, env=env,
                    shell=False, close_fds=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                deadline = time.monotonic() + 20
                ready = []
                while time.monotonic() < deadline and process.poll() is None:
                    ready = [event for event in _events(output_path) if event.get("event") == "ready"]
                    if ready:
                        break
                    await asyncio.sleep(0.05)
                assert ready, "split HTTP startup failed"

                async def send(action, value, *, bearer=token, graph=display, origin=None):
                    path = f"/api/native-observations/{action}/{graph}"
                    status, _, body = await asyncio.to_thread(_exchange, ready[0]["port"], "POST",
                                                              path, value, token=bearer, origin=origin)
                    return status, body

                unreviewed = dict(locator, expected_fingerprint="0" * 64)
                request = dict(locator, expected_fingerprint=fingerprint)
                record_status = _record_status_selector(request)
                assert (await send("dispatch-event", request, bearer=None))[0] == 401
                assert (await send("dispatch-event", request, origin="http://evil.example"))[0] == 403
                assert (await send("dispatch-event", request, graph=f["ids"][0]))[0] == 404
                assert (await send("dispatch-event", request))[0] == 409
                assert await send("event-status", request) == (
                    404, {"success": False, "error": {"code": "not_found"}})
                assert await send("event-status", record_status) == (
                    404, {"success": False, "error": {"code": "not_found"}})
                assert (await send("event-status", dict(record_status, expected_fingerprint=fingerprint)))[0] == 400
                assert (await send("event-status", dict(record_status, schema_version=3.0)))[0] == 400
                assert (await send("stage-event", record_status))[0] == 400
                assert (await send("dispatch-event", dict(record_status, expected_fingerprint=fingerprint)))[0] == 400
                status, staged = await send("stage-event", locator)
                assert status == 200 and staged["data"]["operation_id"] == str(operation)
                pending = ledger.get(target_scope, operation)
                assert pending.state.value == "pending" and pending.current_attempt is None
                assert pending.fingerprint == staged["data"]["fingerprint"] == fingerprint
                assert (await send("dispatch-event", unreviewed))[0] == 409
                assert (await send("event-status", request, bearer=None))[0] == 401
                assert (await send("event-status", request, origin="http://evil.example"))[0] == 403
                assert (await send("event-status", request, graph=f["ids"][0]))[0] == 404
                assert (await send("event-status", unreviewed))[0] == 409
                assert (await send("event-status", dict(request, record_sha256="0" * 64)))[0] == 409
                assert (await send("event-status", dict(record_status, record_sha256="0" * 64)))[0] == 409
                assert (await send("event-status", record_status, bearer=None))[0] == 401
                assert (await send("event-status", record_status, origin="http://evil.example"))[0] == 403
                assert (await send("event-status", record_status, graph=f["ids"][0]))[0] == 404
                await _assert_event_status(send, request, target_scope, operation, pending)
                if lost_ack:
                    for selector in (request, record_status):
                        (directory / "revoke-before-event-status").write_bytes(b"1")
                        assert await send("event-status", selector) == (
                            410, {"success": False, "error": {"code": "tombstoned"}})
                    assert _events(output_path).count({"event": "status_authority_revoked"}) == 2
                    await _assert_event_status(send, request, target_scope, operation, pending)
                assert ledger.get(target_scope, operation) == pending
                assert not [event for event in _events(output_path) if event.get("event") == "spawn"]
                if not lost_ack:
                    await _invalid_event_writer(backend, directory / "invalid-writer", env,
                                                f"/api/native-observations/dispatch-event/{display}", request, token)
                    assert ledger.get(target_scope, operation) == pending
                    records, _, _ = await f["writer"].execute_query(
                        "MATCH (n {group_id:$group}) RETURN count(n) AS nodes",
                        parameters_={"group": target_scope.group_id})
                    assert len(records) == 1 and records[0]["nodes"] == 0
                status, dispatched = await send("dispatch-event", request)
                saved = ledger.get(target_scope, operation)
                if not lost_ack and (status != 200 or saved.state.value != "completed" or saved.current_attempt is None):
                    failure = await _event_dispatch_failure(
                        dispatched, saved, output_path, f["writer"], target_scope, episode, operation)
                    pytest.fail(failure, pytrace=False)
                assert saved.current_attempt is not None
                if lost_ack:
                    assert status == 503 and saved.state.value == "uncertain" and saved.receipt is None
                    assert saved.error_code == "event_ack_uncertain"
                    saved = await _recover_event_ack(send, f["writer"], ledger, target_scope,
                                                     operation, episode, request, saved)
                else:
                    assert status == 200 and saved.state.value == "completed"
                    assert dispatched["data"]["receipt"] == saved.receipt.json_value()
                    await _assert_event_status(send, request, target_scope, operation, saved)
                    assert await send("dispatch-event", request) == (200, dispatched)
                records, _, _ = await f["writer"].execute_query(
                    "MATCH (m:MiroFishIngest {uuid:$episode,group_id:$group}), "
                    "(e:Episodic {uuid:$episode,group_id:$group})-[:MENTIONS]->(a:NativeActor {group_id:$group}), "
                    "(e)-[:MENTIONS]->(p:NativePost {group_id:$group}), "
                    "(a)-[r:RELATES_TO {group_id:$group}]->(p) "
                    "RETURN properties(m) AS marker,a.native_id AS actor,a.name AS name,p.native_id AS post,"
                    "p.summary AS content,r.fact AS fact,r.episodes AS episodes LIMIT 2",
                    parameters_={"group": target_scope.group_id, "episode": episode})
                record, = records
                marker = record["marker"]
                assert (record["actor"], record["name"], record["post"], record["content"], record["fact"],
                        record["episodes"]) == (0, "Recorded actor", 2, "restored action", "restored action", [episode])
                assert marker["operation_id"] == str(operation) and marker["attempt_id"] == str(saved.current_attempt)
                assert marker["fingerprint"] == pending.fingerprint and marker["record_sha256"] == locator["record_sha256"]
                assert marker["receipt_sha256"] == launch.store.get("owner", UUID(locator["launch_id"])).receipt["evidence_sha256"]
                assert json.loads(marker["scope_json"]) == target_scope.model_dump(mode="json")
                assert marker["status"] == "complete" and len(marker["evidence_ids"]) == 1
                with f["factory"]() as conn:
                    assert conn.execute(
                        "SELECT attempt_id,outcome,error_code FROM mf_knowledge.attempts "
                        "WHERE group_id=%s AND operation_id=%s", (target_scope.group_id, operation),
                    ).fetchall() == [(saved.current_attempt, "completed", None)]
                    assert conn.execute(
                        "SELECT attempt_id FROM mf_knowledge.scope_admissions WHERE group_id=%s",
                        (target_scope.group_id,),
                    ).fetchone() is None
                    assert conn.execute("SELECT count(*) FROM mf_execution.reservations WHERE operation_id=%s",
                                        (operation,)).fetchone() == (0,)
                process.stdin.write(b"x")
                process.stdin.flush()
                process.stdin.close()
                assert await asyncio.to_thread(process.wait, timeout=10) == 0
            events = _events(output_path)
            spawned = [event for event in events if event.get("event") == "spawn"]
            closed = [event for event in events if event.get("event") == "cleanup"]
            assert len(spawned) == len(closed) == (5 if lost_ack else 1)
            assert all(event["safe"] for event in spawned) and all(event["closed"] for event in closed)
            assert [event["verify"] for event in spawned] == [False] + [True] * (4 if lost_ack else 0)
            assert [event for event in events if event.get("event") == "graph_ack"] == [
                {"event": "graph_ack", "attempt_id": str(saved.current_attempt)}]
            assert {"event": "shutdown", "loopback_blocked": 0} in events
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
            await f["writer"].execute_query("MATCH (n {group_id:$group}) DETACH DELETE n",
                                            parameters_={"group": target_scope.group_id})
