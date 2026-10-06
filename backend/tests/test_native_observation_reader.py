"""Pure evidence admission. Handcrafted files are parser fixtures, not native qualification."""
import hashlib
import os
from types import SimpleNamespace
import pytest
from app.services.native_observation_reader import NativeObservationReader, parse_log, parse_record
from app.services.native_observations_client import NativeObservationsError, digest, output_names, LINE_BYTES, LOG_BYTES


def outputs(root, twitter=b'{"action_type":"POST","success":true}\r\n{"event_type":"end"}\n', reddit=b'{"unknown":1.0}'):
    (root / 'twitter').mkdir(); (root / 'reddit').mkdir()
    blobs = {'twitter_simulation.db': b'SQLite fixture bytes', 'twitter/actions.jsonl': twitter,
             'reddit_simulation.db': b'other SQLite fixture bytes', 'reddit/actions.jsonl': reddit}
    for name, blob in blobs.items(): (root / name).write_bytes(blob)
    manifest = {'schema_version': 1, 'files': [{'name': name, 'size': len(blobs[name]),
        'sha256': hashlib.sha256(blobs[name]).hexdigest()} for name in output_names(['twitter', 'reddit'])]}
    return manifest


def test_physical_order_full_counts_and_raw_lexemes():
    raw = b'{"event_type":"seed","n":1.0,"e":1E+2,"z":-0,"big":9007199254740993}'
    blob = raw + b'\r\n{"action_type":"POST","success":true}\n{"action_type":"POST","success":false}\n{"action_type":"POST","success":1}\n{"opaque":1}\n'
    first = parse_log(blob, 0, 2); second = parse_log(blob, 2, 2); last = parse_log(blob, 4, 2)
    assert first['total_records'] == 5 and first['next_offset'] == 2
    assert first['records'][0]['raw_json'].encode() == raw
    assert first['records'][0]['record_sha256'] == hashlib.sha256(raw).hexdigest()
    assert first['counts'] == {'event_records': 1, 'action_records': 3, 'successful_action_records': 1, 'failed_action_records': 1}
    assert second['counts'] == last['counts'] == first['counts']
    assert [r['index'] for r in second['records']] == [2, 3] and last['next_offset'] is None
    assert parse_log(blob, 5, 20)['records'] == []
    with pytest.raises(NativeObservationsError) as error: parse_log(blob, 6, 20)
    assert error.value.code == 'invalid_request'
    assert parse_log(b'', 0, 1)['total_records'] == 0


@pytest.mark.parametrize('raw', [b'', b' ', b'[]', b'{"x":NaN}', b'{"x":Infinity}', b'{"x":1e400}',
    b'{"x":1,"x":2}', b'{"x":{"a":1,"a":2}}', b'{"x":"\\ud800"}', b'{"\\udfff":1}', b'\xff',
    b'{"x":' + b'[' * 8 + b'0' + b']' * 8 + b'}'], ids=['empty', 'blank', 'array', 'nan', 'inf', 'overflow', 'dup', 'nested-dup', 'surrogate', 'key-surrogate', 'utf8', 'depth'])
def test_invalid_records_are_never_skipped(raw):
    with pytest.raises(NativeObservationsError) as error: parse_record(raw)
    assert error.value.code == 'evidence_invalid'


def test_depth_boundary_unicode_line_and_no_blank_loss():
    parse_record(b'{"x":' + b'[' * 7 + b'0' + b']' * 7 + b'}')
    raw = '{"unknown":"\u2028\u2029猫😀<script>"}'.encode()
    assert parse_log(raw + b'\n', 0, 1)['records'][0]['raw_json'].encode() == raw
    for blob in [b'{}\n\n', b'{}\n \n', b'{}\n\r\n']:
        with pytest.raises(NativeObservationsError): parse_log(blob, 0, 1)


