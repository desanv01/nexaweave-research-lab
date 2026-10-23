"""Pure strict follow-up DTO, chain, and publication-DAG negatives."""
from copy import deepcopy
import base64
import hashlib
import json
from uuid import UUID
import pytest

from test_connected_report_client import public_result, fixture_context


def planned():
    from app.services.connected_followup_client import DEFAULT_LIMITS, digest, empty_head
    parent,_=public_result('completed')
    report=dict(report_id=parent['report_id'],plan_sha256=parent['plan_sha256'],
        receipt_sha256=parent['receipt_sha256'],manifest_sha256=digest(parent['manifest']),
        full_report_sha256=parent['manifest']['files'][2]['sha256'])
    native=parent['binding']
    binding=dict(display_graph_id=native['display_graph_id'],principal=native['principal'],
        scope=native['scope'],report=report,native_binding=native)
    history=dict(head_sha256=empty_head(report['report_id'],report['plan_sha256']),
        total_completed=0,window_start=1,pairs=[])
    provenance=dict(file_sha256=report['full_report_sha256'],prefix_sha256=report['full_report_sha256'],
        prefix_characters=20,total_characters=20,truncated=False)
    context=fixture_context()
    identity=dict(schema_version=1,turn_id=str(UUID(int=80)),binding=binding,
        options=dict(question='What does the record say?',output_language='en',expected_history_sha256=None),
        history=history,report_context=provenance,context_sha256=digest(context),
        source_projection_sha256=digest(context['graph']),model_label='scripted-local',
        limits=dict(DEFAULT_LIMITS),ceiling_microusd=4)
    result=dict(identity,plan_sha256=digest(identity),
        authorization=dict(model_calls_enabled=False,budget_configured=True),
        state='planned',progress=dict(stage='planned',percent=0,completed_sections=0,total_sections=0),
        workflow=None,receipt=None,receipt_sha256=None,manifest=None,
        published_history_head_sha256=None,cleanup=dict(known=True,pending=False,owner_thread_alive=False),
        cancel_requested=False,error_code=None)
    return result


def request(result,kind=None):
    value=dict(schema_version=1,turn_id=result['turn_id'],plan_sha256=result['plan_sha256'])
    if kind is not None: value['kind']=kind
    return value


def completed():
    from app.services.connected_followup_client import digest, next_head, FILE_NAMES, encoded
    result=planned()
    answer='Answer [['+result['binding']['native_binding']['reference_keys'][0]+']]'
    answer_sha=hashlib.sha256(answer.encode()).hexdigest()
    report=result['binding']['report']
    conversation=dict(schema_version=1,report_id=report['report_id'],
        report_plan_sha256=report['plan_sha256'],total_completed=1,
        pairs=[dict(turn_id=result['turn_id'],plan_sha256=result['plan_sha256'],
            ordinal=1,question=result['options']['question'],answer=answer,
            answer_sha256=answer_sha,predecessor_head_sha256=result['history']['head_sha256'],
            receipt_sha256=None,published_head_sha256=None)])
    raws=[b'{}',answer.encode(),encoded(conversation),b'{}',b'{}',b'{}']
    manifest=dict(schema_version=1,files=[dict(name=name,size=len(raw),sha256=hashlib.sha256(raw).hexdigest())
        for name,raw in zip(FILE_NAMES,raws)])
    receipt=dict(schema_version=1,turn_id=result['turn_id'],plan_sha256=result['plan_sha256'],
        parent_report_id=report['report_id'],parent_report_plan_sha256=report['plan_sha256'],
        parent_report_receipt_sha256=report['receipt_sha256'],context_sha256=result['context_sha256'],
        history_head_sha256=result['history']['head_sha256'],ordinal=1,
        manifest_sha256=digest(manifest),output_language='en',
        reference_integrity='validated',semantic_support_status='not_reviewed')
    receipt_sha=digest(receipt)
    result.update(state='completed',progress=dict(stage='completed',percent=100,completed_sections=0,total_sections=0),
        manifest=manifest,receipt=receipt,receipt_sha256=receipt_sha,
        published_history_head_sha256=next_head(report['report_id'],report['plan_sha256'],
            result['history']['head_sha256'],1,result['turn_id'],result['plan_sha256'],
            result['options']['question'],manifest['files'][1]['sha256'],receipt_sha))
    return result,answer,raws


