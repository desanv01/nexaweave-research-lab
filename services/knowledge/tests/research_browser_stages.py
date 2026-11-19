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


def browser_stages(twitter_only):
    return (_SINGLE_STAGES, _SINGLE_SECONDS) if twitter_only else (PAIRED_STAGES, PAIRED_SECONDS)