def test_finite_integer_boundary_and_literal_cr_lf_admission():
    finite = b'{"integer":1' + b'0' * 308 + b'}'
    assert parse_record(finite)[0].encode() == finite
    for raw in [b'{"integer":1' + b'0' * 309 + b'}', b'{}\r', b'{\r"a":1}', b'{\n"a":1}']:
        with pytest.raises(NativeObservationsError) as error: parse_record(raw)
        assert error.value.code == 'evidence_invalid'
    raw = b'{"a":"\\r\\n","integer":9007199254740993}'
    assert parse_record(raw)[0].encode() == raw
    assert parse_log(b'{}\r\n', 0, 1)['records'][0]['raw_json'] == '{}'
    with pytest.raises(NativeObservationsError): parse_log(b'{}\r', 0, 1)


@pytest.mark.parametrize('blob', [b' ' * (LINE_BYTES + 1), b'{}\n' * 10001, b' ' * (LOG_BYTES + 1)], ids=['line', 'records', 'log'])
def test_explicit_bounds(blob):
    with pytest.raises(NativeObservationsError) as error: parse_log(blob, 0, 1)
    assert error.value.code == 'result_too_large'


def test_verified_manifest_pages_and_input_preservation(tmp_path):
    manifest = outputs(tmp_path)
    before = {f['name']: (tmp_path / f['name']).read_bytes() for f in manifest['files']}
    reader = NativeObservationReader()
    with reader.page(tmp_path, ('twitter', 'reddit'), 'twitter', 1, 1, digest(manifest)) as page:
        assert page['manifest'] == manifest and page['total_records'] == 2
        assert page['records'][0]['index'] == 1 and page['counts']['action_records'] == 1
    with reader.page(tmp_path, ('twitter', 'reddit'), 'reddit', 0, 20, digest(manifest)) as page:
        assert page['records'][0]['raw_json'] == '{"unknown":1.0}'
    assert all((tmp_path / name).read_bytes() == blob for name, blob in before.items())


@pytest.mark.parametrize('kind', ['missing', 'manifest', 'malformed-other', 'hardlink', 'oversize'], ids=['missing', 'manifest', 'other-log', 'hardlink', 'large-db'])
def test_files_are_untrusted_even_with_receipt(tmp_path, kind):
    manifest = outputs(tmp_path, reddit=b'{"x":NaN}' if kind == 'malformed-other' else b'{}')
    evidence = digest(manifest)
    path = tmp_path / 'twitter_simulation.db'
    if kind == 'missing': path.unlink()
    if kind == 'manifest': evidence = '0' * 64
    if kind == 'hardlink': os.link(path, tmp_path / 'alias.db')
    if kind == 'oversize':
        with path.open('r+b') as file: file.truncate(67108865)
    with pytest.raises(NativeObservationsError) as error:
        with NativeObservationReader().page(tmp_path, ('twitter', 'reddit'), 'twitter', 0, 20, evidence): pass
    assert error.value.code == ('result_too_large' if kind == 'oversize' else 'evidence_invalid')


def test_descriptor_stability_checked_after_authority_callback(tmp_path):
    manifest = outputs(tmp_path)
    with pytest.raises(NativeObservationsError) as error:
        with NativeObservationReader().page(tmp_path, ('twitter', 'reddit'), 'twitter', 0, 20, digest(manifest)):
            (tmp_path / 'twitter/actions.jsonl').write_bytes(b'{}')
    assert error.value.code == 'evidence_invalid'


def test_reparse_ancestor_denied_before_open(tmp_path, monkeypatch):
    import app.services.native_observation_reader as module
    manifest = outputs(tmp_path)
    original = module._safe
    def safe(path, directory=False):
        if path == tmp_path: raise NativeObservationsError('evidence_invalid')
        return original(path, directory)
    monkeypatch.setattr(module, '_safe', safe)
    monkeypatch.setattr(module.os, 'open', lambda *args: pytest.fail('must deny before descriptor open'))
    with pytest.raises(NativeObservationsError):
        with NativeObservationReader().page(tmp_path, ('twitter', 'reddit'), 'twitter', 0, 20, digest(manifest)): pass


