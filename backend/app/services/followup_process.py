"""One owned inherited chat child, private parent copy and immutable turn files."""
import base64
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import pickle
import signal
import time
from types import SimpleNamespace

from nexaweave_execution.followup_contracts import (FollowupError, FILE_BYTES,
    TOTAL_BYTES, FILE_NAMES, digest, encoded, strict_json, validate_identity,
    validate_manifest, validate_conversation)
from nexaweave_execution.report_contracts import validate_manifest as validate_parent_manifest
from .connected_report_context import validate_context, validate_references
from .report_models import BoundedReportModelFactory


@contextmanager
def followup_files(root, manifest):
    """Hold descriptors and verify every ancestor before and after the read."""
    from .native_observation_reader import _safe, _identity
    try:
        validate_manifest(manifest)
        root = Path(root)
        if not root.is_absolute() or root.resolve(strict=True) != root:
            raise ValueError
        ancestors = {p:_identity(_safe(p,True)) for p in (root,*root.parents)}
        with ExitStack() as stack:
            held, content = [], {}
            for file in manifest['files']:
                path = root / file['name']
                before = _identity(_safe(path))
                if before[2] != file['size']:
                    raise ValueError
                descriptor = os.open(path, os.O_RDONLY | getattr(os,'O_NOFOLLOW',0) | getattr(os,'O_BINARY',0))
                stack.callback(os.close,descriptor)
                current = _identity(os.fstat(descriptor))
                if current[:3] != before[:3] or current[-1] != 1:
                    raise ValueError
                chunks, remaining = [], file['size']
                while remaining:
                    chunk = os.read(descriptor,min(65536,remaining))
                    if not chunk:
                        raise ValueError
                    chunks.append(chunk); remaining -= len(chunk)
                raw = b''.join(chunks)
                if os.read(descriptor,1) or hashlib.sha256(raw).hexdigest() != file['sha256']:
                    raise ValueError
                raw.decode('utf-8',errors='strict')
                if path.suffix == '.json':
                    if encoded(strict_json(raw)) != raw:
                        raise ValueError
                held.append((path,descriptor,before,current)); content[file['name']] = raw
            yield content
            for path,descriptor,before,current in held:
                if _identity(_safe(path)) != before or _identity(os.fstat(descriptor)) != current:
                    raise ValueError
            if any(_identity(_safe(path,True))[:2] != identity[:2] for path,identity in ancestors.items()):
                raise ValueError
    except FollowupError:
        raise
    except Exception:
        raise FollowupError('conflict') from None


def output_manifest(root):
    from .native_observation_reader import _safe
    files = []
    for name in FILE_NAMES:
        path = Path(root) / name
        details = _safe(path)
        if not 1 <= details.st_size <= FILE_BYTES:
            raise FollowupError('result_too_large')
        with path.open('rb') as handle:
            raw = handle.read(FILE_BYTES+1)
        if len(raw) != details.st_size:
            raise FollowupError('conflict')
        files.append(dict(name=name,size=len(raw),sha256=hashlib.sha256(raw).hexdigest()))
    return validate_manifest(dict(schema_version=1,files=files))


def prior_conversation(store, root, principal, report_id, report_plan_sha256):
    """Load every completed full answer through its immutable file proof."""
    rows = store.completed_rows(principal,report_id,report_plan_sha256)
    pairs = []
    previous = None
    for row in rows:
        with followup_files(Path(root)/row.turn_id.hex/'output',row.manifest) as content:
            answer = content['answer.md'].decode('utf-8')
            if (hashlib.sha256(content['answer.md']).hexdigest()!=row.answer_sha256
                    or answer[:4000]!=row.answer_prefix or len(answer)!=row.answer_characters):
                raise FollowupError('followup_uncertain')
            pair = dict(turn_id=str(row.turn_id),plan_sha256=row.plan_sha256,
                ordinal=row.receipt['ordinal'],question=row.frozen['identity']['options']['question'],
                answer=answer,answer_sha256=row.answer_sha256,
                predecessor_head_sha256=row.receipt['history_head_sha256'],
                receipt_sha256=digest(row.receipt),published_head_sha256=row.published_head_sha256)
            artifact_pair=dict(pair,receipt_sha256=None,published_head_sha256=None)
            expected=dict(schema_version=1,report_id=report_id,
                report_plan_sha256=report_plan_sha256,total_completed=len(pairs)+1,
                pairs=[*pairs,artifact_pair])
            if strict_json(content['conversation.json'])!=expected:
                raise FollowupError('followup_uncertain')
            if previous is not None and pair['predecessor_head_sha256']!=previous:
                raise FollowupError('followup_uncertain')
            previous=pair['published_head_sha256']; pairs.append(pair)
    if len(encoded(dict(schema_version=1,report_id=report_id,
            report_plan_sha256=report_plan_sha256,total_completed=len(pairs),pairs=pairs)))>FILE_BYTES:
        raise FollowupError('result_too_large')
    return pairs


