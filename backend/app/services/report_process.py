"""Spawned inherited ReportAgent with private CWD, explicit model and finite ownership."""
from contextlib import contextmanager, ExitStack
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
from .connected_report_client import (ReportError, BASE_NAMES, FILE_BYTES, TOTAL_BYTES, digest,
    encoded, strict_json, validate_identity, validate_manifest)
from .connected_report_context import validate_context, validate_references
from .report_models import BoundedReportModelFactory


@contextmanager
def report_files(root, manifest):
    """Descriptors and every ancestor remain bound through caller publication/read."""
    from .native_observation_reader import _safe, _identity
    validate_manifest(manifest)
    root = Path(root)
    try:
        if not root.is_absolute() or root.resolve(strict=True) != root:
            raise ValueError
        ancestors = {p: _identity(_safe(p, True)) for p in (root, *root.parents)}
        with ExitStack() as stack:
            held, content = [], {}
            for file in manifest['files']:
                path = root / file['name']
                before = _identity(_safe(path))
                if before[2] != file['size']:
                    raise ValueError
                descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0))
                stack.callback(os.close, descriptor)
                current = _identity(os.fstat(descriptor))
                if current[:3] != before[:3] or current[-1] != 1:
                    raise ValueError
                chunks, remaining = [], file['size']
                while remaining:
                    chunk = os.read(descriptor, min(65536, remaining))
                    if not chunk:
                        raise ValueError
                    chunks.append(chunk); remaining -= len(chunk)
                raw = b''.join(chunks)
                if os.read(descriptor, 1) or hashlib.sha256(raw).hexdigest() != file['sha256']:
                    raise ValueError
                raw.decode('utf-8', errors='strict')
                if file['name'].endswith('.json'):
                    strict_json(raw)
                held.append((path, descriptor, before, current)); content[file['name']] = raw
            yield content
            for path, descriptor, before, current in held:
                if _identity(_safe(path)) != before or _identity(os.fstat(descriptor)) != current:
                    raise ValueError
            if any(_identity(_safe(p, True))[:2] != identity[:2] for p, identity in ancestors.items()):
                raise ValueError
    except ReportError:
        raise
    except Exception:
        raise ReportError('conflict') from None


def output_manifest(root, sections):
    from .native_observation_reader import _safe
    names = BASE_NAMES + ['section_%02d.md' % i for i in range(1, sections + 1)]
    files = []
    for name in names:
        path = Path(root) / name
        details = _safe(path)
        if not 1 <= details.st_size <= FILE_BYTES:
            raise ReportError('result_too_large')
        with path.open('rb') as handle:
            raw = handle.read(FILE_BYTES + 1)
        if len(raw) != details.st_size:
            raise ReportError('conflict')
        files.append(dict(name=name, size=len(raw), sha256=hashlib.sha256(raw).hexdigest()))
    return validate_manifest(dict(schema_version=1, files=files))


def _await_control(connection, cancelled, action, deadline):
    """Prompt action-bound acknowledgement, then grant on the unchanged clock."""
    if action not in ('go', 'admitted'):
        raise ReportError('report_uncertain')
    acknowledgement_deadline = min(deadline, time.monotonic() + 10)
    def receive(until):
        while True:
            if cancelled.is_set():
                raise ReportError('report_cancelled')
            remaining = until - time.monotonic()
            if remaining <= 0:
                raise ReportError('timeout')
            try:
                if connection.poll(min(0.1, remaining)):
                    frame = connection.recv()
                    if cancelled.is_set():
                        raise ReportError('report_cancelled')
                    if time.monotonic() >= until:
                        raise ReportError('timeout')
                    return frame
            except (EOFError, OSError):
                raise ReportError('report_uncertain') from None
    if receive(acknowledgement_deadline) != ('checking', action):
        raise ReportError('report_uncertain')
    if receive(deadline) != (action,):
        raise ReportError('report_uncertain')


