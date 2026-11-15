"""Freeze exact source passages and native log lexemes before any model work."""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import re
import time
from .connected_report_client import (ReportError, CONTEXT_BYTES, digest, encoded, exact,
    integer, text, sha, uuid_string, validate_binding, reference_key, validate_payload)


def _linked_sources(sources, graph, check):
    """Keep membership only for the at most 100 explicitly admitted passages."""
    selected = set()
    for source in sources:
        check()
        for passage in source['passages']:
            check()
            selected.add(passage['evidence_id'])
    linked = set()
    for kind in ('nodes', 'edges'):
        check()
        for item in graph[kind]:
            check()
            for uid in item['evidence_ids']:
                check()
                if uid in selected:
                    linked.add(uid)
    for source in sources:
        check()
        found = False
        for passage in source['passages']:
            check()
            found = found or passage['evidence_id'] in linked
        if not found:
            raise ReportError('conflict')
    check()


def admitted_source_passages(context):
    """Flatten admitted passages with each document's exact source provenance."""
    sources = (context['sources'] if context['schema_version'] == 2 else
               [dict(context['binding']['source'], passages=context['passages'])])
    return [dict(p, source_revision=source['source_revision'], source_name=source['source_name'],
                 reference='[[source:%s]]' % p['evidence_id'])
            for source in sources for p in source['passages']]


