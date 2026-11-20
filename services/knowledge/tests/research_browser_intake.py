"""Fixture custody and independent receipt checks for real browser source intake."""
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from uuid import UUID, uuid4


def _format_files():
    from nexaweave_storage.pdf import extract_pdf
    from test_pdf_source import pdf_bytes
    from test_document_source_integration import _package, _paragraph

    pdf = pdf_bytes(['PDF assertions 猫', 'Second PDF page 雪'])
    markdown = '# Markdown assertions\n\nRetained **Markdown** evidence 猫.\n'
    plain = 'Plain text assertions 雪 😀\n'
    docx = _package(_paragraph('Office assertions 猫') +
                    '<w:tbl><w:tr><w:tc>' + _paragraph('Table evidence 雪') + '</w:tc></w:tr></w:tbl>')
    return [('original.pdf', 'pdf', pdf, extract_pdf(pdf).text),
            ('notes.md', 'text', markdown, markdown),
            ('notes.txt', 'text', plain, plain),
            ('office.docx', 'docx', docx, 'Office assertions 猫\n\nTable evidence 雪')]


def _verify_retained(selected, store, project_id, document, revision, receipt):
    name, kind, content, expected = document
    binary = content.encode('utf-8') if isinstance(content, str) else content
    input_digest = sha256(binary).hexdigest()
    assert receipt['extraction']['format'] == kind
    assert receipt['extraction']['input_hash_verified'] is True
    assert receipt['extraction']['input_sha256'] == input_digest
    assert receipt['graph_ingestion_executed'] is False
    record = store.get_source('owner', project_id, revision)
    assert record.name == name and record.text == expected and record.passages
    assert record.text_sha256 == sha256(expected.encode('utf-8')).hexdigest()
    source = receipt['source']
    assert source['project_id'] == str(project_id) and source['source_revision'] == str(revision)
    assert source['source_name'] == name and source['text_sha256'] == record.text_sha256
    if kind == 'pdf':
        assert receipt['schema_version'] == 2 and receipt['binary_retained'] is True
        assert receipt['binary']['sha256'] == input_digest and receipt['binary']['byte_length'] == len(binary)
        original = store.get_original('owner', project_id, revision)
        assert original.content == binary and original.source == record
        assert [p.page for p in record.passages] == [1, 2]
    else:
        assert receipt['binary_retained'] is False
    if kind == 'docx':
        assert [p.excerpt for p in record.passages] == ['Office assertions 猫', 'Table evidence 雪']
    passages = []
    for passage in record.passages:
        assert expected[passage.start:passage.end] == passage.excerpt
        assert sha256(passage.excerpt.encode('utf-8')).hexdigest() == passage.excerpt_sha256
        passages.append(dict(evidence_id=str(passage.evidence_id), start=passage.start, end=passage.end,
                             page=passage.page, excerpt=passage.excerpt, excerpt_sha256=passage.excerpt_sha256))
    passage_fields = ('evidence_id', 'start', 'end', 'page', 'excerpt_sha256')
    assert [{key: item[key] for key in passage_fields} for item in receipt['passages']] == [
        {key: item[key] for key in passage_fields} for item in passages]
    selected.records.append(record)
    selected.snapshots.append(dict(source_revision=str(revision), source_name=name,
                                   source_sha256=record.text_sha256, text=expected, passages=passages))
    selected.receipts.append(receipt)
    selected.revisions.append(str(revision))


def retain_four_formats(factory, directory, prep, scope, *, browser_intake):
    from nexaweave_storage import SourceStore
    from test_source_library_http_integration import _host, _exchange, _payload

    # Keep the pre-existing split between the locked knowledge and backend executables.
    assert os.environ.get('KNOWLEDGE_INTEGRATION') == '1'
    python = Path(os.environ['KNOWLEDGE_PYTHON'])
    backend = Path(os.environ['NEXAWEAVE_WORKBENCH_BACKEND_PYTHON'])
    bootstrap = Path(os.environ['KNOWLEDGE_BOOTSTRAP_SCRIPT'])
    assert python.is_absolute() and python.is_file()
    assert backend.is_absolute() and backend.is_file() and backend == Path(sys.executable).absolute()
    assert python != backend, 'separate locked KG/backend executable paths required'
    assert not bootstrap.resolve().is_relative_to(Path(sys.prefix).resolve())
    assert bootstrap.is_absolute() and bootstrap.is_file() and 'site-packages' in bootstrap.parts
    assert bootstrap.name == 'read_bootstrap.py' and bootstrap.parent.name == 'nexaweave_knowledge'
    assert all(bootstrap.with_name(name).is_file() for name in
               ('source_bootstrap.py', 'source_ingestion_bootstrap.py', 'read_session_bootstrap.py'))

    documents = _format_files()
    upload_root = directory / 'uploaded-documents'
    upload_root.mkdir()
    selected = SimpleNamespace(records=[], snapshots=[], receipts=[], pdf=documents[0][2], revisions=[],
                               upload_root=upload_root, intake=None)
    files = []
    for name, _, content, _ in documents:
        binary = content.encode('utf-8') if isinstance(content, str) else content
        (upload_root / name).write_bytes(binary)
        files.append(dict(name=name, size=len(binary), sha256=sha256(binary).hexdigest()))
    if browser_intake:
        assert all(0 < item['size'] <= 4500 for item in files)
        selected.intake = SimpleNamespace(documents=documents, files=files, requests=[])
        return selected

    http_root = directory / 'source-http'
    http_root.mkdir()
    store = SourceStore(factory)
    with _host(http_root, scope, prep.display_graph_id) as (port, token, _):
        for document in documents:
            name, kind, content, _ = document
            revision = uuid4()
            payload = _payload(revision, name, content, kind)
            if kind == 'pdf':
                payload['schema_version'] = 2
            status, headers, reply = _exchange(port, 'POST', _route(prep.display_graph_id, kind), payload, token=token)
            assert status == 200 and reply['success'] is True
            assert headers['Cache-Control'] == 'no-store' and headers['X-Content-Type-Options'] == 'nosniff'
            _verify_retained(selected, store, scope.project_id, document, revision, reply['data'])
    return selected


