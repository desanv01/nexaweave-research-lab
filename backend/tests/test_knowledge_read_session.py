"""Actual isolated framing, poisoned-session cleanup and context ownership."""
import json
from pathlib import Path
import sys
import threading
from uuid import uuid4
import pytest
from app.services.knowledge_read_session import KnowledgeReadSession
from app.services.knowledge_transport import (KnowledgeBusy, KnowledgeInvalidRequest,
    KnowledgeTransportFailure, KnowledgeCooperativeAbort)

CHILD = '''import sys,json,os
inp,out=sys.stdin.buffer,sys.stdout.buffer
while True:
 h=inp.read(4)
 if len(h)!=4:raise SystemExit(2)
 n=int.from_bytes(h,'big')
 if n==0:
  if inp.read(1)!=b'':raise SystemExit(3)
  break
 raw=inp.read(n);v=json.loads(raw)
 reply=json.dumps(dict(version=1,request_id=v['request_id'],ok=True,result=dict(pid=os.getpid()))).encode()
 out.write(len(reply).to_bytes(4,'big')+reply);out.flush()
'''

def request():
    return json.dumps(dict(version=1,request_id=str(uuid4()),method='page',scope={},payload={})).encode()

def client(tmp_path, code=CHILD, **kwargs):
    script=tmp_path/'child.py';script.write_text(code)
    return KnowledgeReadSession(str(Path(sys.executable).absolute()),str(script),timeout_seconds=2,**kwargs)

def closed(session):
    assert session._closed and session._owner.closed
    assert session._process is None or session._process.poll() is not None
    assert all(not thread.is_alive() for thread in session._threads)
    assert session._directory is None or not Path(session._directory.name).exists()

def test_two_fresh_calls_use_one_owned_interpreter_and_close(tmp_path):
    session=client(tmp_path)
    with session:
        one=json.loads(session.call(request()))
        two=json.loads(session.call(request()))
        assert one['result']['pid']==two['result']['pid']
        assert one['request_id']!=two['request_id']
    closed(session)
    with pytest.raises(KnowledgeInvalidRequest):session.call(request())

def test_replayed_request_poisons_and_closes_session(tmp_path):
    session=client(tmp_path);raw=request()
    with pytest.raises(KnowledgeInvalidRequest),session:
        session.call(raw);session.call(raw)
    closed(session)

@pytest.mark.parametrize('code',[CHILD.replace("request_id=v['request_id']","request_id='wrong'"),
    'raise SystemExit(0)',CHILD.replace('break',"out.write(b'x');out.flush();break")])
def test_wrong_id_premature_eof_or_trailing_bytes_fail_closed(tmp_path,code):
    session=client(tmp_path,code)
    with pytest.raises(KnowledgeTransportFailure),session:
        session.call(request())
    closed(session)

def test_fresh_cooperative_revocation_before_second_page(tmp_path):
    permitted=True
    def tick():
        if not permitted:raise KnowledgeCooperativeAbort('unauthorized')
    session=client(tmp_path,cooperative_tick=tick)
    with pytest.raises(KnowledgeCooperativeAbort),session:
        session.call(request());permitted=False;session.call(request())
    closed(session)

def test_session_cannot_be_used_from_inheriting_thread(tmp_path):
    session=client(tmp_path);errors=[]
    with session:
        def cross_thread():
            try:session.call(request())
            except KnowledgeBusy:errors.append('busy')
        thread=threading.Thread(target=cross_thread);thread.start();thread.join(2)
        assert not thread.is_alive() and errors==['busy']
        assert session._process is None
        session.call(request())
    closed(session)

def test_context_local_reader_does_not_leak_to_another_request(tmp_path):
    from types import SimpleNamespace
    from app.services.knowledge_read_facade import KnowledgeReadFacade
    facade=KnowledgeReadFacade(SimpleNamespace())
    marker=object();token=facade._verification_client.set(marker);observed=[]
    try:
        thread=threading.Thread(target=lambda:observed.append(facade.has_parent_verification_session()))
        thread.start();thread.join(2)
        assert observed==[False] and facade.has_parent_verification_session()
    finally:facade._verification_client.reset(token)
    assert not facade.has_parent_verification_session()

def test_changed_scope_closes_before_second_dispatch(tmp_path):
    session=client(tmp_path)
    with pytest.raises(KnowledgeInvalidRequest),session:
        session.call(request())
        changed=json.loads(request());changed['scope']={'changed':True}
        session.call(json.dumps(changed).encode())
    closed(session)

@pytest.mark.parametrize('exit_immediately',[False,True])
def test_failed_page_is_terminal_even_for_direct_trusted_caller(tmp_path,exit_immediately):
    code=CHILD.replace("ok=True,result=dict(pid=os.getpid())","ok=False,error=dict(code='timeout')")
    if exit_immediately:code=code.replace('out.flush()','out.flush();raise SystemExit(1)')
    session=client(tmp_path,code)
    with session:
        assert json.loads(session.call(request()))['error']['code']=='timeout'
        closed(session)
        with pytest.raises(KnowledgeInvalidRequest):session.call(request())

def test_inherited_async_task_cannot_use_parent_session(tmp_path):
    import asyncio
    async def scenario():
        session=client(tmp_path)
        with session:
            async def other():
                with pytest.raises(KnowledgeBusy):session.call(request())
            await asyncio.create_task(other())
            assert session._process is None
        closed(session)
    asyncio.run(scenario())

def test_abort_with_queued_writer_command_closes_threads(tmp_path,monkeypatch):
    import app.services.knowledge_read_session as module
    original=module._writer
    release=threading.Event()
    def delayed(pipe,commands,events,shutdown):
        while not release.wait(.01):
            if shutdown.is_set():return
        return original(pipe,commands,events,shutdown)
    monkeypatch.setattr(module,'_writer',delayed)
    session=client(tmp_path)
    def tick():
        if not session._commands.empty():raise KnowledgeCooperativeAbort('unauthorized')
    session._cooperative_tick=tick
    with pytest.raises(KnowledgeCooperativeAbort),session:session.call(request())
    closed(session)

def test_control_first_seen_at_finish_survives_cleanup_error(tmp_path,monkeypatch):
    allowed=True
    def tick():
        if not allowed:raise KnowledgeCooperativeAbort('followup_cancelled')
    session=client(tmp_path,cooperative_tick=tick)
    original=session._owner.stop
    def stop(threads):
        original(threads);raise RuntimeError('private synthetic cleanup failure')
    monkeypatch.setattr(session._owner,'stop',stop)
    with pytest.raises(KnowledgeCooperativeAbort) as error,session:
        session.call(request());allowed=False
    assert error.value.code=='followup_cancelled' and session.cleanup_failures==['RuntimeError']
    closed(session)

@pytest.mark.parametrize('late',['deadline','control'])
def test_successful_cleanup_cannot_authorize_late_parent_return(tmp_path,monkeypatch,late):
    import time
    allowed=True
    def tick():
        if not allowed:raise KnowledgeCooperativeAbort('unauthorized')
    session=client(tmp_path,cooperative_tick=tick)
    original=session._owner.stop
    def stop(threads):
        nonlocal allowed
        original(threads)
        if late=='deadline':session._deadline=time.monotonic()-1
        else:allowed=False
    monkeypatch.setattr(session._owner,'stop',stop)
    expected=KnowledgeTransportFailure if late=='deadline' else KnowledgeCooperativeAbort
    with pytest.raises(expected),session:session.call(request())
    closed(session)
