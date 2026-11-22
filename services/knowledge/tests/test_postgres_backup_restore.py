"""One opt-in logical restore; Main qualifies tools/resources before execution.

Windows only: OwnedProcess supplies pre-execution Job membership. The private
root's ACL/quota and PostgreSQL server disk envelope are operator qualifications,
not properties established by Python's socket guard or TemporaryDirectory.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
from uuid import UUID, uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
import pytest

from nexaweave_storage import NotFound, ProjectStore, SourceStore, canonical_payload, migrate
from nexaweave_storage.pdf import extract_pdf
from test_project_store import snapshot

pytestmark = pytest.mark.postgres
ROOT = Path(__file__).resolve().parents[3]
ARCHIVE_CAP = 32 * 1024 * 1024
DIAGNOSTIC_CAP = 1024 * 1024
SOURCE_ADMISSION_CAP = 64 * 1024 * 1024  # admission only, not a runtime quota
WORK_SECONDS = 480
TOOL_SECONDS = 60


class ExerciseError(RuntimeError):
    """Fixed codes only: never include libpq diagnostics, DSNs or PDF bytes."""


def _require(condition, code):
    if not condition:
        raise ExerciseError(code)


def _receipt(event, **fields):
    print(json.dumps(dict(event=event, **fields), sort_keys=True), flush=True)


def _tool_path(kind):
    prefix = 'PROJECT_STORE_PG_' + kind.upper()
    raw = os.environ.get(prefix, '')
    path = Path(raw)
    expected = os.environ.get(prefix + '_SHA256', '')
    _require(path.is_absolute() and path.is_file() and not path.is_symlink(), 'tool_path_required')
    _require(path.name.lower() == 'pg_' + kind + '.exe', 'fixed_native_tool_required')
    _require(len(expected) == 64 and all(c in '0123456789abcdef' for c in expected),
             'tool_sha256_required')
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(65536), b''):
            digest.update(block)
    _require(digest.hexdigest() == expected, 'native_tool_identity_mismatch')
    return path.resolve()


def _admit():
    _require(os.name == 'nt', 'windows_job_qualification_required')
    for key in ('PROJECT_STORE_POSTGRES_INTEGRATION', 'PROJECT_STORE_POSTGRES_BACKUP_RESTORE',
                'PROJECT_STORE_BACKUP_RESTORE_RESOURCES_QUALIFIED'):
        _require(os.environ.get(key) == '1', 'explicit_restore_opt_in_required')
    _require(not any(key.upper().startswith('PG') for key in os.environ), 'ambient_libpq_refused')
    settings = conninfo_to_dict(os.environ.get('PROJECT_STORE_POSTGRES_TEST_DSN', ''))
    _require(settings.get('host') == '127.0.0.1' and settings.get('port') == '15432'
             and settings.get('dbname') == 'mirofish_operations_test'
             and settings.get('user') == 'mirofish_fixture' and bool(settings.get('password'))
             and not set(settings) - {'host', 'port', 'dbname', 'user', 'password', 'connect_timeout'},
             'approved_maintenance_fixture_required')
    password = settings['password']
    _require(len(password) <= 1024 and not any(c in password for c in '\x00\r\n'),
             'disposable_password_shape_required')
    private_root = Path(os.environ.get('PROJECT_STORE_BACKUP_RESTORE_PRIVATE_ROOT', ''))
    _require(private_root.is_absolute() and private_root.is_dir() and not private_root.is_symlink(),
             'qualified_private_root_required')
    private_root = private_root.resolve()
    _require(not private_root.is_relative_to(ROOT), 'private_root_must_be_outside_checkout')
    return settings, private_root, _tool_path('dump'), _tool_path('restore')


def _owner_type():
    spec = importlib.util.spec_from_file_location('_pg_restore_owned', ROOT / 'backend/app/utils/owned_process.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.OwnedProcess


def _native(owner_type, executable, arguments, directory, env, deadline, archive=None):
    """Only this bounded writer writes archive bytes; no native --file output.

    Diagnostic bytes are counted and discarded, not retained or printed. Readers
    stop on overflow; the controller then kills the owned Job, releasing pipes.
    No unbounded communicate(), log file, or polling-based file-size limit.
    """
    _require(time.monotonic() < deadline, 'work_deadline')
    owner = owner_type()
    failed = threading.Event()
    threads = []
    counts = [0, 0]
    output = None
    primary = None
    cleanup_failed = False
    try:
        if archive is not None:
            output = archive.open('xb')

        def drain(pipe, index, cap, sink):
            try:
                while True:
                    block = pipe.read(65536)
                    if not block:
                        break
                    if len(block) > cap - counts[index]:
                        failed.set()
                        return
                    if sink is not None:
                        sink.write(block)
                    counts[index] += len(block)
            except BaseException:
                failed.set()

        process = owner.start(subprocess.Popen, [str(executable), *arguments],
            cwd=str(directory), env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0,
            shell=False, close_fds=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        threads = [threading.Thread(target=drain, args=(process.stdout, 0,
                       ARCHIVE_CAP if output is not None else DIAGNOSTIC_CAP, output), daemon=True),
                   threading.Thread(target=drain, args=(process.stderr, 1, DIAGNOSTIC_CAP, None), daemon=True)]
        for thread in threads:
            thread.start()
        end = min(deadline, time.monotonic() + TOOL_SECONDS)
        while True:
            _require(not failed.is_set(), 'native_output_or_reader_failure')
            _require(time.monotonic() < end, 'native_tool_timeout')
            if process.poll() is not None and not any(t.is_alive() for t in threads):
                break
            failed.wait(min(0.02, max(0, end - time.monotonic())))
        # Readers may set failure after the loop's first check but before its
        # completion check. Both have finished now; do not accept a late error.
        _require(not failed.is_set(), 'native_output_or_reader_failure')
        _require(process.returncode == 0, 'native_tool_nonzero')
    except BaseException as error:
        primary = error
    finally:
        try:
            owner.stop(threads)
            _require(owner.closed and owner.tree_empty and owner.job is None, 'native_owner_not_closed')
        except BaseException:
            cleanup_failed = True
        if output is not None:
            # Never close a buffered sink while a reader could hold its lock.
            # An unjoined reader is an explicit cleanup failure, not a pass.
            if any(thread.is_alive() for thread in threads):
                cleanup_failed = True
            else:
                try:
                    output.close()
                except BaseException:
                    cleanup_failed = True
        _receipt('pg_restore_native_cleanup', tool=executable.name, closed=not cleanup_failed,
                 output_bytes=counts[0], diagnostic_bytes=counts[1],
                 primary_error=(str(primary) if type(primary) is ExerciseError else
                                'native_operation_failed' if primary is not None else None))
    if cleanup_failed:
        # Distinct receipt preserves cleanup failure even with a primary error.
        raise ExerciseError('native_cleanup_failed') from None
    if primary is not None:
        raise primary
    if archive is not None:
        _require(0 < counts[0] <= ARCHIVE_CAP, 'empty_or_oversize_archive')


class Databases:
    def __init__(self, settings, deadline):
        self.settings = dict(settings)
        self.deadline = deadline
        self.created = []
        self.connections = []
        self.endpoint = None
        self.maintenance_oid = None
        self.proofs = {}

    def connect(self, name, *, cleanup=False):
        _require(cleanup or time.monotonic() < self.deadline, 'work_deadline')
        settings = dict(self.settings, dbname=name, connect_timeout=3)
        conn = psycopg.connect(**settings, autocommit=True)
        self.connections.append(conn)
        try:
            conn.execute("SET statement_timeout = '5s'" if cleanup else "SET statement_timeout = '30s'")
            conn.execute("SET lock_timeout = '2s'" if cleanup else "SET lock_timeout = '5s'")
            conn.execute("SET idle_in_transaction_session_timeout = '10s'")
            return conn
        except BaseException:
            conn.close()
            raise

    def identity(self, conn, name, oid=None):
        row = conn.execute("SELECT current_database(),current_user,inet_server_addr()::text,"
                           "inet_server_port(),oid FROM pg_database WHERE datname=current_database()").fetchone()
        _require(row is not None and row[0] == name and row[1] == 'mirofish_fixture', 'database_identity')
        if self.endpoint is not None:
            _require(row[2:4] == self.endpoint, 'database_endpoint_mismatch')
        if oid is not None:
            _require(row[4] == oid, 'database_oid_mismatch')
        return row

    def maintenance(self, *, cleanup=False):
        conn = self.connect('mirofish_operations_test', cleanup=cleanup)
        try:
            row = self.identity(conn, 'mirofish_operations_test', self.maintenance_oid)
            if self.endpoint is None:
                self.endpoint, self.maintenance_oid = row[2:4], row[4]
                _require(180000 <= conn.info.server_version < 190000, 'postgres_18_required')
                allowed = conn.execute("SELECT rolcreatedb OR rolsuper FROM pg_roles WHERE rolname=current_user").fetchone()
                _require(allowed is not None and allowed[0], 'fixture_createdb_permission_required')
            return conn
        except BaseException:
            conn.close()
            raise

    def create(self, name):
        with self.maintenance() as conn:
            _require(conn.execute('SELECT oid FROM pg_database WHERE datname=%s', (name,)).fetchone() is None,
                     'database_collision')
            # If CREATE's outcome is unknown, do not adopt/drop by prefix. Emit
            # the attempted name for Main; an uncertain leftover is a failure.
            entry = {'name': name, 'oid': None, 'created': False}
            self.created.append(entry)
            _receipt('pg_restore_database_attempt', **entry)
            conn.execute(sql.SQL('CREATE DATABASE {} WITH TEMPLATE template0 OWNER mirofish_fixture').format(sql.Identifier(name)))
            entry['created'] = True
            row = conn.execute("SELECT oid,pg_get_userbyid(datdba) FROM pg_database WHERE datname=%s", (name,)).fetchone()
            _require(row is not None and row[1] == 'mirofish_fixture', 'created_database_identity')
            entry['oid'] = row[0]
            _receipt('pg_restore_database_created', **entry)
            return entry

    def factory(self, entry):
        def connect():
            conn = self.connect(entry['name'])
            try:
                self.identity(conn, entry['name'], entry['oid'])
                # No outstanding identity transaction reaches a real store.
                conn.autocommit = False
                self.proofs[entry['name']] = self.proofs.get(entry['name'], 0) + 1
                return conn
            except BaseException:
                conn.close()
                raise
        return connect

    def empty(self, entry):
        with self.factory(entry)() as conn:
            rows = conn.execute("SELECT nspname FROM pg_namespace WHERE nspname NOT IN "
                                "('public','information_schema') AND nspname NOT LIKE 'pg_%'").fetchall()
            _require(not rows, 'target_not_empty')
            count = conn.execute("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                                 "WHERE n.nspname='public'").fetchone()[0]
            _require(count == 0, 'public_namespace_not_empty')

    def cleanup(self):
        failures = []
        for conn in self.connections:
            try:
                conn.close()
            except BaseException:
                failures.append('connection_close')
        for entry in reversed(self.created):
            if not entry['created'] or entry['oid'] is None:
                failures.append('database_creation_outcome_unknown:' + entry['name'])
                continue
            try:
                with self.maintenance(cleanup=True) as conn:
                    row = conn.execute("SELECT oid,pg_get_userbyid(datdba) FROM pg_database WHERE datname=%s",
                                       (entry['name'],)).fetchone()
                    _require(row == (entry['oid'], 'mirofish_fixture'), 'cleanup_database_identity')
                    conn.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(entry['name'])))
                    _require(conn.execute('SELECT 1 FROM pg_database WHERE datname=%s',
                                          (entry['name'],)).fetchone() is None, 'database_drop_not_confirmed')
                _receipt('pg_restore_database_dropped', name=entry['name'], oid=entry['oid'])
            except BaseException:
                failures.append('database_drop:' + entry['name'])
        return failures


def _passfile(directory, settings, entries):
    def escape(value):
        return value.replace('\\', '\\\\').replace(':', '\\:')
    path = directory / 'native.pgpass'
    # Windows protection comes from the prequalified private-root inherited ACL,
    # not the POSIX mode argument. No password is written before qualification.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
        for entry in entries:
            stream.write(':'.join(escape(value) for value in (
                '127.0.0.1', '15432', entry['name'], 'mirofish_fixture', settings['password'])) + '\n')
    return path


def _native_environment(directory, passfile):
    # No application DSN, provider keys, user profile, service file, proxy or
    # arbitrary PGOPTIONS inherited by a native executable.
    env = {key: value for key, value in os.environ.items()
           if key.upper() in {'SYSTEMROOT', 'WINDIR', 'SYSTEMDRIVE', 'LANG', 'LC_ALL'}}
    env.update(HOME=str(directory), USERPROFILE=str(directory), TMP=str(directory),
               TEMP=str(directory), TMPDIR=str(directory), PGPASSFILE=str(passfile),
               PGCONNECT_TIMEOUT='3', PGCLIENTENCODING='UTF8', PGAPPNAME='nw_w82_restore')
    return env


def _readback(factory, expected_project, expected_original, declarations, binary):
    owner, project = expected_project.principal, expected_project.project_id
    revision = expected_original.source.source_revision
    projects, sources = ProjectStore(factory), SourceStore(factory)
    current = projects.get(owner, project)
    _require(current == expected_project and current.revision == 1, 'project_revision_readback')
    _require(projects.get(owner, project, revision=1) == expected_project, 'explicit_revision_readback')
    _require(projects.history(owner, project) == [expected_project], 'project_history_readback')
    _, _, digest = canonical_payload(current.snapshot, current.evidence, current.display_id)
    _require(digest == current.digest, 'project_digest_readback')
    source = sources.get_source(owner, project, revision)
    _require(source == expected_original.source, 'source_record_readback')
    listed = sources.list_sources(owner, project)
    _require(len(listed) == 1, 'source_count_readback')
    metadata = listed[0]
    for field in ('project_id', 'source_revision', 'name', 'text_sha256', 'byte_length',
                  'codepoint_length', 'recorded_at'):
        _require(getattr(metadata, field) == getattr(source, field), 'source_metadata_readback')
    _require(source.project_id == project and source.source_revision == revision, 'source_identity_readback')
    _require(source.text_sha256 == hashlib.sha256(source.text.encode('utf-8')).hexdigest()
             and source.byte_length == len(source.text.encode('utf-8'))
             and source.codepoint_length == len(source.text), 'source_digest_readback')
    _require(len(source.passages) == len(declarations), 'passage_count_readback')
    for passage, declared in zip(source.passages, declarations, strict=True):
        _require(passage.evidence_id == UUID(declared['evidence_id'])
                 and passage.project_id == project and passage.source_revision == revision
                 and (passage.start, passage.end, passage.page) ==
                     (declared['start'], declared['end'], declared['page']), 'passage_binding_readback')
        _require(passage.excerpt == source.text[passage.start:passage.end]
                 and passage.excerpt_sha256 == hashlib.sha256(passage.excerpt.encode()).hexdigest(),
                 'passage_digest_readback')
        resolved = sources.resolve_evidence(owner, project, passage.evidence_id)
        _require((resolved.principal, resolved.project_id, resolved.source_revision, resolved.evidence_id) ==
                 (owner, project, revision, passage.evidence_id), 'evidence_identity_readback')
        _require(resolved.source_sha256 == source.text_sha256 and resolved.source_recorded_at == source.recorded_at
                 and resolved.source_name == source.name and resolved.source_byte_length == source.byte_length
                 and resolved.source_codepoint_length == source.codepoint_length
                 and (resolved.start, resolved.end, resolved.declared_page, resolved.excerpt,
                      resolved.excerpt_sha256, resolved.offset_unit) ==
                     (passage.start, passage.end, passage.page, passage.excerpt,
                      passage.excerpt_sha256, 'unicode_codepoint'), 'evidence_readback')
    original = sources.get_original(owner, project, revision)
    expected_hash = hashlib.sha256(binary).hexdigest()
    _require(original == expected_original and original.source == source and original.content == binary,
             'original_bytes_readback')
    _require(original.sha256 == hashlib.sha256(original.content).hexdigest() == expected_hash
             and original.byte_length == len(binary) and original.media_type == 'application/pdf'
             and original.contract_version == 1, 'original_metadata_readback')
    _require(SourceStore(factory).get_original(owner, project, revision) == original, 'fresh_original_readback')
    other = owner + '-other'
    for call in (lambda: projects.get(other, project), lambda: projects.history(other, project),
                 lambda: sources.get_source(other, project, revision),
                 lambda: sources.list_sources(other, project),
                 lambda: sources.get_original(other, project, revision)):
        with pytest.raises(NotFound):
            call()
    for passage in source.passages:
        with pytest.raises(NotFound):
            sources.resolve_evidence(other, project, passage.evidence_id)


def test_one_project_original_pdf_logical_backup_restore():
    """No fixture fallback, skips, target seeding, HTTP, Docker or role changes."""
    primary = None
    cleanup_failures = []
    databases = None
    directory = None
    try:
        settings, private_root, dump, restore = _admit()
        deadline = time.monotonic() + WORK_SECONDS
        owner_type = _owner_type()
        directory = tempfile.TemporaryDirectory(prefix='nw-w82-', dir=private_root)
        work = Path(directory.name).resolve()
        _receipt('pg_restore_private_directory', directory_name=work.name,
                 planned_files=['native.pgpass', 'project.dump'])
        databases = Databases(settings, deadline)
        token = uuid4().hex
        source = databases.create('nw_w82_src_' + token)
        target = databases.create('nw_w82_dst_' + token)
        _require(source['oid'] != target['oid'], 'distinct_databases_required')
        databases.empty(source)
        databases.empty(target)
        source_factory, target_factory = databases.factory(source), databases.factory(target)
        with source_factory() as conn:
            migrate(conn)
            migrate(conn)
        workspace, project, source_revision = uuid4(), uuid4(), uuid4()
        owner = 'w82-' + token
        from test_pdf_source import pdf_bytes
        binary = pdf_bytes(['W8.2 original 猫', '', 'Retained end 雪'])
        extracted = extract_pdf(binary)
        declarations = extracted.declarations(str(source_revision))
        _require([item['page'] for item in declarations] == [1, 3], 'synthetic_pdf_pages')
        manifest = [{'evidence_id': declarations[0]['evidence_id'], 'source_revision': str(source_revision),
                     'sha256': hashlib.sha256(binary).hexdigest(),
                     'object_key': 'synthetic/' + token + '/original.pdf', 'byte_length': len(binary)}]
        value = snapshot()
        value.update(name='W8.2 restore ' + token,
                     files=[{'filename': 'original.pdf', 'size': len(binary)}], total_text_length=len(extracted.text))
        before = ProjectStore(source_factory).create(owner, workspace, project, 'proj_1', value, manifest)
        _require((before.principal, before.workspace_id, before.project_id, before.display_id,
                  before.revision, before.snapshot, before.evidence) ==
                 (owner, workspace, project, 'proj_1', 1, value, tuple(manifest)), 'seed_project_identity')
        original = SourceStore(source_factory).ingest_pdf(owner, project, source_revision,
                    'original.pdf', extracted.text, declarations, binary)
        _readback(source_factory, before, original, declarations, binary)
        _require(all(conn.closed for conn in databases.connections), 'seed_connections_not_closed')
        with source_factory() as conn:
            size = conn.execute('SELECT pg_database_size(current_database())').fetchone()[0]
            _require(size <= SOURCE_ADMISSION_CAP, 'source_size_admission')
        passfile = _passfile(work, settings, (source, target))
        env = _native_environment(work, passfile)
        archive = work / 'project.dump'
        common = ['--host=127.0.0.1', '--port=15432', '--username=mirofish_fixture',
                  '--no-password', '--no-owner', '--no-privileges']
        _native(owner_type, dump, [*common, '--dbname=' + source['name'], '--format=custom',
                '--lock-wait-timeout=5s'], work, env, deadline, archive=archive)
        archive_hash = hashlib.sha256()
        with archive.open('rb') as stream:
            for block in iter(lambda: stream.read(65536), b''):
                archive_hash.update(block)
        _receipt('pg_restore_archive', byte_length=archive.stat().st_size, sha256=archive_hash.hexdigest())
        databases.empty(target)  # no migration/repair is permitted on this target
        _native(owner_type, restore, [*common, '--dbname=' + target['name'],
                '--exit-on-error', '--single-transaction', str(archive)], work, env, deadline)
        target_proofs_before = databases.proofs.get(target['name'], 0)
        _readback(target_factory, before, original, declarations, binary)
        _require(databases.proofs.get(target['name'], 0) > target_proofs_before, 'actual_target_connections_required')
        _readback(source_factory, before, original, declarations, binary)
        _receipt('pg_restore_store_readback', source=source['name'], source_oid=source['oid'],
                 target=target['name'], target_oid=target['oid'], project_id=str(project),
                 workspace_id=str(workspace), project_revision=before.revision,
                 source_revision=str(source_revision), pdf_sha256=original.sha256,
                 wrong_owner_refused=True, source_unchanged=True,
                 target_connection_proofs=databases.proofs[target['name']])
    except BaseException as error:
        # Never expose raw psycopg/tool/PDF exceptions or pytest locals.
        primary = str(error) if type(error) is ExerciseError else 'exercise_operation_failed'
    finally:
        if databases is not None:
            try:
                cleanup_failures.extend(databases.cleanup())
            except BaseException:
                cleanup_failures.append('database_cleanup_failed')
        if directory is not None:
            try:
                # Resolved run-owned TemporaryDirectory only; no caller path deletion.
                directory.cleanup()
                _require(not Path(directory.name).exists(), 'private_directory_remains')
            except BaseException:
                cleanup_failures.append('private_directory_cleanup_failed')
        _receipt('pg_restore_exercise_cleanup', complete=not cleanup_failures,
                 failures=cleanup_failures, primary_error=primary)
    if primary is not None or cleanup_failures:
        pytest.fail('pg_restore_exercise_failed; see fixed-code cleanup receipt', pytrace=False)