def test_plan_start_and_completed_external_proof():
    from app.services.connected_followup_client import validate_result, validate_read, validate_download
    value=planned(); binding=value['binding']; report=binding['report']
    plan=dict(schema_version=1,turn_id=value['turn_id'],report_id=report['report_id'],
        report_plan_sha256=report['plan_sha256'],**value['options'])
    assert validate_result(value,binding['display_graph_id'],binding['scope'],plan,'plan','owner')==value
    finished,answer,raws=completed(); ref=request(finished)
    assert validate_result(finished,binding['display_graph_id'],binding['scope'],ref,'status','owner')==finished
    assert validate_read(dict(schema_version=1,turn=finished,content=answer),
        binding['display_graph_id'],binding['scope'],ref,'owner')['content']==answer
    artifact=finished['manifest']['files'][1]
    download=dict(schema_version=1,turn_id=finished['turn_id'],plan_sha256=finished['plan_sha256'],
        receipt_sha256=finished['receipt_sha256'],artifact=dict(artifact,mime='text/markdown',
        content_base64=base64.b64encode(raws[1]).decode()))
    assert validate_download(download,request(finished,'answer'),finished)==download


@pytest.mark.parametrize('mutation',[
    lambda x:x.update(turn_id=True),
    lambda x:x.update(question=''),
    lambda x:x.update(question='\ud800'),
    lambda x:x.update(output_language='fr'),
    lambda x:x.update(expected_history_sha256='F'*64),
    lambda x:x.update(extra='browser history'),
])
def test_plan_rejects_malformed_or_browser_authored_history(mutation):
    from app.services.connected_followup_client import validate_payload, FollowupError
    value=planned(); report=value['binding']['report']
    payload=dict(schema_version=1,turn_id=value['turn_id'],report_id=report['report_id'],
        report_plan_sha256=report['plan_sha256'],**value['options'])
    mutation(payload)
    with pytest.raises(FollowupError) as error: validate_payload('plan',payload)
    assert error.value.code=='invalid_request'


@pytest.mark.parametrize('field', ['receipt_sha256','published_history_head_sha256','manifest'])
def test_completed_external_proof_rejects_mutation(field):
    from app.services.connected_followup_client import validate_result, FollowupError
    value,_,_=completed(); bad=deepcopy(value)
    bad[field]=None
    with pytest.raises(FollowupError):
        validate_result(bad,value['binding']['display_graph_id'],value['binding']['scope'],
            request(value),'status','owner')


def test_history_page_distinguishes_old_page_from_current_head():
    from app.services.connected_followup_client import (validate_history_page, next_head, empty_head)
    value=planned(); report=value['binding']['report']
    answer='Older answer'; ah=hashlib.sha256(answer.encode()).hexdigest()
    root=empty_head(report['report_id'],report['plan_sha256'])
    pair=dict(turn_id=value['turn_id'],plan_sha256=value['plan_sha256'],ordinal=1,
        question=value['options']['question'],answer_sha256=ah,answer_prefix=answer,
        answer_prefix_sha256=ah,answer_characters=len(answer),admitted_characters=len(answer),
        truncated=False,receipt_sha256='a'*64,predecessor_head_sha256=root,
        published_head_sha256=next_head(report['report_id'],report['plan_sha256'],root,1,
            value['turn_id'],value['plan_sha256'],value['options']['question'],ah,'a'*64))
    payload=dict(schema_version=1,report_id=report['report_id'],
                 report_plan_sha256=report['plan_sha256'],before_ordinal=2)
    page=dict(schema_version=1,report_id=report['report_id'],report_plan_sha256=report['plan_sha256'],
        binding=value['binding'],head_sha256='b'*64,total_completed=2,before_ordinal=2,pairs=[pair])
    assert validate_history_page(page,payload)==page
    bad=deepcopy(page); bad['pairs'][0]['receipt_sha256']=None
    from app.services.connected_followup_client import FollowupError
    with pytest.raises(FollowupError): validate_history_page(bad,payload)


