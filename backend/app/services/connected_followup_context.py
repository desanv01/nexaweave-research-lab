"""Fresh, descriptor-bound accepted report context for an owned chat child."""
import hashlib
import time
from .connected_report_context import validate_context
from .connected_report_client import digest, ReportError
from .report_process import report_files
from nexaweave_execution.followup_contracts import FollowupError, CODES, validate_report_context


def freeze_parent(parent_host, report_id, report_plan_sha256, *, deadline=None, tick=None):
    """Return a detached private-copy input only after full parent reauthorization."""
    try:
        def parent_tick():
            if deadline is not None and time.monotonic() >= deadline:
                raise ReportError('timeout')
            if tick is not None:
                try:
                    tick()
                except FollowupError as error:
                    # The shared parent projection reports report control codes.
                    code = {'followup_cancelled': 'report_cancelled',
                            'followup_uncertain': 'report_uncertain'}.get(error.code, error.code)
                    raise ReportError(code) from None
            if deadline is not None and time.monotonic() >= deadline:
                raise ReportError('timeout')
        parent_tick()
        payload = dict(schema_version=1, report_id=report_id, plan_sha256=report_plan_sha256)
        parent = parent_host._reauthorize(parent_host._row(payload), deadline=deadline, tick=parent_tick)
        if parent.state != 'completed' or parent.receipt is None or parent.manifest is None:
            raise FollowupError('conflict')
        context = validate_context(parent.frozen['context'], parent.frozen['identity']['binding'])
        with report_files(parent_host._output_root(parent), parent.manifest) as source:
            raw = source['full_report.md']
            prose = raw.decode('utf-8', errors='strict')
            if not prose.strip():
                raise FollowupError('conflict')
            prefix = prose[:15000]
            provenance = dict(file_sha256=hashlib.sha256(raw).hexdigest(),
                prefix_sha256=hashlib.sha256(prefix.encode('utf-8')).hexdigest(),
                prefix_characters=len(prefix), total_characters=len(prose),
                truncated=len(prose)>15000)
            validate_report_context(provenance)
            bundle = {name:bytes(content) for name,content in source.items()}
        # A second authority check catches changes during the bounded copy.
        parent_tick()
        current = parent_host._row(payload)
        if (current != parent or current.state != 'completed'
                or current.receipt is None or current.manifest is None):
            raise FollowupError('conflict')
        parent_host._reauthorize(current, deadline=deadline, tick=parent_tick)
        parent_tick()
        report = dict(report_id=report_id,plan_sha256=report_plan_sha256,
            receipt_sha256=digest(parent.receipt),manifest_sha256=digest(parent.manifest),
            full_report_sha256=provenance['file_sha256'])
        native = parent.frozen['identity']['binding']
        binding = dict(display_graph_id=native['display_graph_id'],principal=native['principal'],
            scope=native['scope'],report=report,native_binding=native)
        return parent, binding, context, provenance, bundle
    except FollowupError:
        raise
    except ReportError as error:
        code = {'report_cancelled': 'followup_cancelled',
                'report_uncertain': 'followup_uncertain',
                'report_unavailable': 'followup_unavailable',
                'report_failed': 'followup_failed'}.get(error.code, error.code)
        raise FollowupError(code if code in CODES else 'conflict') from None
    except Exception:
        raise FollowupError('conflict') from None


def verify_frozen_parent(parent_host, frozen, *, deadline=None, tick=None):
    report = frozen['identity']['binding']['report']
    parent, binding, context, provenance, bundle = freeze_parent(parent_host,report['report_id'],
        report['plan_sha256'],deadline=deadline,tick=tick)
    if (binding != frozen['identity']['binding'] or context != frozen['context']
            or provenance != frozen['identity']['report_context']
            or digest(context) != frozen['identity']['context_sha256']
            or digest(context['graph']) != frozen['identity']['source_projection_sha256']):
        raise FollowupError('conflict')
    return parent, binding, context, provenance, bundle
