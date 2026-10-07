"""Cold preflight and actual SDK boundary unit cases; lazy inherited imports."""
from copy import deepcopy
from types import SimpleNamespace
import time
import pytest
from test_connected_report_client import fixture_context


def test_actual_budget_parser_report_proof_and_cross_domain_refusal():
    from uuid import UUID, uuid5, NAMESPACE_URL
    from test_connected_report_client import report_budget_wire
    from nexaweave_execution.budget import _reservation, BudgetUncertain
    from nexaweave_execution.report_contracts import ReportBudgetReceipt, budget_episode
    from nexaweave_execution.native_launch_contracts import native_budget_episode
    wire = report_budget_wire()
    operation, attempt, group = UUID(wire['operation_id']), UUID(wire['attempt_id']), 'mf1_' + 'a'*64
    row = (UUID(int=20), operation, wire['fingerprint'], 4, 'settled', attempt, group,
           budget_episode(group, operation), [], wire, None)
    saved = _reservation(row)
    assert type(saved.receipt) is ReportBudgetReceipt and saved.receipt.json_value() == wire
    assert saved.episode_id == budget_episode(group, operation) and not saved.evidence_ids
    for episode in (uuid5(NAMESPACE_URL, 'mirofish:episode:v1:' + group + ':' + str(operation)),
                    native_budget_episode(group, operation)):
        foreign = list(row); foreign[7] = episode
        with pytest.raises(BudgetUncertain):
            _reservation(tuple(foreign))
    for index, value in ((1, UUID(int=13)), (2, 'f'*64), (5, UUID(int=100)), (8, [str(UUID(int=50))])):
        malformed = list(row); malformed[index] = value
        with pytest.raises(BudgetUncertain):
            _reservation(tuple(malformed))
    substitutes = [dict(group_id=group, episode_id=str(row[7]), fingerprint=wire['fingerprint'], evidence_ids=[]),
        dict(kind='prepared_budget_v1', operation_id=str(operation), attempt_id=str(attempt),
             fingerprint=wire['fingerprint'], artifact_sha256='b'*64),
        dict(kind='native_run_budget_v1', operation_id=str(operation), attempt_id=str(attempt),
             fingerprint=wire['fingerprint'], launch_sha256='c'*64, native_receipt={})]
    for substitute in substitutes:
        foreign = list(row); foreign[9] = substitute
        with pytest.raises(BudgetUncertain):
            _reservation(tuple(foreign))


