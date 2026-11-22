"""Bounded component timings for the guarded Windows followup recovery calls."""
from contextlib import contextmanager, ExitStack
from contextvars import ContextVar
import math
import time


COMPONENTS = ('connection-acquisition', 'parent-verification', 'artifact-validation')
OPERATIONS = ('history', 'start', 'read', 'download')


class RecoveryTimings:
    """Measure local work while preserving every original operation outcome.

    Component totals are sums of potentially overlapping intervals, not
    exclusive wall time; artifact timing covers context entry and exit only.
    """

    def __init__(self, connections, emit):
        self.connections, self.emit = connections, emit
        try:
            self.active = ContextVar('followup_recovery_timings', default=None)
        except Exception:
            self.active = None
        try:
            # WindowsRecoveryConnections calls this factory only to open a
            # physical connection; borrowing a lease connection never does.
            connections.factory = self.wrap('connection-acquisition', connections.factory)
        except Exception:
            pass

    @staticmethod
    def clock():
        try:
            return time.perf_counter()
        except Exception:
            return None

    def record(self, component, started, *, count=1):
        try:
            metrics = self.active.get()
            if metrics is None:
                return
            sample = metrics[component]
            sample[0] += count
            if started is None:
                return
            ended = self.clock()
            if ended is None:
                return
            elapsed = ended - started
            total = sample[1] + elapsed
            if math.isfinite(elapsed) and elapsed >= 0 and math.isfinite(total):
                sample[1] = total
        except Exception:
            pass

    def wrap(self, component, operation):
        def measured(*args, **kwargs):
            try:
                active = self.active.get()
            except Exception:
                active = None
            if active is None:
                return operation(*args, **kwargs)
            started = self.clock()
            try:
                return operation(*args, **kwargs)
            finally:
                self.record(component, started)
        return measured

    def wrap_files(self, operation):
        @contextmanager
        def measured(*args, **kwargs):
            try:
                active = self.active.get()
            except Exception:
                active = None
            if active is None:
                with operation(*args, **kwargs) as content:
                    yield content
                return
            started = self.clock()
            try:
                with operation(*args, **kwargs) as content:
                    # Time only descriptor entry and exit. The yielded body is
                    # host work, not artifact-context validation time.
                    self.record('artifact-validation', started, count=0)
                    try:
                        yield content
                    finally:
                        started = self.clock()
            finally:
                self.record('artifact-validation', started)
        return measured

    def _install(self, monkeypatch):
        from app.services import durable_followup_host
        stack = ExitStack()
        try:
            probes = stack.enter_context(monkeypatch.context())
            for name in ('freeze_parent', 'verify_frozen_parent'):
                probes.setattr(durable_followup_host, name, self.wrap(
                    'parent-verification', getattr(durable_followup_host, name)))
            probes.setattr(durable_followup_host, 'followup_files',
                           self.wrap_files(durable_followup_host.followup_files))
            return stack
        except Exception:
            try:
                stack.close()
            except Exception:
                pass
            raise

    def report(self, records):
        for record in records:
            try:
                phase, elapsed = record
                self.emit(phase, 'end', None, elapsed_seconds=elapsed)
            except Exception:
                pass

    def run(self, operation, body, monkeypatch):
        name = getattr(operation, '__name__', '')
        label = name if name in OPERATIONS else 'call'
        try:
            metrics = {component: [0, 0.0] for component in COMPONENTS}
            token = self.active.set(metrics)
        except Exception:
            result, counts = self.connections.run(operation, body)
            return result, counts, ()
        try:
            try:
                stack = self._install(monkeypatch)
            except Exception:
                result, counts = self.connections.run(operation, body)
                return result, counts, ()
            try:
                result, counts = self.connections.run(operation, body)
            finally:
                try:
                    stack.close()
                except Exception:
                    pass
        finally:
            try:
                self.active.reset(token)
            except Exception:
                pass
        try:
            records = tuple(
                (f'windows-recovery-timing-{label}-{component}-calls-{metrics[component][0]}',
                 metrics[component][1])
                for component in COMPONENTS)
        except Exception:
            records = ()
        return result, counts, records
