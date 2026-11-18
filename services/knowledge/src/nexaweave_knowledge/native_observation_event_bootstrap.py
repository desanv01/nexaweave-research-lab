"""Fixed owned entry point for one receipt-bound restored Twitter projection."""
import hashlib
import json
import sys
from uuid import NAMESPACE_URL, UUID, uuid5


WIRE_BYTES = 32768
CREATE = """
CREATE (a:Entity:NativeActor), (p:Entity:NativePost), (e:Episodic), (m:MiroFishIngest)
SET a=$actor, p=$post, e=$episode, m=$marker
CREATE (a)-[r:RELATES_TO]->(p), (e)-[:MENTIONS]->(a), (e)-[:MENTIONS]->(p)
SET r=$edge
"""
READ = """
MATCH (e:Episodic {uuid:$episode_id,group_id:$group_id})-[:MENTIONS]->(a:Entity:NativeActor),
      (e)-[:MENTIONS]->(p:Entity:NativePost),
      (a)-[r:RELATES_TO {uuid:$edge_id,group_id:$group_id}]->(p),
      (m:MiroFishIngest {uuid:$episode_id,group_id:$group_id})
RETURN properties(a) AS actor, properties(p) AS post, properties(r) AS edge,
       properties(e) AS episode, properties(m) AS marker LIMIT 2
"""