def _child(connection, cancelled, frozen, factory, operation, deadline):
    """All preflight runs before inherited imports or transport construction."""
    model = None
    try:
        if os.name != 'nt':
            os.setsid()  # Own the entire Unix descendant group before readiness.
        identity = validate_identity(frozen['identity'])
        context = validate_context(frozen['context'], identity['binding'])
        if (digest(context) != identity['context_sha256'] or digest(context['graph']) != identity['source_projection_sha256']
                or type(factory) is not BoundedReportModelFactory or not factory.configured()
                or factory.limits != identity['limits']
                or hashlib.sha256(pickle.dumps(factory)).hexdigest() != frozen['configuration']['factory_sha256']):
            raise ReportError('conflict')
        operation = Path(operation)
        from .native_observation_reader import _safe
        if not operation.is_absolute() or operation.resolve(strict=True) != operation:
            raise ReportError('conflict')
        for parent in (operation, *operation.parents):
            _safe(parent, True)
        work, output = operation / 'work', operation / 'output'
        if work.exists() or output.exists():
            raise ReportError('conflict')
        work.mkdir(); output.mkdir()
        _safe(work, True); _safe(output, True)
        os.chdir(work)
        connection.send(('ready',))
        _await_control(connection, cancelled, 'go', deadline)

        def checkpoint():
            if cancelled.is_set():
                raise ReportError('report_cancelled')
            if time.monotonic() >= deadline:
                raise ReportError('timeout')

        def first_call():
            checkpoint()
            connection.send(('first',))
            _await_control(connection, cancelled, 'admitted', deadline)
            checkpoint()

        # Globals are assigned only in this spawned operation process.
        from .report_agent import ReportManager, ReportStatus
        from ..utils.locale import set_locale
        from .report_dependencies import create_connected_report_agent
        ReportManager.REPORTS_DIR = str(output)
        set_locale(identity['options']['output_language'])
        checkpoint()
        model = factory.create(deadline=deadline, checkpoint=checkpoint, first_call=first_call)
        agent = create_connected_report_agent(context, requirement=identity['options']['requirement'],
            output_language=identity['options']['output_language'], model_client=model)
        report_id = identity['report_id']

        def progress(stage, percent, _message):
            checkpoint()
            known = ReportManager.get_progress(report_id) or {}
            completed = len(known.get('completed_sections', []))
            outline = output / report_id / 'outline.json'
            total = len(strict_json(outline.read_bytes())['sections']) if outline.exists() else 0
            connection.send(('progress', dict(stage=stage if stage in ('planning', 'researching', 'writing', 'publishing', 'generating') else 'generating',
                percent=max(0, min(99, int(percent))), completed_sections=min(completed, total), total_sections=total)))

        report = agent.generate_report(report_id=report_id, progress_callback=progress)
        checkpoint()
        if report.status != ReportStatus.COMPLETED or report.outline is None:
            raise ReportError('report_failed')
        root = output / report_id
        native_evidence = dict(schema_version=1, context_sha256=identity['context_sha256'],
            native=identity['binding']['native'], coverage=identity['binding']['coverage'],
            reference_keys=identity['binding']['reference_keys'], records=context['native_records'],
            manifest=context['native_manifest'], search_ranking='lexical_only', semantic_support_status='not_reviewed')
        (root / 'native_evidence.json').write_bytes(encoded(native_evidence))
        manifest = output_manifest(root, len(report.outline.sections))
        with report_files(root, manifest) as content:
            validate_references(content['full_report.md'].decode('utf-8'), context)
            for name in content:
                if name.startswith('section_'):
                    validate_references(content[name].decode('utf-8'), context, require_native=False)
            agent.zep_tools.restore_evidence(strict_json(content['retrieval_evidence.json']))
        model.close(); model = None
        checkpoint()
        connection.send(('done', manifest))
    except BaseException as error:
        try:
            code = getattr(error, 'code', 'report_failed')
            connection.send(('failed', code if code in ('report_cancelled', 'timeout', 'conflict', 'result_too_large', 'report_uncertain') else 'report_failed'))
        except BaseException:
            pass
    finally:
        if model is not None:
            try:
                model.close()
            except BaseException:
                pass
        connection.close()


@dataclass(frozen=True)
class ProcessOutcome:
    state: str
    cleanup: dict
    error_code: str | None
    manifest: dict | None