def conversation_document(identity, prior, answer, answer_sha256):
    """Current final proof is intentionally external to its hashed artifact."""
    report = identity['binding']['report']
    ordinal = identity['history']['total_completed'] + 1
    if ordinal>1000:
        raise FollowupError('result_too_large')
    if len(prior) != ordinal-1:
        raise FollowupError('history_changed')
    current = dict(turn_id=identity['turn_id'],plan_sha256=digest(identity),ordinal=ordinal,
        question=identity['options']['question'],answer=answer,answer_sha256=answer_sha256,
        predecessor_head_sha256=identity['history']['head_sha256'],
        receipt_sha256=None,published_head_sha256=None)
    result = dict(schema_version=1,report_id=report['report_id'],
        report_plan_sha256=report['plan_sha256'],total_completed=ordinal,pairs=[*prior,current])
    if len(encoded(result))>FILE_BYTES:
        raise FollowupError('result_too_large')
    if answer:
        validate_conversation(result,identity,answer)
    return result


def reserve_conversation(identity, prior):
    """Bound the actual escaped question before one shared-budget reservation."""
    placeholder='0'*64
    baseline=conversation_document(identity,prior,'',placeholder)
    # A response capped at 16,384 UTF8 bytes can expand to at most six JSON
    # escape bytes per input byte. Fixed metadata is already serialized above.
    if len(encoded(baseline))+6*16384>FILE_BYTES:
        raise FollowupError('result_too_large')


def _await_control(connection,cancelled,action,deadline,request_number=None):
    if action not in ('go','admitted'):
        raise FollowupError('followup_uncertain')
    if (action=='go' and request_number is not None or action=='admitted'
            and (type(request_number) is not int or request_number<1)):
        raise FollowupError('followup_uncertain')
    acknowledgement_deadline=min(deadline,time.monotonic()+10)
    def receive(until):
        while True:
            if cancelled.is_set():
                raise FollowupError('followup_cancelled')
            remaining=until-time.monotonic()
            if remaining<=0:
                raise FollowupError('timeout')
            try:
                if connection.poll(min(0.1,remaining)):
                    frame=connection.recv()
                    if cancelled.is_set() or time.monotonic()>=until:
                        raise FollowupError('followup_cancelled' if cancelled.is_set() else 'timeout')
                    return frame
            except (EOFError,OSError):
                raise FollowupError('followup_uncertain') from None
    checking=('checking',action) if request_number is None else ('checking',action,request_number)
    admitted=(action,) if request_number is None else (action,request_number)
    def matches(frame,expected):
        return (type(frame) is tuple and frame==expected
                and (request_number is None or type(frame[-1]) is int))
    if not matches(receive(acknowledgement_deadline),checking) or not matches(receive(deadline),admitted):
        raise FollowupError('followup_uncertain')