def validate_context(value, binding=None, *, tick=None):
    """SDK-cold whole-object preflight, including late native records/passages."""
    try:
        version = integer(value['schema_version'], 1, 2)
        exact(value, ('schema_version', 'binding', 'graph', 'native_records', 'native_manifest') +
              (('sources',) if version == 2 else ('source_text', 'passages')))
        if version == 2:
            refusal_deadline = time.monotonic() + 15
            def check():
                if tick is not None:
                    tick()
                if time.monotonic() >= refusal_deadline:
                    raise ReportError('timeout')
        else:
            check = lambda: None
        check()
        if version == 2:
            source_set = exact(value['binding']['source_set'], ('schema_version', 'source_revisions'))
            integer(source_set['schema_version'], 1, 1)
            if type(source_set['source_revisions']) is not list or not 1 <= len(source_set['source_revisions']) <= 100:
                raise ValueError
        elif 'source_set' in value['binding']:
            raise ValueError
        validate_binding(value['binding'])
        if binding is not None and value['binding'] != binding:
            raise ValueError
        if len(encoded(value)) > CONTEXT_BYTES:
            raise ReportError('result_too_large')
        from .durable_preparation_host import validate_graph
        graph = validate_graph(value['graph'], value['binding']['display_graph_id'])
        if graph != value['graph']:
            raise ValueError
        if version == 2:
            sources = value['sources']
            if type(sources) is not list or not 1 <= len(sources) <= 100:
                raise ValueError
        else:
            sources = [dict(value['binding']['source'], text=value['source_text'], passages=value['passages'])]
        refs, evidence, revisions = [], set(), set()
        ordered_revisions = []
        total_text = total_passages = 0
        for record in sources:
            check()
            if version == 2:
                exact(record, ('source_revision', 'source_name', 'source_sha256', 'text', 'passages'))
                revision = uuid_string(record['source_revision'])
                text(record['source_name'], 256)
                if revision in revisions:
                    raise ValueError
                revisions.add(revision)
                ordered_revisions.append(revision)
            source = record['text']
            if (type(source) is not str or len(source) > 32768
                    or hashlib.sha256(source.encode('utf-8')).hexdigest() != sha(record['source_sha256'])):
                raise ValueError
            if type(record['passages']) is not list or not 1 <= len(record['passages']) <= 100:
                raise ValueError
            total_text += len(source)
            total_passages += len(record['passages'])
            if total_text > 32768 or total_passages > 100:
                raise ReportError('result_too_large')
            for passage in record['passages']:
                check()
                exact(passage, ('evidence_id', 'start', 'end', 'page', 'excerpt', 'excerpt_sha256'))
                uid = uuid_string(passage['evidence_id'])
                if uid in evidence:
                    raise ValueError
                evidence.add(uid)
                start = integer(passage['start'], 0, len(source))
                end = integer(passage['end'], start + 1, len(source))
                if passage['page'] is not None:
                    integer(passage['page'], 1, 2147483647)
                if (passage['excerpt'] != source[start:end]
                        or sha(passage['excerpt_sha256']) != hashlib.sha256(passage['excerpt'].encode('utf-8')).hexdigest()
                        or not 1 <= len(passage['excerpt'].encode('utf-8')) <= 32768):
                    raise ValueError
                refs.append('source:' + uid)
        if version == 2:
            if {key: sources[0][key] for key in value['binding']['source']} != value['binding']['source']:
                raise ValueError
            if ordered_revisions != source_set['source_revisions']:
                raise ValueError
            _linked_sources(sources, graph, check)
        # Projected facts may contain unrelated retained evidence; only passages
        # actually admitted here can be used as report reference markers.
        from .native_observation_reader import parse_record
        from .native_observations_client import output_names, LOG_BYTES, OUTPUT_BYTES, AGGREGATE_BYTES
        platforms = value['binding']['native']['platforms']
        exact(value['native_records'], platforms)
        manifest = exact(value['native_manifest'], ('schema_version', 'files'))
        integer(manifest['schema_version'], 1, 1)
        names = output_names(platforms)
        if type(manifest['files']) is not list or len(manifest['files']) != len(names):
            raise ValueError
        for file, name in zip(manifest['files'], names):
            check()
            exact(file, ('name', 'size', 'sha256')); sha(file['sha256'])
            integer(file['size'], 0, LOG_BYTES if name.endswith('/actions.jsonl') else OUTPUT_BYTES)
            if file['name'] != name:
                raise ValueError
        if (sum(f['size'] for f in manifest['files']) > AGGREGATE_BYTES
                or digest(manifest) != value['binding']['native']['evidence_sha256']):
            raise ValueError
        for platform, coverage in zip(platforms, value['binding']['coverage']):
            check()
            records = value['native_records'][platform]
            if type(records) is not list or len(records) != coverage['selected_records']:
                raise ValueError
            start = coverage['windows'][0]['offset'] if coverage['windows'] else 0
            for index, record in enumerate(records, start):
                check()
                exact(record, ('index', 'record_sha256', 'raw_json'))
                integer(record['index'], 0, 9999)
                if record['index'] != index or type(record['raw_json']) is not str:
                    raise ValueError
                raw = record['raw_json'].encode('utf-8')
                parse_record(raw)
                if sha(record['record_sha256']) != hashlib.sha256(raw).hexdigest():
                    raise ValueError
                refs.append('native:%s:%d:%s' % (platform, index, record['record_sha256']))
        if refs != value['binding']['reference_keys'] or len(refs) > 2048:
            raise ValueError
        check()
        detached = json.loads(encoded(value))
        check()
        return detached
    except ReportError:
        raise
    except Exception:
        raise ReportError('conflict') from None