def connect_sources(selected):
    if selected.intake is not None:
        return dict(intake=selected.intake.files)
    return dict(sources=[dict(source_revision=source['source_revision'], source_name=source['source_name'])
                         for source in selected.snapshots],
                pdf=dict(source_revision=selected.revisions[0], size=len(selected.pdf),
                         sha256=sha256(selected.pdf).hexdigest()))


def _route(graph, kind):
    action = 'retain-original' if kind == 'pdf' else 'retain'
    return f'/api/source/{action}/{graph}'


def guard_intake(session, request, pairs, wire):
    intake = session.selected.intake
    if intake is None or request.method != 'POST' or not request.path.startswith('/api/source/retain'):
        return
    graph = session.prep.display_graph_id
    assert session.active == 'connect' and session.single_platform is None
    assert request.host == f'127.0.0.1:{session.port}' and not request.query_string
    assert request.headers.getlist('Origin') == ['http://127.0.0.1:4317']
    assert request.headers.getlist('Authorization') == ['Bearer ' + session.token]
    assert (request.mimetype == 'application/json' and request.content_length is not None
            and 0 < request.content_length <= wire and not request.headers.get('Content-Encoding')
            and not request.headers.get('Transfer-Encoding'))
    raw = request.stream.read(request.content_length + 1)
    assert len(raw) == request.content_length
    request.stream = BytesIO(raw)
    body = json.loads(raw, object_pairs_hook=pairs)
    with session.lock:
        assert session.active == 'connect' and len(intake.requests) < len(intake.files)
        index = len(intake.requests)
        kind = intake.documents[index][1]
        spec = intake.files[index]
        assert request.path == _route(graph, kind)
        assert body['schema_version'] == (2 if kind == 'pdf' else 1)
        assert (body['source_name'], body['format'], body['input_sha256']) == (spec['name'], kind, spec['sha256'])
        revision = UUID(body['source_revision'])
        assert str(revision) == body['source_revision'] and revision not in intake.requests
        intake.requests.append(revision)


def intake_mutation_allowed(session, path, response):
    intake = session.selected.intake
    if intake is None or session.active != 'connect':
        return False
    graph = session.prep.display_graph_id
    limit = {_route(graph, 'pdf'): 1, _route(graph, 'text'): 3}.get(path, 0)
    return (0 < session.mutations[path] <= limit and response.status_code == 200
            and response.headers.get('Cache-Control') == 'no-store'
            and response.headers.get('X-Content-Type-Options') == 'nosniff')


def bind_intake(session, events, proof):
    from nexaweave_storage import NotFound

    selected = session.selected
    intake = selected.intake
    if intake is None:
        return
    graph = session.prep.display_graph_id
    project_id = UUID(session.prep.scope_dto['project_id'])
    routes = [_route(graph, document[1]) for document in intake.documents]
    protected = [(path, data) for _, path, data in events if path in routes]
    assert [path for path, _ in protected] == routes
    assert len(intake.requests) == 4
    store = session.prep.sources
    for document, revision, (_, receipt) in zip(intake.documents, intake.requests, protected, strict=True):
        _verify_retained(selected, store, project_id, document, revision, receipt)
        try:
            store.get_source('other', project_id, revision)
        except NotFound:
            pass
        else:
            raise RuntimeError('research_browser_foreign_intake_accepted')
    assert proof['intake_receipts'] == [{key: src[key] for key in
        ('source_revision', 'source_name', 'source_sha256')} for src in selected.snapshots]
