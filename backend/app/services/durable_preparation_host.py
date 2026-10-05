"""Explicit trusted composition over PG, frozen graph and inherited generators.

No optional SDK/client/native engine is initialized by importing this module.
Only a trusted worker invokes generate(); HTTP start only schedules identifiers.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
from pathlib import Path
import shutil
import threading
import time
from types import SimpleNamespace
from uuid import UUID

from .preparation_client import (PreparationError, canonical, digest, encoded, file_names,
    labels, text, uuid_string, validate_payload, validate_receipt, validate_result)


class BoundedChat:
    """One shared bound across inherited retries and parallel profile workers.

    The trusted client MUST honor timeout/max_tokens with SDK retries disabled.
    A late response is never accepted. The full ceiling remains accounted when
    provider billing or transport outcome is uncertain.
    """
    def __init__(self, client, *, checkpoint, first_call, deadline,
                 max_calls=104, max_tokens=4096, call_seconds=15):
        if (not callable(getattr(getattr(getattr(client, 'chat', None), 'completions', None), 'create', None))
                or type(max_calls) is not int or not 1 <= max_calls <= 304
                or type(max_tokens) is not int or not 1 <= max_tokens <= 8192
                or type(call_seconds) not in (int, float) or not math.isfinite(call_seconds)
                or not 0 < call_seconds <= 15):
            raise PreparationError('preparation_unavailable')
        self.client, self.checkpoint, self.first_call = client, checkpoint, first_call
        self.deadline, self.max_calls, self.max_tokens, self.call_seconds = deadline, max_calls, max_tokens, call_seconds
        self.calls = 0
        self.lock = threading.Lock()
        self.chat = SimpleNamespace(completions=self)

    def create(self, **kwargs):
        with self.lock:
            self.checkpoint()
            if self.calls >= self.max_calls or time.monotonic() >= self.deadline:
                raise PreparationError('preparation_uncertain')
            if len(encoded(kwargs.get('messages'))) > 262144:
                raise PreparationError('result_too_large')
            if self.calls == 0:
                self.first_call()
            self.calls += 1
        kwargs['timeout'] = min(self.call_seconds, max(0.001, self.deadline - time.monotonic()))
        kwargs['max_tokens'] = self.max_tokens
        self.checkpoint()
        if time.monotonic() >= self.deadline:
            raise PreparationError('preparation_uncertain')
        result = self.client.chat.completions.create(**kwargs)
        self.checkpoint()
        if time.monotonic() >= self.deadline:
            raise PreparationError('preparation_uncertain')
        return result


def validate_graph(graph, graph_id):
    """Detach bounded projected input, preserving complete neighbor evidence."""
    try:
        raw = encoded(graph)
        if len(raw) > 8 * 1024 * 1024:
            raise PreparationError('result_too_large')
        graph = json.loads(raw)
        if (type(graph) is not dict or set(graph) != {'graph_id', 'nodes', 'edges', 'node_count', 'edge_count'}
                or graph['graph_id'] != graph_id or type(graph['nodes']) is not list
                or type(graph['edges']) is not list or len(graph['nodes']) > 10000 or len(graph['edges']) > 20000
                or type(graph['node_count']) is not int or graph['node_count'] != len(graph['nodes'])
                or type(graph['edge_count']) is not int or graph['edge_count'] != len(graph['edges'])):
            raise ValueError
        nodes, edges = set(), set()
        for node in graph['nodes']:
            uid = uuid_string(node['uuid'])
            if uid in nodes:
                raise ValueError
            nodes.add(uid)
            text(node['name'], 1024)
            if type(node['summary']) is not str or len(node['summary']) > 32768 or type(node['attributes']) is not dict:
                raise ValueError
            if type(node['labels']) is not list or len(set(node['labels'])) != len(node['labels']):
                raise ValueError
            labels([v for v in node['labels'] if v not in {'Entity', 'Node'}])
            for key in ('episodes', 'evidence_ids'):
                if type(node[key]) is not list or len(node[key]) > 100:
                    raise ValueError
                for v in node[key]:
                    uuid_string(v)
        for edge in graph['edges']:
            uid = uuid_string(edge['uuid'])
            if uid in edges or edge['source_node_uuid'] not in nodes or edge['target_node_uuid'] not in nodes:
                raise ValueError
            edges.add(uid)
            if type(edge['fact']) is not str or len(edge['fact']) > 32768:
                raise ValueError
            for key in ('episodes', 'evidence_ids'):
                if type(edge[key]) is not list or len(edge[key]) > 100:
                    raise ValueError
                for v in edge[key]:
                    uuid_string(v)
        graph['nodes'].sort(key=lambda v: v['uuid'])
        graph['edges'].sort(key=lambda v: v['uuid'])
        return graph
    except PreparationError:
        raise
    except (KeyError, TypeError, ValueError, UnicodeError):
        raise PreparationError('invalid_reply') from None


class DurablePreparationHost:
    def __init__(self, *, settings, connection_factory, read_facade, artifact_root,
                 account_id=None, ceiling_microusd=None, authorize=None,
                 chat_client_factory=None, model_name=None, base_url=None,
                 scheduler=None, generation_seconds=300):
        from mirofish_execution.preparation_store import PreparationStore
        from mirofish_execution.budget import BudgetLedger
        from mirofish_knowledge.bindings import ScopeBindingStore
        from mirofish_storage import ProjectStore, SourceStore
        from mirofish_knowledge.contracts import KnowledgeScope
        if (not callable(connection_factory) or not Path(artifact_root).is_absolute()
                or type(generation_seconds) not in (int, float) or not math.isfinite(generation_seconds)
                or not 1 <= generation_seconds <= 600):
            raise PreparationError('preparation_unavailable')
        self.settings = settings
        self.principal, self.display_graph_id = settings.principal, settings.display_graph_id
        self.scope_dto = KnowledgeScope.model_validate_json(
            json.dumps(settings.scope, ensure_ascii=True, allow_nan=False)).model_dump(mode='json')
        if self.scope_dto['layer'] != 'source' or self.scope_dto['run_id'] is not None or self.scope_dto['branch_id'] is not None:
            raise PreparationError('unauthorized')
        if read_facade._settings.principal != self.principal or read_facade._settings.scope != self.scope_dto or read_facade._settings.display_graph_id != self.display_graph_id:
            raise PreparationError('preparation_unavailable')
        self.store, self.budget = PreparationStore(connection_factory), BudgetLedger(connection_factory)
        self.bindings, self.projects, self.sources = ScopeBindingStore(connection_factory), ProjectStore(connection_factory), SourceStore(connection_factory)
        self.reader, self.root = read_facade, Path(artifact_root)
        self.account_id, self.ceiling = account_id, ceiling_microusd
        self.authorize, self.client_factory = authorize, chat_client_factory
        self.model_name, self.base_url, self.scheduler = model_name, base_url, scheduler
        self.generation_seconds = generation_seconds
        self._cleanup_lock = threading.Lock()
        self._pending_cleanup = {}
        self._transport_owners = set()

    def _new_client(self, attempt_id):
        with self._cleanup_lock:
            if len(self._transport_owners) >= 4:
                raise PreparationError('busy')
            self._transport_owners.add(attempt_id)
        try:
            return self.client_factory()
        except BaseException:
            with self._cleanup_lock:
                self._transport_owners.discard(attempt_id)
            raise

    def _close_client(self, attempt_id, client):
        close = getattr(client, 'close', None)
        if not callable(close):
            with self._cleanup_lock:
                self._transport_owners.discard(attempt_id)
            return True
        with self._cleanup_lock:
            self._pending_cleanup[attempt_id] = client
            try:
                close()
            except Exception:
                return False
            self._pending_cleanup.pop(attempt_id, None)
            self._transport_owners.discard(attempt_id)
            return True

    def drain_cleanup(self):
        """Trusted operator retry for retained transports, never a model retry.

        Clients must implement prompt finite close. This returns false while
        cleanup remains unproven; the caller must retain this host for retry.
        """
        with self._cleanup_lock:
            pending = list(self._pending_cleanup.items())
        for attempt, client in pending:
            self._close_client(attempt, client)
        with self._cleanup_lock:
            return not self._pending_cleanup

    def authorization(self):
        try:
            enabled = (callable(self.authorize) and self.authorize() is True
                       and self.account_id is not None and type(self.ceiling) is int
                       and 1 <= self.ceiling <= 2**63 - 1 and callable(self.client_factory)
                       and type(self.model_name) is str and bool(self.model_name.strip())
                       and type(self.base_url) is str and bool(self.base_url.strip()))
        except Exception:
            enabled = False
        return {'model_calls_enabled': bool(enabled), 'ceiling_microusd': str(self.ceiling) if type(self.ceiling) is int and 1 <= self.ceiling <= 2**63-1 else None}

    def _owned(self, source_revision, *, expected_revision=None):
        from mirofish_knowledge.operations import Tombstoned, NotFound, StorageError
        from mirofish_storage.store import NotFound as AppNotFound, StorageError as AppError
        try:
            binding = self.bindings.resolve(self.principal, self.display_graph_id)
            scope = binding.scope
            if scope.model_dump(mode='json') != self.scope_dto or binding.principal != self.principal or binding.display_graph_id != self.display_graph_id:
                raise PreparationError('conflict')
            project = self.projects.get(self.principal, scope.project_id)
            if project.workspace_id != scope.workspace_id or project.principal != self.principal or project.project_id != scope.project_id:
                raise PreparationError('unauthorized')
            if expected_revision is not None and project.revision != expected_revision:
                raise PreparationError('conflict')
            retained = self.sources.get_source(self.principal, scope.project_id, UUID(source_revision))
            if retained.project_id != scope.project_id or str(retained.source_revision) != source_revision or not retained.passages or retained.codepoint_length > 32768:
                raise PreparationError('invalid_request')
            return scope, project, retained
        except Tombstoned:
            raise PreparationError('tombstoned') from None
        except (NotFound, AppNotFound):
            raise PreparationError('not_found') from None
        except (StorageError, AppError):
            raise PreparationError('preparation_unavailable') from None

    def _dto(self, row, method, payload):
        data = json.loads(encoded(row.frozen['public']))
        data.update(state=row.state, progress={'stage': row.stage, 'completed': row.completed, 'total': 100},
                    error_code=row.error_code, authorization=self.authorization(), receipt=row.receipt,
                    graph_snapshot_atomic=False, model_calls_started=row.model_calls_started, simulation_executed=False)
        return validate_result(data, self.display_graph_id, self.scope_dto, payload, method)

    def _row(self, payload):
        row = self.store.get(self.principal, payload['operation_id'], payload.get('plan_sha256'))
        if row.display_graph_id != self.display_graph_id or row.frozen['public']['scope'] != self.scope_dto:
            raise PreparationError('not_found')
        return row

    def plan(self, payload):
        from mirofish_execution.preparation_contracts import PreparationAuthorityError
        payload = validate_payload('plan', payload)
        # Existing request recovers its original snapshot; never silently reread.
        scope, project, retained = self._owned(payload['source_revision'])
        try:
            prior = self.store.get(self.principal, payload['operation_id'])
        except PreparationAuthorityError as error:
            if error.code != 'not_found':
                raise
        else:
            if (prior.request_sha256 != digest(payload) or prior.display_graph_id != self.display_graph_id
                    or prior.frozen['public']['scope'] != self.scope_dto):
                raise PreparationError('conflict')
            return self._dto(prior, 'plan', payload)
        graph = validate_graph(self.reader.graph_data(self.display_graph_id), self.display_graph_id)
        allowed = set(payload['options']['types'] or [])
        eligible = [n for n in graph['nodes'] if any(v not in {'Entity', 'Node'} and (not allowed or v in allowed) for v in n['labels'])]
        selected = eligible[:payload['options']['max_agents']]
        if not selected:
            raise PreparationError('empty_selection')
        public = {'schema_version': 1, 'display_graph_id': self.display_graph_id, 'scope': self.scope_dto,
                  'project_revision': project.revision, 'operation_id': payload['operation_id'],
                  'source': {'source_revision': payload['source_revision'], 'source_name': retained.name, 'source_sha256': retained.text_sha256},
                  'options': payload['options'], 'actors': [{'source_entity_uuid': n['uuid'], 'name': n['name'], 'labels': [v for v in n['labels'] if v not in {'Entity', 'Node'}]} for n in selected],
                  'projection_sha256': digest(graph)}
        identity = json.loads(encoded(public))
        public['plan_sha256'] = digest(identity)
        frozen = {'public': public, 'plan_identity': identity, 'request': payload,
                  'source_text': retained.text, 'graph': graph}
        # A plan that cannot fit the public contract must not become an
        # inaccessible durable operation merely because PG can hold its input.
        self._dto(SimpleNamespace(frozen=frozen, state='planned', stage='planned', completed=0,
                   model_calls_started=False, receipt=None, error_code=None), 'plan', payload)
        row = self.store.put(self.principal, frozen)
        return self._dto(row, 'plan', payload)

    def start(self, payload):
        from mirofish_execution.budget import ReservationState
        payload = validate_payload('start', payload)
        row = self._row(payload)
        scope, _, retained = self._owned(row.frozen['public']['source']['source_revision'], expected_revision=row.project_revision)
        if retained.text_sha256 != row.frozen['public']['source']['source_sha256']:
            raise PreparationError('conflict')
        if row.state != 'planned':
            return self._dto(row, 'start', payload)
        if not self.authorization()['model_calls_enabled']:
            raise PreparationError('model_calls_disabled')
        if not callable(self.scheduler):
            raise PreparationError('preparation_unavailable')
        reservation = self.budget.reserve_prepared(self.principal, self.account_id, scope,
                                                   row.operation_id, row.plan_sha256, self.ceiling)
        if reservation.state != ReservationState.reserved:
            raise PreparationError('preparation_uncertain')
        queued, won = self.store.queue(self.principal, row.operation_id, row.plan_sha256, reservation.attempt_id)
        if won:
            # Scheduling ack is not model dispatch authority. Any lost ack leaves
            # a queued, fenced operation; subsequent requests only read it.
            try:
                self.scheduler(queued.dispatch.to_wire())
            except Exception:
                raise PreparationError('preparation_uncertain') from None
        return self._dto(self._row(payload), 'start', payload)

    def status(self, payload):
        payload = validate_payload('status', payload)
        row = self._row(payload)
        # Historical completed plans survive project revision changes, but still
        # require owned binding and retained source. No filesystem/provider IO.
        self._owned(row.frozen['public']['source']['source_revision'])
        return self._dto(row, 'status', payload)

    def _artifacts(self, row, root):
        from mirofish_execution.native_owned_binding import _regular_bound_file, _manifest
        from .oasis_profile_generator import OasisProfileGenerator
        public = row.frozen['public']
        names = file_names(public['options']['platforms'])
        blobs = {n: _regular_bound_file(root, n, 2097152) for n in names}
        state, config, grounding = (json.loads(blobs[n]) for n in names[:3])
        ids = [a['source_entity_uuid'] for a in public['actors']]
        sim = 'sim_' + row.operation_id.hex
        if (state.get('status') != 'ready' or state.get('simulation_id') != sim
                or state.get('project_id') != str(row.project_id) or state.get('graph_id') != self.display_graph_id
                or state.get('profiles_count') != len(ids) or state.get('entities_count') != len(ids)
                or state.get('profiles_generated') is not True or state.get('config_generated') is not True
                or config.get('simulation_id') != sim or config.get('project_id') != str(row.project_id)
                or config.get('graph_id') != self.display_graph_id or type(grounding) is not dict or set(grounding) != set(ids)
                or [a.get('entity_uuid') for a in config.get('agent_configs', [])] != ids
                or [a.get('agent_id') for a in config.get('agent_configs', [])] != list(range(len(ids)))
                or [p for p in ('twitter', 'reddit') if state.get('enable_' + p) is True] != public['options']['platforms']
                or any(bool(config.get(p + '_config')) != (p in public['options']['platforms']) for p in ('twitter', 'reddit'))):
            raise PreparationError('preparation_failed')
        nodes = {node['uuid']: node for node in row.frozen['graph']['nodes']}
        for uid in ids:
            node = nodes[uid]
            incident = [edge for edge in row.frozen['graph']['edges']
                        if uid in (edge['source_node_uuid'], edge['target_node_uuid'])]
            expected = {'source_entity_uuid': uid, 'labels': list(node['labels']),
                        'summary': node['summary'], 'attributes': node['attributes'],
                        'episode_ids': list(node['episodes']), 'evidence_ids': list(node['evidence_ids']),
                        'facts': [{'edge_uuid': e['uuid'], 'fact': e['fact'],
                                   'episode_ids': list(e['episodes']), 'evidence_ids': list(e['evidence_ids'])}
                                  for e in incident]}
            if grounding[uid] != expected:
                raise PreparationError('preparation_failed')
        profile_rows = {}
        twitter_header = ['user_id', 'name', 'username', 'user_char', 'description']
        for platform in public['options']['platforms']:
            if platform == 'twitter':
                try:
                    reader = csv.reader(io.StringIO(blobs['twitter_profiles.csv'].decode('utf-8'), newline=''), strict=True)
                    if next(reader, None) != twitter_header:
                        raise PreparationError('preparation_failed')
                    values = list(reader)
                    if any(len(item) != len(twitter_header) for item in values):
                        raise PreparationError('preparation_failed')
                    rows = [dict(zip(twitter_header, item)) for item in values]
                except (csv.Error, UnicodeError):
                    raise PreparationError('preparation_failed') from None
            else:
                rows = json.loads(blobs['reddit_profiles.json'])
            if (type(rows) is not list or any(type(item) is not dict for item in rows)
                    or len(rows) != len(ids) or [str(a.get('user_id')) for a in rows] != [str(i) for i in range(len(ids))]):
                raise PreparationError('preparation_failed')
            if [a.get('name') for a in rows] != [a['name'] for a in public['actors']]:
                raise PreparationError('preparation_failed')
            if (any(type(a.get('username')) is not str or not a['username'].strip() for a in rows)
                    or len({a['username'] for a in rows}) != len(rows)):
                raise PreparationError('preparation_failed')
            if platform == 'twitter' and (any(set(a) != {'user_id', 'name', 'username', 'user_char', 'description'} for a in rows)
                    or any(not a['user_char'].strip() or not a['description'].strip() for a in rows)):
                raise PreparationError('preparation_failed')
            if platform == 'reddit' and any(type(a.get('persona')) is not str or not a['persona'].strip()
                                          or type(a.get('bio')) is not str or not a['bio'].strip() for a in rows):
                raise PreparationError('preparation_failed')
            profile_rows[platform] = rows
        if set(profile_rows) == {'twitter', 'reddit'}:
            for index, (twitter, reddit) in enumerate(zip(profile_rows['twitter'], profile_rows['reddit'])):
                # Reuse the inherited writer's EXACT bio/persona joining and
                # CR/LF normalization; no alternative export format or coercion.
                profile = SimpleNamespace(name=reddit['name'], user_name=reddit['username'],
                                          bio=reddit['bio'], persona=reddit['persona'])
                expected = [str(value) for value in OasisProfileGenerator.twitter_loader_row(profile, index)]
                if [twitter[key] for key in twitter_header] != expected:
                    raise PreparationError('preparation_failed')
        files = [{'name': n, 'sha256': hashlib.sha256(blobs[n]).hexdigest(), 'size': len(blobs[n])} for n in names]
        receipt = {'simulation_id': sim, 'artifact_sha256': _manifest(root, tuple(names), 2097152), 'files': files}
        validate_receipt(receipt, str(row.operation_id), public['options']['platforms'])
        return receipt

    def generate(self, wire, *, heartbeat=lambda: None, cancelled=lambda: False):
        """Synchronous trusted Temporal activity; cooperative finite client bounds.

        The worker executor owns this call. No detached job or provider SDK is
        created by HTTP. Injected transport must honor finite timeout/no retries.
        """
        from mirofish_execution.preparation_contracts import PreparationDispatch
        from mirofish_execution.native_owned_binding import _safe_ancestors
        from .preparation_dependencies import create_knowledge_preparation
        from .simulation_manager import SimulationManager
        dispatch = PreparationDispatch.from_wire(wire)
        row = self.store.claim(self.principal, dispatch)
        deadline = time.monotonic() + self.generation_seconds
        client = None
        started = False

        def checkpoint():
            heartbeat()
            if cancelled() or time.monotonic() >= deadline:
                raise PreparationError('preparation_uncertain')
            current = self.store.get(self.principal, dispatch.operation_id, dispatch.plan_sha256)
            if current.state != 'preparing' or current.attempt_id != dispatch.attempt_id:
                raise PreparationError('preparation_uncertain')

        def first_call():
            nonlocal started
            if not self.authorization()['model_calls_enabled']:
                raise PreparationError('model_calls_disabled')
            self._owned(row.frozen['public']['source']['source_revision'], expected_revision=row.project_revision)
            self.budget.start(self.principal, self.account_id, row.operation_id, row.budget_attempt_id)
            started = True
            self.store.update(self.principal, dispatch, stage='generating_profiles', completed=10, calls_started=True)

        try:
            scope, _, retained = self._owned(row.frozen['public']['source']['source_revision'], expected_revision=row.project_revision)
            if retained.text != row.frozen['source_text'] or not self.authorization()['model_calls_enabled']:
                raise PreparationError('model_calls_disabled')
            checkpoint()
            if not _safe_ancestors(self.root) or self.root.resolve(strict=True) != self.root or not self.root.is_dir():
                raise PreparationError('preparation_unavailable')
            attempt = self.root / '_attempts' / row.operation_id.hex / dispatch.attempt_id.hex
            for component in (self.root / '_attempts', self.root / '_attempts' / row.operation_id.hex, attempt):
                if component.exists():
                    if component == attempt or not _safe_ancestors(component) or not component.is_dir():
                        raise PreparationError('preparation_unavailable')
                else:
                    component.mkdir()
                if not _safe_ancestors(component):
                    raise PreparationError('preparation_unavailable')
            client = self._new_client(dispatch.attempt_id)
            bounded = BoundedChat(client, checkpoint=checkpoint, first_call=first_call,
                                  deadline=deadline, max_calls=len(row.frozen['public']['actors']) * 3 + 4)
            dependencies = create_knowledge_preparation(self.reader, self.display_graph_id,
                chat_client=bounded, model_name=self.model_name, base_url=self.base_url,
                frozen_graph=validate_graph(row.frozen['graph'], self.display_graph_id),
                selected_entity_uuids=[a['source_entity_uuid'] for a in row.frozen['public']['actors']])
            manager = SimulationManager(preparation=dependencies, simulation_data_dir=attempt)
            opts = row.frozen['public']['options']
            state = manager.create_simulation(str(row.project_id), self.display_graph_id,
                enable_twitter='twitter' in opts['platforms'], enable_reddit='reddit' in opts['platforms'],
                trusted_simulation_id='sim_' + row.operation_id.hex)

            def progress(stage, percent, *args, **kwargs):
                checkpoint()
                offsets = {'reading': (0, 10), 'generating_profiles': (10, 60), 'generating_config': (70, 20)}
                offset, span = offsets[stage]
                self.store.update(self.principal, dispatch, stage=stage, completed=offset + int(percent * span / 100))

            manager.prepare_simulation(state.simulation_id, opts['simulation_requirement'], row.frozen['source_text'],
                                       defined_entity_types=opts['types'], parallel_profile_count=1, progress_callback=progress)
            checkpoint()
            self.store.update(self.principal, dispatch, stage='publishing', completed=95)
            source = attempt / state.simulation_id
            receipt = self._artifacts(row, source)
            destination = self.root / state.simulation_id
            # Exclusive directory creation. Partial/orphan publication is never
            # authoritative; it is preserved and cannot be overwritten/retried.
            shutil.copytree(source, destination)
            for name in file_names(opts['platforms']):
                path = destination / name
                with path.open('rb+') as stream:
                    os.fsync(stream.fileno())
                path.chmod(0o444)
            if self._artifacts(row, destination) != receipt:
                raise PreparationError('preparation_uncertain')
            checkpoint()
            # Close the transport before READY; failure quarantines the result.
            if not self._close_client(dispatch.attempt_id, client):
                raise PreparationError('preparation_uncertain')
            client = None
            checkpoint()
            self.budget.settle_prepared(self.principal, self.account_id, scope, row.operation_id,
                row.budget_attempt_id, row.plan_sha256, receipt['artifact_sha256'])
            checkpoint()
            self.store.update(self.principal, dispatch, state='ready', stage='ready', completed=100, receipt=receipt)
            return {'schema_version': 1, 'operation_id': str(row.operation_id),
                    'plan_sha256': row.plan_sha256, 'state': 'ready', 'artifact_sha256': receipt['artifact_sha256']}
        except BaseException:
            # Started uncertainty holds the full ceiling. Even if the activity
            # dies here, preparing+attempt fence blocks restart dispatch.
            try:
                if started:
                    self.budget.mark_uncertain(self.principal, self.account_id, row.operation_id,
                                                row.budget_attempt_id, 'dispatch_uncertain')
                else:
                    self.budget.release_undispatched(self.principal, self.account_id, row.operation_id, row.budget_attempt_id)
            except Exception:
                pass
            try:
                self.store.update(self.principal, dispatch, state='uncertain' if started else 'failed',
                                  stage='uncertain' if started else 'failed',
                                  error_code='preparation_uncertain' if started else 'preparation_failed')
            except Exception:
                pass
            raise PreparationError('preparation_uncertain' if started else 'preparation_failed') from None
        finally:
            if client is not None:
                self._close_client(dispatch.attempt_id, client)

    def bind_native(self, payload, *, run_id, runtime_sha256, model_factory):
        """Trusted only: reauthorize READY/project BEFORE inspecting any files."""
        from mirofish_execution.native_owned_binding import NativeOwnedSessionFactory
        from mirofish_execution.native_run_contracts import NativeRunRequest
        import pickle
        payload = validate_payload('status', payload)
        row = self._row(payload)
        self._owned(row.frozen['public']['source']['source_revision'], expected_revision=row.project_revision)
        if row.state != 'ready' or row.receipt is None:
            raise PreparationError('conflict')
        opts = row.frozen['public']['options']
        validate_receipt(row.receipt, str(row.operation_id), opts['platforms'])
        if opts['max_rounds'] > 24:
            raise PreparationError('conflict')
        request = NativeRunRequest.from_wire({'schema_version': 1, 'principal': self.principal,
            'project_id': str(row.project_id), 'project_revision': row.project_revision,
            'simulation_id': row.receipt['simulation_id'], 'run_id': str(run_id),
            'artifact_sha256': row.receipt['artifact_sha256'], 'runtime_sha256': runtime_sha256,
            'platforms': opts['platforms'], 'seed': opts['seed'], 'max_rounds': opts['max_rounds']})
        if not callable(model_factory) or len(pickle.dumps(model_factory)) > 65536:
            raise PreparationError('preparation_unavailable')
        root = self.root / ('sim_' + row.operation_id.hex)
        if self._artifacts(row, root) != row.receipt:
            raise PreparationError('preparation_uncertain')
        factory = NativeOwnedSessionFactory(str(root), self.display_graph_id, request.simulation_id,
            self.principal, str(row.project_id), row.project_revision, request.platforms,
            request.seed, request.max_rounds, runtime_sha256, model_factory)
        factory.validate(request)
        return request, factory
