"""Closed stage orders and phase limits for the owned research browser."""
ONTOLOGY = ('ontology-review', 'ontology-plan', 'ontology-execute', 'ontology-recover')
PAIRED_STAGES = ('connect', *ONTOLOGY, 'preparation-plan', 'preparation-start', 'preparation-ready',
          'native-start', 'native-ready', 'report-start', 'report-read',
          'pdf-read-after-restart', 'close')
PAIRED_SECONDS = (60, 60, 60, 120, 60, 60, 60, 60, 60, 180, 180, 120, 60, 15)
FAILURE_PHASES = frozenset(('browser-launch', 'page-load', 'graph-connect',
    'source-catalog', 'source-inspect', 'pdf-metadata', 'pdf-download', 'protocol', 'close', *ONTOLOGY))
LAUNCH_ERRORS = frozenset(('launch-timeout', 'executable-missing', 'permission-refused',
    'sandbox-refused', 'browser-closed', 'context-create-failed', 'page-create-failed', 'launch-unknown',
    'namespace-refused', 'runtime-library-missing', 'crashpad-database-missing',
    'profile-lock-refused', 'runtime-resource-refused', 'browser-process-sigtrap',
    'browser-process-sigabrt', 'browser-process-exit1', 'cdp-protocol-refused'))
_SINGLE_STAGES = PAIRED_STAGES[:PAIRED_STAGES.index('report-start')] + ('close',)
_SINGLE_SECONDS = PAIRED_SECONDS[:PAIRED_STAGES.index('report-start')] + (PAIRED_SECONDS[-1],)


_FORK = ('native-fork-review', 'native-fork-start', 'native-fork-ready')


def browser_stages(twitter_only, *, fork=False):
    if fork:
        return _SINGLE_STAGES[:-1] + _FORK + ('close',), _SINGLE_SECONDS[:-1] + (60, 60, 60, 15)
    return (_SINGLE_STAGES, _SINGLE_SECONDS) if twitter_only else (PAIRED_STAGES, PAIRED_SECONDS)


def browser_route(session, stage):
    graph = session.prep.display_graph_id
    if stage == 'pdf-read-after-restart':
        return '/api/source/original/' + graph + '/' + session.selected.revisions[0]
    return {
        'ontology-review': '/api/source/ontology/' + graph,
        'ontology-plan': '/api/source/ingestion/plan/' + graph,
        'ontology-execute': '/api/source/ingestion/execute/' + graph,
        'ontology-recover': '/api/source/ingestion/operation/' + graph + '/' +
            (session.ontology_plan or {}).get('operation_id', ''),
        'preparation-plan': '/api/preparation/' + graph + '/plan',
        'preparation-start': '/api/preparation/' + graph + '/start',
        'preparation-ready': '/api/preparation/' + graph + '/status',
        'native-start': '/api/native-launch/start/' + graph,
        'native-ready': '/api/native-launch/status/' + graph,
        'native-fork-review': '/api/native-launch/plan/' + graph,
        'native-fork-start': '/api/native-launch/start/' + graph,
        'native-fork-ready': '/api/native-launch/status/' + graph,
        'report-start': '/api/connected-report/start/' + graph,
        'report-read': '/api/connected-report/read/' + graph,
    }.get(stage)


def browser_mutation(session, path):
    graph = session.prep.display_graph_id
    native = '/api/native-launch/'
    report = '/api/connected-report/'
    stage = session.active
    allowed = {
        'ontology-execute': (session._route('ontology-execute'),),
        'preparation-plan': (browser_route(session, 'preparation-plan'),),
        'preparation-start': (browser_route(session, 'preparation-start'),),
        'native-start': (native + 'plan/' + graph, native + 'start/' + graph),
        'report-start': (report + 'plan/' + graph, report + 'start/' + graph),
    }
    key = path
    if session.fork and path.startswith(native):
        # Each launch gets one Plan and one explicit Start, in separate stages.
        allowed.update({
            'native-fork-review': (native + 'fork-preview/' + graph, native + 'plan/' + graph),
            'native-fork-start': (native + 'start/' + graph,),
        })
        key = stage, path
    session.mutations[key] = session.mutations.get(key, 0) + 1
    return path in allowed.get(stage, ()) and session.mutations[key] == 1


def fork_result(session, stage, events, reply, require):
    """Bind each driver reply to ordered responses from the protected Flask host."""

    prefix = '/api/native-launch/'
    graph = session.prep.display_graph_id
    native = [(path, value) for _, path, value in events if path.startswith(prefix)]
    route = browser_route(session, stage)
    values = [value for _, value in native]
    paths = [path for path, _ in native]
    if stage == 'native-fork-review':
        require(paths == [prefix + 'fork-preview/' + graph, route])
        declaration, plan = values
        parent = session.references['native-ready']
        require(declaration['schema_version'] == plan['schema_version'] == 3
                and plan['state'] == 'planned' and plan['receipt'] is None
                and plan['request']['run_id'] == declaration['launch_id'] != parent['launch_id']
                and plan['checkpoint'] == declaration['checkpoint']
                and plan['checkpoint']['source_run_id'] == parent['launch_id']
                and plan['checkpoint']['checkpoint_sha256'] == parent['evidence_sha256']
                and plan['checkpoint']['mode'] == 'fork'
                and plan['checkpoint']['intervention']['kind'] == 'swap_active_actor_v1'
                and all(plan['preparation'][key] == value for key, value in declaration['preparation'].items()))
        result = plan
    elif stage == 'native-fork-start':
        require(paths == [route] and reply['proof'] == {'dispatches': 1})
        result, = values
    else:
        require(stage == 'native-fork-ready' and paths == [route, route])
        result, parent = values
        known = session.references['native-ready']
        require(parent['request']['run_id'] == known['launch_id']
                and parent['launch_sha256'] == known['launch_sha256']
                and parent['receipt']['evidence_sha256'] == known['evidence_sha256'])
    session._reference(stage, result, reply)
    if stage != 'native-fork-review':
        require(session.references[stage] == session.references['native-fork-review'])
    return (result, parent) if stage == 'native-fork-ready' else result