def _child(connection,cancelled,frozen,factory,operation,parent_bundle,prior,deadline):
    model=None
    try:
        if os.name!='nt':
            os.setsid()
        identity=validate_identity(frozen['identity'])
        context=validate_context(frozen['context'],identity['binding']['native_binding'])
        if (digest(context)!=identity['context_sha256'] or digest(context['graph'])!=identity['source_projection_sha256']
                or type(factory) is not BoundedReportModelFactory or not factory.configured()
                or factory.limits!=identity['limits']
                or hashlib.sha256(pickle.dumps(factory)).hexdigest()!=frozen['configuration']['factory_sha256']):
            raise FollowupError('conflict')
        parent_manifest=validate_parent_manifest(frozen['parent_manifest'])
        report=identity['binding']['report']
        if (digest(parent_manifest)!=report['manifest_sha256']
                or set(parent_bundle)!={file['name'] for file in parent_manifest['files']}
                or sum(len(raw) for raw in parent_bundle.values())>TOTAL_BYTES):
            raise FollowupError('conflict')
        for file in parent_manifest['files']:
            raw=parent_bundle[file['name']]
            if type(raw) is not bytes or len(raw)!=file['size'] or hashlib.sha256(raw).hexdigest()!=file['sha256']:
                raise FollowupError('conflict')
        full=parent_bundle['full_report.md']
        prose=full.decode('utf-8',errors='strict')
        prefix=prose[:15000]
        if (hashlib.sha256(full).hexdigest()!=report['full_report_sha256']
                or hashlib.sha256(prefix.encode('utf-8')).hexdigest()!=identity['report_context']['prefix_sha256']
                or len(prefix)!=identity['report_context']['prefix_characters']
                or len(prose)!=identity['report_context']['total_characters']):
            raise FollowupError('conflict')
        meta=strict_json(parent_bundle['meta.json'])
        if (meta.get('report_id')!=report['report_id'] or meta.get('graph_id')!=identity['binding']['display_graph_id']
                or meta.get('simulation_id')!=context['binding']['preparation']['simulation_id']
                or meta.get('status')!='completed'
                or meta.get('simulation_requirement')!=frozen['parent_requirement']):
            raise FollowupError('conflict')
        operation=Path(operation)
        from .native_observation_reader import _safe
        if not operation.is_absolute() or operation.resolve(strict=True)!=operation:
            raise FollowupError('conflict')
        for parent in (operation,*operation.parents):
            _safe(parent,True)
        work,output=operation/'work',operation/'output'
        if work.exists() or output.exists():
            raise FollowupError('conflict')
        work.mkdir(); output.mkdir()
        _safe(work,True); _safe(output,True)
        os.chdir(work)
        connection.send(('ready',))
        _await_control(connection,cancelled,'go',deadline)
        def checkpoint():
            if cancelled.is_set():
                raise FollowupError('followup_cancelled')
            if time.monotonic()>=deadline:
                raise FollowupError('timeout')
        def request_admission(number):
            checkpoint(); connection.send(('request',number))
            _await_control(connection,cancelled,'admitted',deadline,request_number=number)
            checkpoint()
        # Only this private child changes the inherited process globals.
        from .report_agent import ReportManager
        from ..utils.locale import set_locale
        from .report_dependencies import create_connected_report_agent
        report_id=identity['binding']['report']['report_id']
        private_reports=work/'reports'
        private_reports.mkdir()
        private_report=private_reports/report_id
        private_report.mkdir()
        for file in parent_manifest['files']:
            raw=parent_bundle[file['name']]
            (private_report/file['name']).write_bytes(raw)
        ReportManager.REPORTS_DIR=str(private_reports)
        set_locale(identity['options']['output_language'])
        checkpoint()
        # The owner persists the first-request journal marker on request 1,
        # before acknowledging it. Subsequent numbered requests reauthorize.
        model=factory.create(deadline=deadline,checkpoint=checkpoint,first_call=lambda:None,
                             request_admission=request_admission)
        agent=create_connected_report_agent(context,
            requirement=frozen['parent_requirement'],
            output_language=identity['options']['output_language'],model_client=model)
        history=[]
        for pair in identity['history']['pairs']:
            history.extend([dict(role='user',content=pair['question']),
                            dict(role='assistant',content=pair['answer_prefix'])])
        result=agent.chat(identity['options']['question'],history)
        checkpoint()
        answer=result['response']
        if type(answer) is not str or not answer.strip() or len(answer.encode('utf-8'))>16384:
            raise FollowupError('followup_failed')
        validate_references(answer,context,require_native=False)
        trace=result.get('executed_trace')
        if type(trace) is not list or len(trace)>2 or any(type(item) is not dict
                or set(item)!= {'tool','parameters','observation'}
                or item['tool'] not in agent.tools or item['tool']=='interview_agents'
                or type(item['parameters']) is not dict or type(item['observation']) is not str
                or len(item['observation'])>1500 for item in trace):
            raise FollowupError('followup_failed')
        if len(encoded(trace))>FILE_BYTES:
            raise FollowupError('result_too_large')
        answer_sha=hashlib.sha256(answer.encode('utf-8')).hexdigest()
        conversation=conversation_document(identity,prior,answer,answer_sha)
        turn=dict(schema_version=1,identity=identity,plan_sha256=digest(identity),
            parent_report_provenance=identity['binding']['report'],
            report_context=identity['report_context'],
            history_prompt_limit=dict(pairs=5,answer_prefix_characters=4000),
            inherited_chat_limit=dict(iterations=2,direct_requests=3,answer_utf8_bytes=16384),
            publication_proof_scope='external_current_receipt_and_head')
        retrieval,_=ReportManager.read_evidence(report_id)
        native=strict_json(parent_bundle['native_evidence.json'])
        if native.get('context_sha256')!=identity['context_sha256']:
            raise FollowupError('conflict')
        files=dict(zip(FILE_NAMES,(encoded(turn),answer.encode('utf-8'),encoded(conversation),
            encoded(retrieval),encoded(native),encoded(dict(schema_version=1,executed=trace)))))
        for name,raw in files.items():
            if not 1<=len(raw)<=FILE_BYTES:
                raise FollowupError('result_too_large')
            (output/name).write_bytes(raw)
        manifest=output_manifest(output)
        with followup_files(output,manifest) as contents:
            if contents['answer.md']!=answer.encode('utf-8'):
                raise FollowupError('conflict')
        model.close(); model=None
        checkpoint(); connection.send(('done',manifest))
    except BaseException as error:
        try:
            code=getattr(error,'code','followup_failed')
            code={'report_uncertain':'followup_uncertain','report_cancelled':'followup_cancelled',
                  'report_failed':'followup_failed','chat_failed':'followup_failed'}.get(code,code)
            connection.send(('failed',code if code in ('followup_cancelled','timeout','conflict','result_too_large','followup_uncertain') else 'followup_failed'))
        except BaseException:
            pass
    finally:
        if model is not None:
            try: model.close()
            except BaseException: pass
        connection.close()