def test_legacy_no_init_tool_caller_keeps_defaults_and_original_tool_result(monkeypatch):
    from app.services import report_agent
    calls = []
    def forbidden(*args, **kwargs):
        pytest.fail('legacy no-init tool call constructed a model')
    monkeypatch.setattr(report_agent, 'LLMClient', forbidden)
    def search(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(to_text=lambda: 'Original legacy search result')
    agent = report_agent.ReportAgent.__new__(report_agent.ReportAgent)
    agent.graph_id = 'legacy-graph'
    agent.zep_tools = SimpleNamespace(quick_search=search)
    assert 'connected_context' not in vars(agent) and 'connected_instruction' not in vars(agent)
    assert agent.neutral_mode is False and agent.connected_context is None and agent.connected_instruction == ''
    assert agent._execute_tool('quick_search', {'query': 'legacy query'}) == 'Original legacy search result'
    assert calls == [dict(graph_id='legacy-graph', query='legacy query', limit=10)]
    assert set(agent._define_tools()) == {'insight_forge', 'panorama_search', 'quick_search', 'interview_agents'}
    assert 'recorded_native_events' not in agent.VALID_TOOL_NAMES


@pytest.mark.parametrize('change', [lambda c: c['passages'][-1].update(excerpt='invented'),
    lambda c: c['native_records']['reddit'][-1].update(record_sha256='a'*64),
    lambda c: c['native_records']['reddit'][-1].update(raw_json='{"a":1,"a":2}'),
    lambda c: c['binding']['reference_keys'].append('source:00000000-0000-0000-0000-000000000099'),
    lambda c: c['native_manifest']['files'][-1].update(size=True),
    lambda c: c['binding']['coverage'][-1].update(selected_records=0),
    lambda c: c.update(source_text='changed')])
def test_whole_context_refuses_late_corruption_before_dependencies(change):
    from app.services.connected_report_context import validate_context
    from app.services.connected_report_client import ReportError
    context = fixture_context(); change(context)
    with pytest.raises(ReportError):
        validate_context(context)


def test_native_tool_distinct_exact_window_and_no_slicing():
    from app.services.connected_report_tools import RecordedNativeEvents
    from app.services.connected_report_client import ReportError
    tool = RecordedNativeEvents(fixture_context())
    text = tool(dict(platform='reddit', offset=0, limit=1))
    assert 'Recorded native observations' in text and '[[native:reddit:0:' in text
    for payload in (dict(platform='reddit', offset=0, limit=2), dict(platform='reddit', offset=True, limit=1),
                    dict(platform='reddit', offset=0, limit=21), dict(platform='reddit', offset=0, limit=1, source='fake')):
        with pytest.raises(ReportError):
            tool(payload)


def test_lexical_selection_over_actual_byte_projection_keeps_nulls_scope_order_and_bytes():
    import json
    from uuid import UUID
    from app.services.knowledge_read_facade import KnowledgeReadFacade, ReadHostSettings
    from app.services.connected_report_tools import lexical_selector
    from app.services.connected_report_client import encoded, ReportError
    context = fixture_context()
    bound = deepcopy(context['binding']['scope'])
    uid = lambda number: str(UUID(int=number))
    def fact(number, kind, name, content):
        return dict(schema_version=1, provider_id=uid(number), scope=deepcopy(bound), kind=kind,
            name=name, fact=None if kind == 'node' else content, summary=content if kind == 'node' else None,
            source_node_id=None if kind == 'node' else uid(10), target_node_id=None if kind == 'node' else uid(11),
            episode_ids=[uid(800)], evidence_ids=[uid(900)], labels=['Entity', 'Person'] if kind == 'node' else [],
            valid_at=None, invalid_at=None, expired_at=None, created_at=None, attributes={}, score=None)
    facts = dict(node=[fact(10, 'node', 'Rain', 'market'), fact(11, 'node', 'Cloud', 'rain')],
                 edge=[fact(20, 'edge', 'OBSERVES', 'Rain market'), fact(21, 'edge', 'FOLLOWS', 'cloud')])
    original_wire_facts = encoded(facts)
    class ByteClient:
        def __init__(self):
            self.calls = []
        def call(self, raw):
            request = json.loads(raw)
            self.calls.append(request)
            assert request['scope'] == bound and request['payload']['cursor'] is None
            return encoded(dict(version=1, request_id=request['request_id'], ok=True,
                result=dict(schema_version=1, facts=facts[request['payload']['kind']], next_cursor=None)))
    byte_client = ByteClient()
    settings = ReadHostSettings('unused-python', 'unused-bootstrap', 'unused-token', 'owner',
        context['binding']['display_graph_id'], bound, {})
    facade = KnowledgeReadFacade(settings, client_factory=lambda: byte_client)
    graph = facade.graph_data(settings.display_graph_id)
    assert all(node['fact'] is None for node in graph['nodes'])
    assert all(edge['summary'] is None for edge in graph['edges'])
    assert graph['nodes'][0]['name'] == 'Rain' and graph['nodes'][0]['summary'] == 'market'
    assert graph['edges'][0]['fact'] == 'Rain market'
    before = encoded(graph)
    context['graph'] = graph
    select = lexical_selector(context)
    expected = dict(nodes=[(10, 'node', 2), (11, 'node', 1)],
        edges=[(20, 'edge', 2), (21, 'edge', 0)],
        both=[(10, 'node', 2), (20, 'edge', 2), (11, 'node', 1), (21, 'edge', 0)])
    for scope, ordering in expected.items():
        arguments = dict(graph_id=settings.display_graph_id, bound_scope=deepcopy(bound),
                         query='rain market', scope=scope, limit=10)
        result = select(**arguments)
        assert result == [dict(id=uid(number), kind=kind, score=score, bound_scope=bound)
                          for number, kind, score in ordering]
        assert select(**arguments) == result
    limited = select(graph_id=settings.display_graph_id, bound_scope=bound, query='rain market', scope='both', limit=2)
    assert [item['id'] for item in limited] == [uid(10), uid(20)]
    with pytest.raises(ReportError) as denied:
        select(graph_id=settings.display_graph_id, bound_scope=dict(bound, graph_id=uid(99)), query='rain', scope='both', limit=2)
    assert denied.value.code == 'unauthorized'
    assert encoded(graph) == before and encoded(facts) == original_wire_facts
    assert [call['payload']['kind'] for call in byte_client.calls] == ['node', 'edge']


def test_reference_integrity_is_not_semantic_support():
    from app.services.connected_report_context import validate_references
    from app.services.connected_report_client import ReportError
    context = fixture_context(); key = context['binding']['reference_keys'][-1]
    assert validate_references('Observation [[' + key + ']]', context) == [key]
    for prose in ('no evidence', '[[native:reddit:0:' + 'f'*64 + ']]', '[[' + key, '[[made_up]]'):
        with pytest.raises(ReportError):
            validate_references(prose, context)


@pytest.mark.parametrize('key_index', [0, -1], ids=['source', 'native'])
@pytest.mark.parametrize('opening,closing', [('[[[', ']]]'), ('[[[', ']]'), ('[[', ']]]'), ('[[[[', ']]]]')],
                         ids=['triple', 'nested-opening', 'nested-closing', 'quadruple'])
def test_nested_reference_pairs_are_refused_even_beside_valid_markers(key_index, opening, closing):
    from app.services.connected_report_context import validate_references
    from app.services.connected_report_client import ReportError
    context = fixture_context()
    nested = opening + context['binding']['reference_keys'][key_index] + closing
    valid = 'Observation [[' + context['binding']['reference_keys'][-1] + ']]'
    for prose in (nested, valid + ' ' + nested):
        with pytest.raises(ReportError) as error:
            validate_references(prose, context)
        assert error.value.code == 'report_failed'


def test_sdk_counter_first_possible_request_timeout_tokens_and_retry_fence():
    from app.services.report_models import RequestBoundary
    from app.services.connected_report_client import ReportError, DEFAULT_LIMITS
    calls, first = [], []
    def create(**kwargs):
        calls.append(kwargs)
        if len(calls) == 2:
            # Provider compatibility failure cannot trigger another SDK call.
            error = RuntimeError('secret provider body'); error.status_code = 400
            error.body = dict(error=dict(param='response_format'))
            raise error
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='ok'), finish_reason='stop')])
    transport = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    boundary = RequestBoundary(transport, DEFAULT_LIMITS, time.monotonic()+30, lambda: None, lambda: first.append(True))
    assert boundary.create(model='local', messages=[dict(role='user', content='hi')]).choices[0].message.content == 'ok'
    assert first == [True] and 0 < calls[0]['timeout'] <= 15 and calls[0]['max_tokens'] == 4096 and calls[0]['stream'] is False
    with pytest.raises(ReportError) as error:
        boundary.create(model='local', messages=[])
    assert not hasattr(error.value, 'status_code') and error.value.code == 'report_uncertain'
    with pytest.raises(ReportError):
        boundary.create(model='local', messages=[])
    assert len(calls) == 2 and boundary.calls == 2


def test_oversize_input_and_unconfigured_factory_make_zero_requests():
    from app.services.report_models import RequestBoundary, BoundedReportModelFactory
    from app.services.connected_report_client import ReportError, DEFAULT_LIMITS
    limits = dict(DEFAULT_LIMITS, max_input_bytes=128)
    def forbidden(**kwargs):
        pytest.fail('preflight constructed or requested a transport')
    transport = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=forbidden)))
    boundary = RequestBoundary(transport, limits, time.monotonic()+30, lambda: None, forbidden)
    with pytest.raises(ReportError):
        boundary.create(model='local', messages=[dict(role='user', content='x'*129)])
    assert boundary.calls == 0
    factory = BoundedReportModelFactory(None, 'local', limits)
    assert not factory.configured()
    with pytest.raises(ReportError):
        factory.create(deadline=time.monotonic()+30, checkpoint=lambda: None, first_call=forbidden)