@contextmanager
def freeze_context(observations, read_facade, payload, *, deadline=None, tick=None):
    """Keep the native file lease through final authority/snapshot comparison."""
    multiple = type(payload) is dict and payload.get('schema_version') == 2
    if multiple:
        refusal_deadline = time.monotonic() + 15
        deadline = refusal_deadline if deadline is None else min(deadline, refusal_deadline)
        caller_tick = tick
        def selected_tick():
            if caller_tick is not None:
                caller_tick()
            if time.monotonic() >= deadline:
                raise ReportError('timeout')
        tick = selected_tick
        tick()
        payload = validate_payload('plan', payload)
    if tick is not None:
        tick()
    reference = dict(schema_version=1, launch_id=payload['launch_id'], launch_sha256=payload['launch_sha256'],
                     platform='twitter', offset=0, limit=1)
    # Platform membership is validated by existing authority. Select the first
    # requested platform from the authoritative row before accessing any files.
    row = observations.launch._row(reference)
    reference['platform'] = row.request.platforms[0]
    if tick is not None:
        tick()
    row, prep, native, launch = observations._authority(reference)
    if prep.frozen['public'].get('schema_version', 1) == 2:
        declared = [source['source_revision'] for source in prep.frozen['public']['sources']]
        if not multiple or payload['source_revisions'] != declared:
            raise ReportError('conflict')
    if tick is not None:
        tick()
    scope, project, retained = observations.launch.preparation._owned(prep.frozen['public']['source']['source_revision'],
                                                                expected_revision=prep.project_revision)
    retained_sources = [retained]
    if multiple:
        tick()
        if (payload['source_revisions'][0] != prep.frozen['public']['source']['source_revision']
                or project.revision != prep.project_revision):
            raise ReportError('conflict')
        project = deepcopy(project)
        scope = deepcopy(scope)
        retained_sources = [deepcopy(retained)]
        selected_text, selected_passages = len(retained.text), len(retained.passages)
        if selected_text > 32768 or selected_passages > 100:
            raise ReportError('result_too_large')
        for revision in payload['source_revisions'][1:]:
            tick()
            selected_scope, selected_project, selected = observations.launch.preparation._owned(
                revision, expected_revision=prep.project_revision)
            tick()
            if selected_scope != scope or selected_project != project:
                raise ReportError('conflict')
            selected_text += len(selected.text)
            selected_passages += len(selected.passages)
            if selected_text > 32768 or selected_passages > 100:
                raise ReportError('result_too_large')
            retained_sources.append(deepcopy(selected))
        tick()
    from .durable_preparation_host import validate_graph
    def projected():
        if deadline is None:
            return validate_graph(read_facade.graph_data(observations.display_graph_id), observations.display_graph_id)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ReportError('timeout')
        # Keep the existing projection reader's limits and parsing. Only the
        # trusted private transport wait is clipped to the whole report clock.
        from .knowledge_read_facade import KnowledgeReadFacade
        if isinstance(read_facade, KnowledgeReadFacade) and read_facade.has_parent_verification_session():
            from .knowledge_transport import KnowledgeCooperativeAbort
            try:
                graph = read_facade.graph_data(observations.display_graph_id)
            except KnowledgeCooperativeAbort as error:
                raise ReportError(error.code) from None
            if time.monotonic() >= deadline:
                raise ReportError('timeout')
            return validate_graph(graph, observations.display_graph_id)
        if isinstance(read_facade, KnowledgeReadFacade) and read_facade._client_factory is None:
            from .knowledge_transport import KnowledgeProcessClient, KnowledgeCooperativeAbort
            from .knowledge_reader import KnowledgeGraphReader, ReadLimits
            settings = read_facade._settings
            def transport_tick():
                if tick is None:
                    return
                try:
                    tick()
                except (KeyboardInterrupt, SystemExit):
                    raise
                except BaseException as error:
                    raise KnowledgeCooperativeAbort(getattr(error, 'code', 'report_uncertain')) from None
            client = KnowledgeProcessClient(settings.python, settings.bootstrap,
                timeout_seconds=min(15, remaining), child_environment=dict(settings.child_environment),
                cooperative_tick=transport_tick if tick is not None else None)
            try:
                graph = KnowledgeGraphReader(client, scope=dict(settings.scope), graph_id=settings.display_graph_id,
                    limits=ReadLimits()).get_graph_data(settings.display_graph_id)
            except KnowledgeCooperativeAbort as error:
                raise ReportError(error.code) from None
        else:
            # Explicit fixture/trusted reader transports must be cooperative,
            # just as explicit scripted model transports must be cooperative.
            graph = read_facade.graph_data(observations.display_graph_id)
        if time.monotonic() >= deadline:
            raise ReportError('timeout')
        return validate_graph(graph, observations.display_graph_id)
    if tick is not None:
        tick()
    graph = projected()
    if tick is not None:
        tick()
    root = observations.launch.preparation.root / ('sim_' + prep.operation_id.hex)
    if observations._artifacts(prep, root) != prep.receipt:
        raise ReportError('conflict')
    if tick is not None:
        tick()
    with observations.reader.context(root, row.request.platforms, payload['native_windows'], row.receipt['evidence_sha256']) as observed:
        if multiple:
            tick()
            sources, refs = [], []
            total_text = total_passages = 0
            for revision, selected in zip(payload['source_revisions'], retained_sources):
                tick()
                if str(selected.source_revision) != revision or selected.project_id != project.project_id:
                    raise ReportError('conflict')
                total_text += len(selected.text)
                total_passages += len(selected.passages)
                if total_text > 32768 or total_passages > 100:
                    raise ReportError('result_too_large')
                passages = []
                for p in selected.passages:
                    tick()
                    passages.append(dict(evidence_id=str(p.evidence_id), start=p.start, end=p.end, page=p.page,
                                         excerpt=p.excerpt, excerpt_sha256=p.excerpt_sha256))
                    refs.append('source:' + str(p.evidence_id))
                sources.append(dict(source_revision=revision, source_name=selected.name,
                    source_sha256=selected.text_sha256, text=selected.text, passages=passages))
            for platform in row.request.platforms:
                tick()
                for record in observed['records'][platform]:
                    tick()
                    refs.append('native:%s:%d:%s' % (platform, record['index'], record['record_sha256']))
            tick()
        else:
            passages = [dict(evidence_id=str(p.evidence_id), start=p.start, end=p.end, page=p.page,
                             excerpt=p.excerpt, excerpt_sha256=p.excerpt_sha256) for p in retained.passages]
            refs = ['source:' + p['evidence_id'] for p in passages]
            refs += ['native:%s:%d:%s' % (p, r['index'], r['record_sha256'])
                     for p in row.request.platforms for r in observed['records'][p]]
        if len(refs) > 2048:
            raise ReportError('result_too_large')
        binding = dict(display_graph_id=observations.display_graph_id, principal=observations.principal,
            scope=scope.model_dump(mode='json'), project_revision=prep.project_revision,
            source=prep.frozen['public']['source'], preparation=launch['preparation'],
            native=dict(run_id=str(row.run_id), launch_sha256=row.launch_sha256,
                request_fingerprint=row.request.fingerprint, evidence_sha256=row.receipt['evidence_sha256'],
                platforms=list(row.request.platforms)), coverage=observed['coverage'], reference_keys=refs)
        if multiple:
            tick()
            binding['source_set'] = dict(schema_version=1, source_revisions=list(payload['source_revisions']))
        body = dict(binding=binding, graph=graph, native_records=observed['records'], native_manifest=observed['manifest'])
        context = (validate_context(dict(body, schema_version=2, sources=sources), tick=tick) if multiple else
                   validate_context(dict(body, schema_version=1, source_text=retained.text, passages=passages)))
        if tick is not None:
            tick()
        after, current, native_after, final_launch = observations._authority(reference)
        if tick is not None:
            tick()
        if multiple:
            for revision, selected in zip(payload['source_revisions'], retained_sources):
                tick()
                scope_after, project_after, retained_after = observations.launch.preparation._owned(
                    revision, expected_revision=prep.project_revision)
                tick()
                if scope_after != scope or project_after != project or retained_after != selected:
                    raise ReportError('conflict')
            tick()
            retained_after = retained
        else:
            _, _, retained_after = observations.launch.preparation._owned(binding['source']['source_revision'], expected_revision=prep.project_revision)
        if (after != row or current != prep or native_after != native or final_launch != launch
                or retained_after != retained or observations._artifacts(current, root) != current.receipt
                or projected() != graph):
            raise ReportError('conflict')
        if tick is not None:
            tick()
        yield context


def validate_references(prose, context, *, require_native=True):
    """Reference integrity only; no claim of semantic support."""
    if type(prose) is not str or not prose.strip():
        raise ReportError('report_failed')
    allowed = set(context['binding']['reference_keys'])
    marker_pattern = r'(?<!\[)\[\[([^\[\]]+)\]\](?!\])'
    markers = re.findall(marker_pattern, prose)
    if not markers or any(key not in allowed for key in markers):
        raise ReportError('report_failed')
    # Reject partial or nested markers as well as invented well-formed ones.
    remainder = re.sub(marker_pattern, '', prose)
    if '[[' in remainder or ']]' in remainder:
        raise ReportError('report_failed')
    for marker in markers:
        reference_key(marker)
    if require_native and any(context['native_records'].values()) and not any(m.startswith('native:') for m in markers):
        raise ReportError('report_failed')
    return markers