def test_replacement_identity_denied_and_all_descriptors_closed(tmp_path, monkeypatch):
    import app.services.native_observation_reader as module
    manifest = outputs(tmp_path)
    original, closed = module._safe, []
    calls = {}
    close = module.os.close
    def safe(path, directory=False):
        details = original(path, directory)
        calls[path] = calls.get(path, 0) + 1
        if not directory and calls[path] > 1:
            return SimpleNamespace(st_dev=details.st_dev, st_ino=details.st_ino + 1, st_size=details.st_size,
                st_mtime_ns=details.st_mtime_ns, st_ctime_ns=details.st_ctime_ns, st_nlink=details.st_nlink)
        return details
    def tracked(descriptor): closed.append(descriptor); close(descriptor)
    monkeypatch.setattr(module, '_safe', safe); monkeypatch.setattr(module.os, 'close', tracked)
    with pytest.raises(NativeObservationsError):
        with NativeObservationReader().page(tmp_path, ('twitter', 'reddit'), 'twitter', 0, 20, digest(manifest)): pass
    assert len(closed) == 4 and len(set(closed)) == 4


@pytest.mark.parametrize('mutation', ['none', 'path', 'descriptor'], ids=['portable-time', 'path-race', 'fd-race'])
def test_cross_api_timestamp_difference_preserves_same_api_race_checks(tmp_path, monkeypatch, mutation):
    import app.services.native_observation_reader as module
    manifest = outputs(tmp_path)
    original = module.os.fstat
    calls = {}
    def fstat(descriptor):
        details = original(descriptor)
        calls[descriptor] = calls.get(descriptor, 0) + 1
        return SimpleNamespace(st_dev=details.st_dev, st_ino=details.st_ino, st_size=details.st_size,
            st_mtime_ns=details.st_mtime_ns + (1 if mutation == 'descriptor' and calls[descriptor] >= 3 else 0),
            st_ctime_ns=details.st_ctime_ns + 1000000000, st_nlink=details.st_nlink, st_mode=details.st_mode)
    monkeypatch.setattr(module.os, 'fstat', fstat)
    def read():
        with NativeObservationReader().page(tmp_path, ('twitter', 'reddit'), 'twitter', 0, 20, digest(manifest)) as page:
            assert page['total_records'] == 2
            if mutation == 'path':
                path = tmp_path / 'twitter_simulation.db'
                details = path.stat()
                os.utime(path, ns=(details.st_atime_ns, details.st_mtime_ns + 1000000000))
    if mutation == 'none':
        read()
    else:
        with pytest.raises(NativeObservationsError) as error: read()
        assert error.value.code == 'evidence_invalid'


@pytest.mark.parametrize('kind', ['symlink', 'reparse', 'junction'], ids=['symlink', 'reparse', 'junction'])
def test_safe_file_inspection_refuses_each_link_kind(tmp_path, monkeypatch, kind):
    import stat
    from pathlib import Path
    import app.services.native_observation_reader as module
    path = tmp_path / 'evidence'; path.write_bytes(b'{}')
    original = Path.lstat
    def lstat(value):
        details = original(value)
        if value != path: return details
        return SimpleNamespace(st_mode=stat.S_IFLNK if kind == 'symlink' else details.st_mode,
            st_file_attributes=getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400) if kind == 'reparse' else 0,
            st_nlink=details.st_nlink)
    monkeypatch.setattr(Path, 'lstat', lstat)
    monkeypatch.setattr(module.os.path, 'isjunction', lambda value: kind == 'junction' and value == path, raising=False)
    with pytest.raises(NativeObservationsError) as error: module._safe(path)
    assert error.value.code == 'evidence_invalid'
