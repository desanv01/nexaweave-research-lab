"""Pure late-context and report-copy boundaries before any SDK construction."""
from copy import deepcopy
import hashlib
from types import SimpleNamespace
from uuid import UUID
import pytest
from test_connected_report_client import fixture_context,public_result


def test_parent_copy_binds_prefix_and_detects_changed_original(monkeypatch,tmp_path):
    from app.services.connected_followup_context import freeze_parent
    from app.services.connected_report_client import digest,encoded
    parent_dto,text=public_result('completed')
    source=tmp_path/UUID(parent_dto['report_id']).hex/'output'/parent_dto['report_id']
    source.mkdir(parents=True)
    original=text.encode()
    for file in parent_dto['manifest']['files']:
        raw=original if file['name'].endswith('.md') else encoded(dict(schema_version=1))
        (source/file['name']).write_bytes(raw)
        file.update(size=len(raw),sha256=hashlib.sha256(raw).hexdigest())
    parent_dto['receipt']['manifest_sha256']=digest(parent_dto['manifest'])
    row=SimpleNamespace(report_id=UUID(parent_dto['report_id']),plan_sha256=parent_dto['plan_sha256'],
        state='completed',receipt=parent_dto['receipt'],manifest=parent_dto['manifest'],
        frozen=dict(identity={k:parent_dto[k] for k in ('schema_version','report_id','binding','options',
            'context_sha256','source_projection_sha256','model_label','limits','ceiling_microusd')},
            context=fixture_context()))
    parent=SimpleNamespace(_row=lambda _:row,_reauthorize=lambda value,**kw:value,
        _output_root=lambda _:source)
    _,binding,context,provenance,bundle=freeze_parent(parent,parent_dto['report_id'],parent_dto['plan_sha256'])
    assert bundle['full_report.md']==original
    assert provenance['file_sha256']==hashlib.sha256(original).hexdigest()
    assert provenance['prefix_characters']==len(text)
    (source/'full_report.md').write_bytes(b'changed')
    from nexaweave_execution.followup_contracts import FollowupError
    with pytest.raises(FollowupError):
        freeze_parent(parent,parent_dto['report_id'],parent_dto['plan_sha256'])
