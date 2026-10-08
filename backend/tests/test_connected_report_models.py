"""Optional synchronous request admission without changing report defaults."""
from types import SimpleNamespace
import pytest
from app.services import report_models as models
from app.services.connected_report_client import DEFAULT_LIMITS, ReportError


def transport(events):
    def create(**kwargs):
        events.append(('sdk', kwargs))
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content='bounded answer'), finish_reason='stop')])
    return SimpleNamespace(max_retries=0, chat=SimpleNamespace(
        completions=SimpleNamespace(create=create)), close=lambda:None)


def boundary(events, *, admission=None, deadline=100):
    return models.RequestBoundary(transport(events), dict(DEFAULT_LIMITS, max_calls=2),
        deadline, lambda:None, lambda:events.append(('first',)), request_admission=admission)


def test_default_report_first_marker_once_and_optional_admission_each_request(monkeypatch):
    monkeypatch.setattr(models.time, 'monotonic', lambda:0)
    legacy=[]
    old=boundary(legacy)
    old.create(messages=[]); old.create(messages=[])
    assert [event[0] for event in legacy]==['first','sdk','sdk']
    events=[]
    current=boundary(events, admission=lambda number:events.append(('admit',number)))
    current.create(messages=[]); current.create(messages=[])
    assert [event[:2] if event[0]=='admit' else (event[0],) for event in events]==[
        ('admit',1),('first',),('sdk',),('admit',2),('sdk',)]
    with pytest.raises(ReportError): current.create(messages=[])
    assert current.calls==2 and len(events)==5


def test_authority_admission_recomputes_remaining_sdk_timeout(monkeypatch):
    clock=[0]
    monkeypatch.setattr(models.time, 'monotonic', lambda:clock[0])
    events=[]
    def admit(number):
        assert number==1
        clock[0]=8
    current=boundary(events, admission=admit, deadline=10)
    current.create(messages=[])
    assert events[-1][1]['timeout']==2
    assert events[-1][1]['stream'] is False


@pytest.mark.parametrize('failure',['revoked','expired'])
def test_admission_failure_fences_all_inherited_retries_before_transport(monkeypatch,failure):
    clock=[0]
    monkeypatch.setattr(models.time, 'monotonic', lambda:clock[0])
    events=[]; admissions=[]
    def admit(number):
        admissions.append(number)
        if failure=='revoked': raise ReportError('unauthorized')
        clock[0]=10
    current=boundary(events, admission=admit, deadline=10)
    for _ in range(2):
        with pytest.raises(ReportError): current.create(messages=[])
    assert current.failed and current.calls==0 and admissions==[1] and events==[]


def test_factory_passes_hook_and_refuses_invalid_hook_before_constructing_sdk(monkeypatch):
    monkeypatch.setattr(models.time, 'monotonic', lambda:0)
    events=[]; constructed=[]; admitted=[]
    def factory(**kwargs):
        constructed.append(kwargs)
        return transport(events)
    configured=models.BoundedReportModelFactory(factory,'scripted',dict(DEFAULT_LIMITS))
    with pytest.raises(ReportError):
        configured.create(deadline=100,checkpoint=lambda:None,first_call=lambda:None,
                          request_admission=False)
    assert constructed==[]
    model=configured.create(deadline=100,checkpoint=lambda:None,first_call=lambda:None,
                            request_admission=lambda number:admitted.append(number))
    model.client.create(messages=[])
    assert admitted==[1] and len(constructed)==1