def _object(raw):
    def unique(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError
            result[key] = value
        return result

    def reject_constant(_):
        raise ValueError

    return json.loads(raw, object_pairs_hook=unique, parse_constant=reject_constant)


def _uuid(raw):
    value = UUID(raw)
    if str(value) != raw:
        raise ValueError
    return value


def _sha(raw):
    if type(raw) is not str or len(raw) != 64 or any(c not in '0123456789abcdef' for c in raw):
        raise ValueError
    return raw


def event_ontology():
    from nexaweave_knowledge.contracts import OntologySpec

    return OntologySpec(
        revision=uuid5(NAMESPACE_URL, 'mirofish:restored-twitter-action-ontology:v1'),
        entity_types=(
            {'name': 'NativeActor', 'description': 'An actor in the native simulation'},
            {'name': 'NativePost', 'description': 'A post created in the native simulation'},
        ),
        edge_types=({'name': 'AUTHORED', 'description': 'The actor created the post',
                     'source_targets': ({'source': 'NativeActor', 'target': 'NativePost'},)},),
    )


def event_projection(frame):
    from nexaweave_knowledge.contracts import KnowledgeScope, SourceEnvelope
    from nexaweave_knowledge.operations import request_fingerprint

    if (type(frame) is not dict or set(frame) != {'version', 'scope', 'operation_id', 'attempt_id', 'fingerprint', 'source'}
            or type(frame['version']) is not int or frame['version'] != 1):
        raise ValueError
    scope = KnowledgeScope.model_validate_json(json.dumps(frame['scope']))
    source = SourceEnvelope.model_validate_json(json.dumps(frame['source']))
    operation = _uuid(frame['operation_id'])
    attempt = _uuid(frame['attempt_id'])
    envelope = _object(source.content)
    ontology = event_ontology()
    if (scope.layer.value != 'simulation' or scope.branch_id is not None or scope.run_id is None
            or source.operation_id != operation or source.source_revision != operation
            or source.source_kind != 'event'
            or source.ontology_revision != ontology.revision
            or request_fingerprint(scope, source, ontology) != _sha(frame['fingerprint'])
            or source.evidence_ids != (uuid5(operation, 'mirofish:restored-twitter-action-evidence:v1'),)
            or set(envelope) != {'launch_sha256', 'evidence_sha256', 'platform', 'record_index', 'record_sha256', 'raw_json'}
            or envelope['platform'] != 'twitter' or type(envelope['record_index']) is not int
            or not 1 <= envelope['record_index'] <= 9998
            or operation != uuid5(scope.run_id, f"mirofish:restored-twitter-action:v1:{envelope['record_index']}")):
        raise ValueError
    raw = envelope['raw_json'].encode('utf-8')
    if not 1 <= len(raw) <= 8192 or hashlib.sha256(raw).hexdigest() != _sha(envelope['record_sha256']):
        raise ValueError
    action = _object(raw)
    args = action['action_args']
    actor_id, post_id = action['agent_id'], args['post_id']
    content = args['content']
    if (action['action_type'] != 'CREATE_POST' or action['success'] is not True
            or type(actor_id) is not int or not 0 <= actor_id <= 2**53-1
            or type(post_id) is not int or not 1 <= post_id <= 2**53-1
            or type(content) is not str or not content or len(content.encode('utf-8')) > 8192):
        raise ValueError
    name = action.get('agent_name', str(actor_id))
    if type(name) is not str or not name or len(name.encode('utf-8')) > 512:
        raise ValueError
    episode = str(scope.episode_uuid(operation))
    identity = {'group_id': scope.group_id, 'operation_id': str(operation)}
    stamp = source.recorded_at.isoformat()
    marker = dict(identity, uuid=episode, episode_id=episode, attempt_id=str(attempt),
                  fingerprint=_sha(frame['fingerprint']), sha256=source.source_sha256,
                  source_revision=str(source.source_revision), platform='twitter', record_index=envelope['record_index'],
                  record_sha256=envelope['record_sha256'], receipt_sha256=_sha(envelope['evidence_sha256']),
                  launch_sha256=_sha(envelope['launch_sha256']), status='complete',
                  scope_json=json.dumps(scope.model_dump(mode='json'), sort_keys=True, separators=(',', ':')),
                  evidence_ids=[str(value) for value in source.evidence_ids])
    return {
        'actor': dict(identity, uuid=str(uuid5(operation, 'actor')), native_id=actor_id, name=name, created_at=stamp),
        'post': dict(identity, uuid=str(uuid5(operation, 'post')), native_id=post_id, name='Twitter post',
                     summary=content, created_at=stamp),
        'edge': dict(identity, uuid=str(uuid5(operation, 'authored')), name='AUTHORED',
                     fact=content, episodes=[episode], created_at=stamp),
        'episode': dict(identity, uuid=episode, name=source.source_name, created_at=stamp),
        'marker': marker,
    }


def _apply(frame):
    from neo4j import GraphDatabase
    from nexaweave_knowledge.contracts import Layer
    from nexaweave_knowledge.operations import Ledger, OperationState
    from nexaweave_knowledge.read_runtime import ReadSettings

    settings = ReadSettings.from_environment()
    expected = dict(settings.scope.model_dump(mode='json'), layer=Layer.simulation.value,
                    run_id=frame['scope']['run_id'])
    if expected != frame['scope'] or settings.scope.layer != Layer.source:
        raise ValueError
    from nexaweave_knowledge.contracts import KnowledgeScope

    scope = KnowledgeScope.model_validate_json(json.dumps(expected))
    projection = event_projection(frame)
    operation = _uuid(frame['operation_id'])
    attempt = _uuid(frame['attempt_id'])
    row = Ledger(settings.connection).get(scope, operation)
    if (row.state != OperationState.running or row.current_attempt != attempt
            or row.fingerprint != frame['fingerprint']):
        raise ValueError
    group = scope.group_id
    episode = projection['episode']['uuid']
    with GraphDatabase.driver(settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password),
                              connection_timeout=3, connection_acquisition_timeout=5,
                              max_transaction_retry_time=0) as driver:
        with driver.session() as session:
            with session.begin_transaction(timeout=5) as tx:
                existing = tx.run(
                    "MATCH (n {group_id:$group_id}) WHERE n.uuid IN $ids RETURN n.uuid LIMIT 1",
                    group_id=group, ids=[projection[key]['uuid'] for key in ('actor', 'post', 'episode')],
                ).single()
                if existing is not None:
                    raise ValueError
                tx.run(CREATE, **projection).consume()
                tx.commit()
            with session.begin_transaction(timeout=5) as tx:
                result = tx.run(READ, group_id=group, episode_id=episode,
                                edge_id=projection['edge']['uuid']).single(strict=True)
                observed = result.data()
                if observed != projection:
                    raise ValueError
                tx.commit()
    return observed


def main():
    try:
        from pathlib import Path
        import nexaweave_knowledge

        if len(sys.argv) != 1:
            raise ValueError
        if Path(__file__).resolve().parent != Path(nexaweave_knowledge.__file__).resolve().parent:
            raise ValueError
        stream = sys.stdin.buffer
        length = int.from_bytes(stream.read(4), 'big')
        if not 0 < length <= WIRE_BYTES:
            raise ValueError
        raw = stream.read(length)
        if len(raw) != length or stream.read(1):
            raise ValueError
        observed = _apply(_object(raw))
        reply = json.dumps(observed, ensure_ascii=False, sort_keys=True, allow_nan=False,
                           separators=(',', ':')).encode('utf-8')
        if not 0 < len(reply) <= WIRE_BYTES:
            raise ValueError
        sys.stdout.buffer.write(len(reply).to_bytes(4, 'big') + reply)
        sys.stdout.buffer.flush()
    except Exception:
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