@dataclass(frozen=True)
class ProcessOutcome:
    state: str
    cleanup: dict
    error_code: str | None
    manifest: dict | None


class FollowupProcess:
    """One local owner; never resume a partially executed model turn."""
    def __init__(self,*,frozen,model_factory,operation_root,parent_bundle,prior):
        self.frozen,self.factory,self.root,self.bundle,self.prior=(frozen,model_factory,
            Path(operation_root),parent_bundle,prior)

    def run(self,*,checkpoint,first_call,cancelled,heartbeat=None,reauthorize=None,deadline=None):
        import psutil
        ceiling_deadline=time.monotonic()+self.frozen['identity']['limits']['max_run_seconds']
        deadline=ceiling_deadline if deadline is None else min(deadline,ceiling_deadline)
        ctx=multiprocessing.get_context('spawn')
        parent,child=ctx.Pipe(duplex=True)
        event=ctx.Event()
        process=ctx.Process(target=_child,args=(child,event,self.frozen,self.factory,
            str(self.root),self.bundle,self.prior,deadline),
            name='mf-followup-'+self.frozen['identity']['turn_id'][:8])
        state,code,manifest='uncertain','followup_uncertain',None
        owned,ready,first_admitted,job,job_closed={},False,False,None,True
        next_request=1
        try:
            if not callable(reauthorize):
                raise FollowupError('followup_uncertain')
            def admission_checkpoint():
                if cancelled():
                    event.set(); raise FollowupError('followup_cancelled')
                if time.monotonic()>=deadline:
                    raise FollowupError('timeout')
                checkpoint()
                if time.monotonic()>=deadline:
                    raise FollowupError('timeout')
            admission_checkpoint()
            process.start(); child.close()
            if os.name=='nt':
                from ..utils.owned_process import _WindowsJob
                job=_WindowsJob(); job.assign(SimpleNamespace(_handle=process._popen._handle))
                job_closed=False
            root_process=psutil.Process(process.pid)
            owned[root_process.pid]=root_process.create_time()
            handshake=min(deadline,time.monotonic()+10)
            while time.monotonic()<deadline:
                if heartbeat is not None: heartbeat()
                for descendant in root_process.children(recursive=True) if root_process.is_running() else []:
                    owned.setdefault(descendant.pid,descendant.create_time())
                if cancelled():
                    event.set(); state,code='cancelled','followup_cancelled'; break
                checkpoint()
                if not ready and time.monotonic()>=handshake:
                    code='timeout'; break
                if parent.poll(0.1):
                    message=parent.recv()
                    if message==('ready',) and not ready:
                        parent.send(('checking','go'))
                        if os.name!='nt' and os.getpgid(process.pid)!=process.pid:
                            raise FollowupError('followup_uncertain')
                        reauthorize()
                        admission_checkpoint(); parent.send(('go',)); ready=True
                    elif (type(message) is tuple and len(message)==2 and message[0]=='request'
                            and ready and type(message[1]) is int and message[1]==next_request
                            and next_request<=self.frozen['identity']['limits']['max_calls']):
                        parent.send(('checking','admitted',next_request))
                        reauthorize()
                        admission_checkpoint()
                        if next_request==1:
                            first_call()
                            admission_checkpoint()
                            first_admitted=True
                        parent.send(('admitted',next_request))
                        next_request+=1
                    elif type(message) is tuple and len(message)==2 and message[0]=='done' and ready and first_admitted:
                        manifest=validate_manifest(message[1]); state,code='completed',None; break
                    elif type(message) is tuple and len(message)==2 and message[0]=='failed':
                        code=message[1] if message[1] in ('followup_failed','followup_cancelled','timeout','conflict','result_too_large','followup_uncertain') else 'followup_uncertain'
                        state='cancelled' if code=='followup_cancelled' else ('uncertain' if code in ('timeout','followup_uncertain') else 'failed')
                        break
                    else:
                        raise FollowupError('invalid_reply')
                elif not process.is_alive():
                    break
            else:
                code='timeout'
        except BaseException as error:
            code=getattr(error,'code','followup_uncertain')
            if code not in ('conflict','unauthorized','model_calls_disabled','tombstoned',
                            'followup_cancelled','timeout','result_too_large'):
                code='followup_uncertain'
            state='cancelled' if code=='followup_cancelled' else 'uncertain'
        finally:
            cleanup_deadline=time.monotonic()+20
            event.set()
            if process.pid is not None:
                process.join(timeout=min(1,max(0,cleanup_deadline-time.monotonic())))
                if job is not None:
                    try:
                        job.terminate_and_wait(cleanup_deadline)
                        job.close(); job_closed=True
                    except BaseException:
                        state,code='uncertain','followup_uncertain'
                        try: job.close()
                        except BaseException: pass
                elif ready and os.name!='nt':
                    try: os.killpg(process.pid,signal.SIGTERM)
                    except ProcessLookupError: pass
                    except OSError: state,code='uncertain','followup_uncertain'
                try:
                    current=psutil.Process(process.pid)
                    if owned.get(current.pid)==current.create_time():
                        for descendant in current.children(recursive=True):
                            owned.setdefault(descendant.pid,descendant.create_time())
                except psutil.NoSuchProcess: pass
                for pid,created in reversed(list(owned.items())):
                    try:
                        target=psutil.Process(pid)
                        if target.create_time()==created and target.is_running(): target.terminate()
                    except psutil.NoSuchProcess: pass
                    except psutil.Error: state,code='uncertain','followup_uncertain'
                process.join(timeout=min(2,max(0,cleanup_deadline-time.monotonic())))
                if ready and os.name!='nt':
                    try: os.killpg(process.pid,signal.SIGKILL)
                    except ProcessLookupError: pass
                    except OSError: state,code='uncertain','followup_uncertain'
                for pid,created in reversed(list(owned.items())):
                    try:
                        target=psutil.Process(pid)
                        if target.create_time()==created and target.is_running():
                            target.kill()
                            remaining=cleanup_deadline-time.monotonic()
                            if remaining>0: target.wait(timeout=remaining)
                    except psutil.NoSuchProcess: pass
                    except psutil.Error: state,code='uncertain','followup_uncertain'
                process.join(timeout=max(0,cleanup_deadline-time.monotonic()))
            parent.close(); child.close()
            closed=(process.pid is None or not process.is_alive()) and job_closed
            for pid,created in owned.items():
                try:
                    target=psutil.Process(pid)
                    if target.create_time()==created and target.is_running(): closed=False
                except psutil.NoSuchProcess: pass
                except psutil.Error: closed=False
            if ready and os.name!='nt':
                for candidate in psutil.process_iter(['pid','status']):
                    try:
                        if os.getpgid(candidate.pid)==process.pid and candidate.status()!=psutil.STATUS_ZOMBIE:
                            closed=False
                    except (ProcessLookupError,psutil.NoSuchProcess): pass
                    except (OSError,psutil.Error): closed=False
            if process.pid is not None and not process.is_alive():
                if state=='completed' and process.exitcode!=0:
                    state,code='uncertain','followup_uncertain'
                process.close()
        cleanup=dict(known=True,pending=not closed,owner_thread_alive=not closed)
        if not closed:
            return ProcessOutcome('uncertain',cleanup,'followup_uncertain',None)
        return ProcessOutcome(state,cleanup,code,manifest if state=='completed' else None)
