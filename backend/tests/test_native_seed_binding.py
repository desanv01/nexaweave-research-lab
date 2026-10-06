"""Main-owned SDK-cold factory gate: seed refusal precedes model construction."""
import json
from uuid import uuid4

import pytest


class ModelFactoryReached(Exception):
    pass


def prepared_binding(tmp_path, posts, actors=None):
    from mirofish_execution.native_owned_binding import NativeOwnedSessionFactory, _manifest
    from mirofish_execution.native_run_contracts import NativeRunRequest
    root = tmp_path / 'prepared'
    root.mkdir()
    state = dict(status='ready', graph_id='seed-graph', simulation_id='seed-sim',
                 enable_twitter=True, enable_reddit=True)
    config = dict(graph_id='seed-graph', simulation_id='seed-sim',
                  agent_configs=actors if actors is not None else [dict(agent_id=0), dict(agent_id=1)])
    if posts is not None:
        config['event_config'] = dict(initial_posts=posts)
    names = ('state.json', 'simulation_config.json', 'source_grounding.json',
             'twitter_profiles.csv', 'reddit_profiles.json')
    data = {'state.json': state, 'simulation_config.json': config,
            'source_grounding.json': {}, 'reddit_profiles.json': []}
    for name, value in data.items():
        (root/name).write_bytes(json.dumps(value, ensure_ascii=True).encode('ascii'))
    (root/'twitter_profiles.csv').write_bytes(b'user_id,name\n0,Alpha\n1,Beta\n')
    original = {name: (root/name).read_bytes() for name in names}
    project = uuid4()
    calls = []
    def models():
        calls.append('constructed')
        raise ModelFactoryReached()
    factory = NativeOwnedSessionFactory(str(root.resolve()), 'seed-graph', 'seed-sim',
        'owner', str(project), 1, ('twitter','reddit'), 7, 1, 'b'*64, models)
    request = NativeRunRequest.from_wire(dict(schema_version=1,principal='owner',
        project_id=project,project_revision=1,simulation_id='seed-sim',run_id=uuid4(),
        artifact_sha256=_manifest(root.resolve(),names,2*1024*1024),runtime_sha256='b'*64,
        platforms=('twitter','reddit'),seed=7,max_rounds=1))
    return root, factory, request, calls, original


@pytest.mark.parametrize('posts', [None, [], [
    {'poster_agent_id':0,'content':'相同 seed\n技术 <script>literal</script>'},
    {'poster_agent_id':1,'content':'seed B'},
    {'poster_agent_id':0,'content':'相同 seed\n技术 <script>literal</script>'},
    {'poster_agent_id':1,'content':'seed D'},
    {'poster_agent_id':0,'content':'最後 🚀'}]], ids=['absent','empty','interleaved-repeated'])
def test_admitted_seed_batch_reaches_factory_without_mutating_inputs(tmp_path, posts):
    root, factory, request, calls, original = prepared_binding(tmp_path, posts)
    with pytest.raises(ModelFactoryReached):
        factory.create_session(request)
    assert calls == ['constructed']
    assert not (root/'.native_prepared_start_claim').exists()
    assert {p.name for p in root.iterdir()} == set(original)
    assert all((root/name).read_bytes()==value for name,value in original.items())


@pytest.mark.parametrize('bad', [
    None, [], {}, {'content':'missing actor'}, {'poster_agent_id':0},
    {'poster_agent_id':True,'content':'bool'}, {'poster_agent_id':-1,'content':'negative'},
    {'poster_agent_id':0.0,'content':'float'}, {'poster_agent_id':2,'content':'unadmitted'},
    {'poster_agent_id':'0','content':'string'}, {'poster_agent_id':0,'content':False},
    {'poster_agent_id':0,'content':'\ud800'}, {'poster_agent_id':0,'content':'\udfff'},
], ids=['null','array','empty-object','no-actor','no-content','bool-id','negative-id',
        'float-id','unknown-id','string-id','bool-content','high-surrogate','low-surrogate'])
def test_later_malformed_seed_rejected_before_model_factory_or_native_claim(tmp_path, bad):
    posts=[{'poster_agent_id':0,'content':'valid earlier seed'},bad]
    root, factory, request, calls, original = prepared_binding(tmp_path, posts)
    with pytest.raises(ValueError):
        factory.create_session(request)
    assert calls == []
    assert not (root/'.native_prepared_start_claim').exists()
    assert {p.name for p in root.iterdir()} == set(original)
    assert all((root/name).read_bytes()==value for name,value in original.items())


@pytest.mark.parametrize('actors', [[{'agent_id':True},{'agent_id':1}],
    [{'agent_id':0},{'agent_id':0}], [{'agent_id':0},{'agent_id':2}]],
    ids=['bool-actor','duplicate-actor','noncontiguous-actor'])
def test_nonempty_plan_requires_actual_contiguous_admitted_actor_ids(tmp_path, actors):
    root,factory,request,calls,original=prepared_binding(tmp_path,[{'poster_agent_id':0,'content':'seed'}],actors)
    with pytest.raises(ValueError):
        factory.create_session(request)
    assert calls == [] and not (root/'.native_prepared_start_claim').exists()
    assert all((root/name).read_bytes()==value for name,value in original.items())