class ReportProcess:
    """One local owner; never restarted or resumed after lost owner state."""
    def __init__(self, *, frozen, model_factory, operation_root):
        self.frozen, self.factory, self.root = frozen, model_factory, Path(operation_root)

    def run(self, *, checkpoint, first_call, progress, cancelled, heartbeat=None, reauthorize=None, deadline=None):
        import psutil
        ceiling_deadline = time.monotonic() + self.frozen['identity']['limits']['max_run_seconds']
        deadline = ceiling_deadline if deadline is None else min(deadline, ceiling_deadline)
        ctx = multiprocessing.get_context('spawn')
        parent, child = ctx.Pipe(duplex=True)
        event = ctx.Event()
        process = ctx.Process(target=_child, args=(child, event, self.frozen, self.factory, str(self.root), deadline),
                              name='mf-report-' + self.frozen['identity']['report_id'][:8])
        state, code, manifest = 'uncertain', 'report_uncertain', None
        owned, ready, first_admitted, job, job_closed = {}, False, False, None, True
        try:
            def admission_checkpoint():
                if cancelled():
                    event.set()
                    raise ReportError('report_cancelled')
                if time.monotonic() >= deadline:
                    raise ReportError('timeout')
                checkpoint()
                if time.monotonic() >= deadline:
                    raise ReportError('timeout')
            admission_checkpoint()
            process.start(); child.close()
            if os.name == 'nt':
                from ..utils.owned_process import _WindowsJob
                job = _WindowsJob()
                job.assign(SimpleNamespace(_handle=process._popen._handle))
                job_closed = False
            root_process = psutil.Process(process.pid)
            owned[root_process.pid] = root_process.create_time()
            handshake = min(deadline, time.monotonic() + 10)
            while time.monotonic() < deadline:
                if heartbeat is not None:
                    heartbeat()
                for descendant in root_process.children(recursive=True) if root_process.is_running() else []:
                    owned.setdefault(descendant.pid, descendant.create_time())
                if cancelled():
                    event.set(); state, code = 'cancelled', 'report_cancelled'; break
                checkpoint()
                if not ready and time.monotonic() >= handshake:
                    code = 'timeout'; break
                if parent.poll(0.1):
                    message = parent.recv()
                    if message == ('ready',) and not ready:
                        parent.send(('checking', 'go'))
                        if os.name != 'nt' and os.getpgid(process.pid) != process.pid:
                            raise ReportError('report_uncertain')
                        if reauthorize is not None:
                            reauthorize()
                        admission_checkpoint(); parent.send(('go',)); ready = True
                    elif message == ('first',) and ready and not first_admitted:
                        parent.send(('checking', 'admitted'))
                        if reauthorize is not None:
                            reauthorize()
                        admission_checkpoint(); first_call(); admission_checkpoint(); parent.send(('admitted',)); first_admitted = True
                    elif type(message) is tuple and len(message) == 2 and message[0] == 'progress' and ready:
                        progress(message[1])
                    elif type(message) is tuple and len(message) == 2 and message[0] == 'done' and ready and first_admitted:
                        manifest = validate_manifest(message[1]); state, code = 'completed', None; break
                    elif type(message) is tuple and len(message) == 2 and message[0] == 'failed':
                        code = message[1] if message[1] in ('report_failed', 'report_cancelled', 'timeout', 'conflict', 'result_too_large', 'report_uncertain') else 'report_uncertain'
                        state = 'cancelled' if code == 'report_cancelled' else ('uncertain' if code in ('timeout', 'report_uncertain') else 'failed')
                        break
                    else:
                        raise ReportError('invalid_reply')
                elif not process.is_alive():
                    break
            else:
                code = 'timeout'
        except BaseException as error:
            code = getattr(error, 'code', 'report_uncertain')
            if code not in ('conflict', 'unauthorized', 'report_cancelled', 'timeout', 'result_too_large'):
                code = 'report_uncertain'
            state = 'cancelled' if code == 'report_cancelled' else 'uncertain'
        finally:
            # One total cleanup deadline; no extension to obtain a passing result.
            cleanup_deadline = time.monotonic() + 20
            event.set()
            if process.pid is not None:
                process.join(timeout=min(1, max(0, cleanup_deadline - time.monotonic())))
                if job is not None:
                    try:
                        job.terminate_and_wait(cleanup_deadline)
                        job.close(); job_closed = True
                    except BaseException:
                        state, code = 'uncertain', 'report_uncertain'
                        try:
                            job.close()
                        except BaseException:
                            pass
                elif ready and os.name != 'nt':
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    except OSError:
                        state, code = 'uncertain', 'report_uncertain'
                try:
                    current = psutil.Process(process.pid)
                    if owned.get(current.pid) == current.create_time():
                        for descendant in current.children(recursive=True):
                            owned.setdefault(descendant.pid, descendant.create_time())
                except psutil.NoSuchProcess:
                    pass
                for pid, created in reversed(list(owned.items())):
                    try:
                        target = psutil.Process(pid)
                        if target.create_time() == created and target.is_running():
                            target.terminate()
                    except psutil.NoSuchProcess:
                        pass
                    except psutil.Error:
                        state, code = 'uncertain', 'report_uncertain'
                process.join(timeout=min(2, max(0, cleanup_deadline - time.monotonic())))
                if ready and os.name != 'nt':
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    except OSError:
                        state, code = 'uncertain', 'report_uncertain'
                for pid, created in reversed(list(owned.items())):
                    try:
                        target = psutil.Process(pid)
                        if target.create_time() == created and target.is_running():
                            target.kill()
                            remaining = cleanup_deadline - time.monotonic()
                            if remaining > 0:
                                target.wait(timeout=remaining)
                    except psutil.NoSuchProcess:
                        pass
                    except psutil.Error:
                        state, code = 'uncertain', 'report_uncertain'
                process.join(timeout=max(0, cleanup_deadline - time.monotonic()))
            parent.close(); child.close()
            closed = (process.pid is None or not process.is_alive()) and job_closed
            for pid, created in owned.items():
                try:
                    target = psutil.Process(pid)
                    if target.create_time() == created and target.is_running():
                        closed = False
                except psutil.NoSuchProcess:
                    pass
                except psutil.Error:
                    closed = False
            if ready and os.name != 'nt':
                # A vanished root is not proof its descendant group is empty.
                for candidate in psutil.process_iter(['pid', 'status']):
                    try:
                        if os.getpgid(candidate.pid) == process.pid and candidate.status() != psutil.STATUS_ZOMBIE:
                            closed = False
                    except (ProcessLookupError, psutil.NoSuchProcess):
                        pass
                    except (OSError, psutil.Error):
                        closed = False
            if process.pid is not None and not process.is_alive():
                if state == 'completed' and process.exitcode != 0:
                    state, code = 'uncertain', 'report_uncertain'
                process.close()
        cleanup = dict(known=True, pending=not closed, owner_thread_alive=not closed)
        if not closed:
            return ProcessOutcome('uncertain', cleanup, 'report_uncertain', None)
        return ProcessOutcome(state, cleanup, code, manifest if state == 'completed' else None)