def test_current_conversation_proof_must_be_external_and_prior_proof_complete():
    from app.services.connected_followup_client import FollowupError
    from nexaweave_execution.followup_contracts import validate_conversation
    value,answer,raws=completed()
    identity={key:value[key] for key in ('schema_version','turn_id','binding','options',
        'history','report_context','context_sha256','source_projection_sha256',
        'model_label','limits','ceiling_microusd')}
    document=json.loads(raws[2])
    assert validate_conversation(document,identity,answer)==document
    for field in ('receipt_sha256','published_head_sha256'):
        bad=deepcopy(document); bad['pairs'][-1][field]='a'*64
        with pytest.raises(FollowupError): validate_conversation(bad,identity,answer)
    changed=deepcopy(document); changed['pairs'][-1]['answer']='changed'
    with pytest.raises(FollowupError): validate_conversation(changed,identity,answer)
    next_identity=deepcopy(identity)
    next_identity['history']['total_completed']=1
    next_identity['history']['head_sha256']='a'*64
    previous=deepcopy(document['pairs'][0])
    next_doc=deepcopy(document)
    next_doc['total_completed']=2
    next_doc['pairs']=[previous,dict(previous,ordinal=2,predecessor_head_sha256='a'*64)]
    with pytest.raises(FollowupError): validate_conversation(next_doc,next_identity,answer)


def test_full_conversation_worst_case_escape_bound_refuses_before_budget():
    from app.services.followup_process import reserve_conversation
    from app.services.connected_followup_client import FollowupError,digest
    value=planned()
    identity={key:value[key] for key in ('schema_version','turn_id','binding','options',
        'history','report_context','context_sha256','source_projection_sha256',
        'model_label','limits','ceiling_microusd')}
    identity['history']=dict(identity['history'],total_completed=999)
    prior=[dict(turn_id=value['turn_id'],plan_sha256=value['plan_sha256'],ordinal=i,
        question='old question',answer='x'*3000,answer_sha256='a'*64,
        predecessor_head_sha256='b'*64,receipt_sha256='c'*64,
        published_head_sha256='d'*64) for i in range(1,1000)]
    with pytest.raises(FollowupError) as error: reserve_conversation(identity,prior)
    assert error.value.code=='result_too_large'


def test_completed_thousand_history_page_survives_new_turn_limit():
    from app.services.connected_followup_client import validate_history_page, FollowupError
    from app.services.followup_process import reserve_conversation
    value=planned(); report=value['binding']['report']
    payload=dict(schema_version=1,report_id=report['report_id'],
        report_plan_sha256=report['plan_sha256'],before_ordinal=1)
    page=dict(schema_version=1,report_id=report['report_id'],
        report_plan_sha256=report['plan_sha256'],binding=value['binding'],
        head_sha256='a'*64,total_completed=1000,before_ordinal=1,pairs=[])
    assert validate_history_page(page,payload)==page
    identity={key:value[key] for key in ('schema_version','turn_id','binding','options',
        'history','report_context','context_sha256','source_projection_sha256',
        'model_label','limits','ceiling_microusd')}
    identity['history']=dict(identity['history'],total_completed=1000)
    with pytest.raises(FollowupError) as error:
        reserve_conversation(identity,[{}]*1000)
    assert error.value.code=='result_too_large'
