import test from 'node:test'
import assert from 'node:assert/strict'
import { webcrypto, createHash } from 'node:crypto'
import { createWorkbenchClient } from '../src/api/workbench.js'
import { preparationPayload, preparationInteger, preparationIdentity, preparationSources, validatePreparationResult } from '../src/api/simulationPreparation.js'
import { nativeReadyPreparation } from '../src/api/nativeLaunch.js'
globalThis.crypto ||= webcrypto
const uid = n => `00000000-0000-0000-0000-${String(n).padStart(12, '0')}`
const hash = 'a'.repeat(64), clone = v => JSON.parse(JSON.stringify(v)), code = expected => e => e.code === expected
const source = { project_id: uid(2), source_revision: uid(5), source_name: '<img src=x> 中文😀', text_sha256: hash, byte_length: 8, codepoint_length: 4, recorded_at: '2026-10-05T00:00:00Z' }
const payload = { schema_version: 1, operation_id: uid(9), source_revision: source.source_revision, options: { types: null, max_agents: 10, seed: 0, platforms: ['twitter', 'reddit'], max_rounds: 10, simulation_requirement: 'Study responses 中文😀' } }
// Independent fixture producer: recursive JSON serializer, not the UI helper.
function producerAscii(value) {
  if (Array.isArray(value)) return '[' + value.map(producerAscii).join(',') + ']'
  if (value && typeof value === 'object') return '{' + Object.keys(value).sort().map(key => producerAscii(key) + ':' + producerAscii(value[key])).join(',') + '}'
  const raw = JSON.stringify(value)
  let result = ''
  for (let i = 0; i < raw.length; i++) { const unit = raw.charCodeAt(i); result += unit >= 127 ? '\\u' + unit.toString(16).padStart(4, '0') : raw[i] }
  return result
}
function producerPlanSha(value) {
  const identity = { schema_version: value.schema_version, display_graph_id: value.display_graph_id, scope: value.scope, project_revision: value.project_revision, operation_id: value.operation_id, source: value.source, options: value.options, actors: value.actors, projection_sha256: value.projection_sha256 }
  if (value.schema_version === 2 && Object.hasOwn(value, 'sources')) identity.sources = value.sources
  return createHash('sha256').update(producerAscii(identity), 'ascii').digest('hex')
}
function fixture(state = 'planned') {
  const value = { schema_version: 1, display_graph_id: 'graph_1', scope: { schema_version: 1, workspace_id: uid(1), project_id: uid(2), graph_id: uid(3), run_id: null, branch_id: null, layer: 'source' }, project_revision: 1, operation_id: payload.operation_id, source: { source_revision: source.source_revision, source_name: source.source_name, source_sha256: hash }, options: clone(payload.options), actors: [{ source_entity_uuid: uid(7), name: '<script>actor</script>😀', labels: ['Person'] }], projection_sha256: hash, plan_sha256: null, state, progress: { stage: state, completed: state === 'ready' ? 100 : 0, total: 100 }, error_code: null, authorization: { model_calls_enabled: true, ceiling_microusd: '12345' }, receipt: null, graph_snapshot_atomic: false, model_calls_started: state === 'ready', simulation_executed: false }
  value.plan_sha256 = producerPlanSha(value); return value
}
function readyFixture() {
  const v = fixture('ready'), files = ['state.json', 'simulation_config.json', 'source_grounding.json', 'twitter_profiles.csv', 'reddit_profiles.json'].map(name => ({ name, sha256: hash, size: 123 }))
  const raw = JSON.stringify({ files: files.map(f => ({ name: f.name, sha256: f.sha256, size: f.size })), schema_version: 1 })
  v.receipt = { simulation_id: 'sim_' + payload.operation_id.replaceAll('-', ''), artifact_sha256: createHash('sha256').update(raw).digest('hex'), files }
  return v
}
const envelope = v => new Response(JSON.stringify({ success: true, data: v }), { headers: { 'Content-Type': 'application/json' } })
const graph = { graph_id: 'graph_1', nodes: [], edges: [], node_count: 0, edge_count: 0 }
const credentials = { origin: 'http://127.0.0.1:5001', graph: 'graph_1', token: 'private-token' }
const selectedSources = () => [clone(source), ...[31, 32].map(n => ({ ...source, source_revision: uid(n),
  source_name: `Document ${n} 雪😀 <literal>`, text_sha256: String(n === 31 ? 'b' : 'c').repeat(64) }))]
const selectedPayload = () => ({ ...clone(payload), schema_version: 2, source_revisions: selectedSources().map(s => s.source_revision) })
function selectedFixture(state = 'planned') {
  const value = state === 'ready' ? readyFixture() : fixture(state)
  value.schema_version = 2
  value.sources = selectedSources().map(s => ({ source_revision: s.source_revision, source_name: s.source_name, source_sha256: s.text_sha256 }))
  value.plan_sha256 = producerPlanSha(value)
  return value
}

function nativePlanFor(ready, request) {
  const value = { schema_version: 1, display_graph_id: ready.display_graph_id, scope: clone(ready.scope),
    preparation: { operation_id: ready.operation_id, plan_sha256: ready.plan_sha256,
      simulation_id: ready.receipt.simulation_id, artifact_sha256: ready.receipt.artifact_sha256 },
    request: { schema_version: 1, principal: 'local-research', project_id: ready.scope.project_id,
      project_revision: ready.project_revision, simulation_id: ready.receipt.simulation_id, run_id: request.launch_id,
      artifact_sha256: ready.receipt.artifact_sha256, runtime_sha256: hash, platforms: clone(ready.options.platforms),
      seed: ready.options.seed, max_rounds: ready.options.max_rounds },
    limits: { max_calls: 20, max_input_bytes: 2097152, max_output_tokens: 4096, max_run_seconds: 600 },
    ceiling_microusd: '9223372036854775807', model_label: 'scripted', launch_sha256: null, state: 'planned',
    error_code: null, authorization: { model_calls_enabled: true }, workflow: null, receipt: null,
    cancel_requested: false, cleanup: { known: true, pending: false, owner_thread_alive: false } }
  const identity = Object.fromEntries('schema_version display_graph_id scope preparation request limits ceiling_microusd model_label'.split(' ').map(key => [key, value[key]]))
  value.launch_sha256 = createHash('sha256').update(producerAscii(identity), 'ascii').digest('hex')
  return value
}
test('V2 plan request and selected metadata admit exact ordered unique sources while V1 remains exact', () => {
  const request = selectedPayload(), metadata = selectedSources(), original = clone(metadata)
  assert.deepEqual(JSON.parse(preparationPayload(request, true)), request)
  const admitted = preparationSources(metadata, request)
  metadata[1].source_name = 'changed caller'; metadata.reverse()
  assert.deepEqual(admitted, original)
  for (const revisions of [[], null, 'not-array', [uid(5), uid(5)], [uid(31), uid(5)], [uid(5), true],
    [uid(5), '00000000-0000-0000-0000-0000000000AB'], Array.from({ length: 101 }, (_, i) => uid(i + 5))]) {
    assert.throws(() => preparationPayload({ ...selectedPayload(), source_revisions: revisions }, true), code('invalid_request'))
  }
  const maximum = { ...selectedPayload(), source_revisions: Array.from({ length: 100 }, (_, i) => uid(i + 5)) }
  assert.equal(JSON.parse(preparationPayload(maximum, true)).source_revisions.length, 100)
  assert.throws(() => preparationPayload({ ...payload, source_revisions: [uid(5)] }, true), code('invalid_request'))
  assert.throws(() => preparationPayload({ ...selectedPayload(), sources: [] }, true), code('invalid_request'))
  const missing = selectedPayload(); delete missing.source_revisions
  assert.throws(() => preparationPayload(missing, true), code('invalid_request'))
  for (const version of [true, '2', 3]) assert.throws(() => preparationPayload({ ...selectedPayload(), schema_version: version }, true), code('invalid_request'))
  assert.throws(() => preparationPayload({ schema_version: 2, operation_id: uid(9), plan_sha256: hash }), code('invalid_request'))
  assert.deepEqual(JSON.parse(preparationPayload(payload, true)), payload)
})

test('V2 metadata helper refuses wrong order, foreign project, duplicates and aggregate overflow before transport', () => {
  const changes = [
    rows => rows.reverse(), rows => rows.pop(), rows => rows.push({ ...source, source_revision: uid(33) }),
    rows => rows[1].project_id = uid(99), rows => rows[1] = clone(rows[0]),
    rows => rows[1].source_name = '\ud800', rows => rows[1].text = 'unexpected source contents',
    rows => { rows[0].codepoint_length = 32768; rows[0].byte_length = 32768 }
  ]
  for (const change of changes) {
    const rows = selectedSources(); change(rows)
    assert.throws(() => preparationSources(rows, selectedPayload()), code('invalid_request'))
  }
  for (const rows of [source, [], null]) assert.throws(() => preparationSources(rows, selectedPayload()), code('invalid_request'))
  const rows = selectedSources(); rows[0].codepoint_length = 32760; rows[0].byte_length = 32760
  assert.equal(preparationSources(rows, selectedPayload()).reduce((n, row) => n + row.codepoint_length, 0), 32768)
})

test('V2 reply fingerprint covers ordered complete Unicode source metadata and rejects correctly rehashed substitutions', async () => {
  const request = selectedPayload(), metadata = selectedSources(), expected = selectedFixture()
  assert.deepEqual(await validatePreparationResult(expected, { graph: 'graph_1', payload: request, source: metadata, planning: true }), expected)
  const changes = [
    v => { v.schema_version = 1; delete v.sources }, v => delete v.sources, v => v.sources = [],
    v => v.sources = [v.sources[0]], v => v.sources.push({ ...v.sources[1], source_revision: uid(33) }),
    v => [v.sources[1], v.sources[2]] = [v.sources[2], v.sources[1]],
    v => v.sources[1].source_name = 'substituted', v => v.sources[1].source_sha256 = 'd'.repeat(64),
    v => v.sources[1].source_revision = uid(99), v => v.sources[0].source_name = 'changed anchor',
    v => v.sources[1] = clone(v.sources[0]), v => v.sources[1].project_id = uid(2),
    v => v.sources[1].source_name = '\udc00', v => v.sources[1].source_name = ' ',
    v => v.sources[1].source_name = '😀'.repeat(257)
  ]
  for (const change of changes) {
    const value = selectedFixture(); change(value); value.plan_sha256 = producerPlanSha(value)
    assert.equal(value.plan_sha256, producerPlanSha(value))
    await assert.rejects(validatePreparationResult(value, { graph: 'graph_1', payload: request, source: metadata, planning: true }), code('invalid_reply'))
  }
  await assert.rejects(validatePreparationResult(selectedFixture(), { graph: 'graph_1', payload, source, planning: true }), code('invalid_reply'))
  const extraOnV1 = fixture(); extraOnV1.sources = clone(expected.sources)
  await assert.rejects(validatePreparationResult(extraOnV1, { graph: 'graph_1', payload, source, planning: true }), code('invalid_reply'))
})

test('V2 recovery and native-ready validation retain all immutable sources and recompute complete hash', async () => {
  const known = selectedFixture(), ready = selectedFixture('ready'), identity = preparationIdentity(known)
  assert.deepEqual(identity, { schema_version: 1, operation_id: known.operation_id, plan_sha256: known.plan_sha256 })
  assert.deepEqual(await validatePreparationResult(ready, { graph: 'graph_1', payload: identity, known }), ready)
  assert.deepEqual(await nativeReadyPreparation(ready, 'graph_1'), ready)
  for (const mutate of [v => v.sources[1].source_name += ' forged',
    v => [v.sources[1], v.sources[2]] = [v.sources[2], v.sources[1]], v => v.sources.pop(),
    v => { v.schema_version = 1; delete v.sources }]) {
    const value = selectedFixture('ready'); mutate(value); value.plan_sha256 = producerPlanSha(value)
    // Matching forged known/request hashes still cannot replace the recomputed
    // full identity, and a real known plan additionally fences valid rehashes.
    await assert.rejects(validatePreparationResult(value, { graph: 'graph_1', payload: identity, known }), code('invalid_reply'))
  }
  const staleHash = selectedFixture('ready'); staleHash.sources[1].source_name += ' forged'
  await assert.rejects(nativeReadyPreparation(staleHash, 'graph_1'), code('invalid_reply'))
  const forgedKnown = clone(staleHash)
  await assert.rejects(validatePreparationResult(staleHash, { graph: 'graph_1', payload: preparationIdentity(forgedKnown), known: forgedKnown }), code('invalid_reply'))
})

test('V2 direct async validation detaches metadata, payload, reply and recovery sources before digest waits', async () => {
  const reply = selectedFixture(), request = selectedPayload(), rows = selectedSources(), expected = clone(reply)
  const pending = validatePreparationResult(reply, { graph: 'graph_1', payload: request, source: rows, planning: true })
  rows[1].source_name = 'caller changed'; rows.reverse(); request.source_revisions.reverse()
  reply.sources[1].source_sha256 = 'f'.repeat(64); reply.sources.reverse()
  assert.deepEqual(await pending, expected)
  const known = selectedFixture(), ready = selectedFixture('ready'), identity = preparationIdentity(known), readyExpected = clone(ready)
  const status = validatePreparationResult(ready, { graph: 'graph_1', payload: identity, known })
  known.sources.reverse(); ready.sources[1].source_name = 'changed'; identity.plan_sha256 = 'e'.repeat(64)
  assert.deepEqual(await status, readyExpected)
  const nativeReady = selectedFixture('ready'), nativeExpected = clone(nativeReady)
  const nativePending = nativeReadyPreparation(nativeReady, 'graph_1'); nativeReady.sources.pop()
  assert.deepEqual(await nativePending, nativeExpected)
})

test('Workbench V2 transport snapshots selected metadata, recovers after lost Start and admits unchanged native wire', async () => {
  const calls = [], known = selectedFixture(), ready = selectedFixture('ready')
  let releasePlan
  const client = createWorkbenchClient({ fetchImpl: async (url, options) => {
    calls.push({ url, options })
    if (url.includes('/data/')) return envelope(graph)
    if (url.includes('/native-launch/plan/')) return envelope(nativePlanFor(ready, JSON.parse(options.body)))
    if (url.endsWith('/plan')) return new Promise(resolve => { releasePlan = () => resolve(envelope(known)) })
    if (url.endsWith('/start')) throw new Error('private lost Start')
    return envelope(ready)
  } })
  try {
    await client.connect(credentials)
    const request = selectedPayload(), metadata = selectedSources(), original = clone(request)
    const pending = client.preparationPlan(request, metadata)
    assert.equal(calls.length, 2)
    request.source_revisions.reverse(); request.options.seed = 99
    metadata[1].source_name = 'late caller'; metadata.reverse()
    releasePlan()
    const admitted = await pending
    assert.deepEqual(admitted.sources, known.sources)
    assert.deepEqual(JSON.parse(calls[1].options.body), original)
    admitted.sources[1].source_name = 'do not mutate private cache'
    const identity = preparationIdentity(known)
    await assert.rejects(client.preparationStart(identity), code('transport_failure'))
    await assert.rejects(client.preparationStart(identity), code('conflict'))
    const recovered = await client.preparationStatus(identity)
    assert.deepEqual(recovered.sources, known.sources)
    assert.deepEqual(JSON.parse(calls[2].options.body), identity)
    assert.deepEqual(JSON.parse(calls[3].options.body), identity)
    const nativeRequest = { schema_version: 1, launch_id: uid(90), preparation: { operation_id: known.operation_id, plan_sha256: known.plan_sha256 } }
    const alteredReady = clone(recovered); alteredReady.sources.reverse()
    await assert.rejects(client.nativeLaunchPlan(nativeRequest, alteredReady), code('invalid_request'))
    assert.equal(calls.length, 4)
    const native = await client.nativeLaunchPlan(nativeRequest, recovered)
    assert.equal(native.schema_version, 1)
    assert.deepEqual(JSON.parse(calls[4].options.body), nativeRequest)
    assert.equal(Object.hasOwn(native.preparation, 'sources'), false)
    assert.equal(native.preparation.plan_sha256, ready.plan_sha256)
    assert.equal(native.ceiling_microusd, '9223372036854775807')
    for (const { url, options } of calls.slice(1)) {
      assert.equal(options.method, 'POST'); assert.equal(options.credentials, 'omit'); assert.equal(options.redirect, 'error')
      assert.equal(options.cache, 'no-store'); assert.equal(options.headers.Authorization, 'Bearer private-token')
      assert.ok(!url.includes('private-token')); assert.ok(options.signal.aborted)
    }
  } finally { client.disconnect() }
})

test('Workbench rejects invalid V2 source context before network and fences correctly rehashed recovery substitution', async () => {
  const calls = [], known = selectedFixture()
  let corrupted = false
  const client = createWorkbenchClient({ fetchImpl: async (url, options) => {
    calls.push({ url, options })
    if (url.includes('/data/')) return envelope(graph)
    const value = url.endsWith('/plan') ? clone(known) : selectedFixture('ready')
    if (corrupted) { value.sources[1].source_name = 'rehashed foreign source'; value.plan_sha256 = producerPlanSha(value) }
    return envelope(value)
  } })
  try {
    await client.connect(credentials)
    for (const rows of [source, selectedSources().reverse(), selectedSources().slice(0, 1),
      selectedSources().map((s, i) => i === 1 ? { ...s, project_id: uid(99) } : s)]) {
      await assert.rejects(client.preparationPlan(selectedPayload(), rows), code('invalid_request'))
    }
    await assert.rejects(client.preparationPlan(payload, [source]), e => ['invalid_request', 'invalid_reply'].includes(e.code))
    assert.equal(calls.length, 1)
    await client.preparationPlan(selectedPayload(), selectedSources())
    corrupted = true
    await assert.rejects(client.preparationStatus(preparationIdentity(known)), code('invalid_reply'))
    corrupted = false
    assert.deepEqual((await client.preparationStatus(preparationIdentity(known))).sources, known.sources)
    assert.equal(calls.length, 4)
  } finally { client.disconnect() }
})

test('Workbench cancelled V2 plan cannot populate preparation recovery or native-ready admission', async () => {
  const calls = [], known = selectedFixture(); let releasePlan
  const client = createWorkbenchClient({ fetchImpl: async (url, options) => {
    calls.push({ url, options })
    if (url.includes('/data/')) return envelope(graph)
    if (calls.length === 2) return new Promise(resolve => { releasePlan = () => resolve(envelope(known)) })
    return envelope(known)
  } })
  try {
    await client.connect(credentials)
    const pending = client.preparationPlan(selectedPayload(), selectedSources())
    client.cancel(); await assert.rejects(pending, code('cancelled'))
    assert.equal(calls[1].options.signal.aborted, true)
    releasePlan(); await Promise.resolve()
    await assert.rejects(client.preparationStatus(preparationIdentity(known)), code('invalid_request'))
    assert.equal(calls.length, 2)
    assert.deepEqual((await client.preparationPlan(selectedPayload(), selectedSources())).sources, known.sources)
    assert.equal(calls.length, 3)
  } finally { client.disconnect() }
})

test('payload and integer admission preserve unsigned limits and reject coercion or extra authority', () => {
  for (const seed of [0, 4294967295]) { const p = clone(payload); p.options.seed = seed; assert.equal(JSON.parse(preparationPayload(p, true)).options.seed, seed) }
  for (const v of ['-1', '1e2', '0.5', ' 0', '', '4294967296']) assert.throws(() => preparationInteger(v, 4294967295), code('invalid_request'))
  for (const mutate of [p => p.path = '/tmp', p => p.options.seed = '0', p => p.options.max_agents = 0, p => p.options.types = ['Entity'], p => p.options.types = ['Person', 'Person'], p => p.options.platforms.reverse(), p => p.options.simulation_requirement = '\ud800', p => p.options.max_rounds = Infinity]) { const p = clone(payload); mutate(p); assert.throws(() => preparationPayload(p, true), code('invalid_request')) }
})
test('complete plan admission detaches and binds owned source, options and source-only scope', async () => {
  const v = fixture(), admitted = await validatePreparationResult(v, { graph: 'graph_1', payload, source, planning: true })
  v.actors[0].name = 'mutation'; assert.notEqual(admitted.actors[0].name, 'mutation')
  for (const mutate of [v => v.scope.project_id = uid(99), v => v.scope.run_id = uid(99), v => v.source.source_revision = uid(99), v => v.source.source_sha256 = 'c'.repeat(64), v => v.options.seed = 1, v => v.graph_snapshot_atomic = true, v => v.simulation_executed = true, v => v.model_calls_started = true, v => v.progress.completed = 1, v => v.actors.push(clone(v.actors[0])), v => v.actors[0].extra = 1, v => v.actors[0].name = '\ud800', v => v.authorization.ceiling_microusd = '01', v => v.authorization.extra = true]) { const p = fixture(); mutate(p); await assert.rejects(validatePreparationResult(p, { graph: 'graph_1', payload, source, planning: true }), code('invalid_reply')) }
})
test('ready receipt requires exact enabled filenames, native manifest hash and immutable plan correspondence', async () => {
  const known = fixture(), p = preparationIdentity(known), ready = readyFixture()
  assert.equal((await validatePreparationResult(ready, { graph: 'graph_1', payload: p, known })).state, 'ready')
  for (const mutate of [v => v.receipt.files.reverse(), v => v.receipt.files.pop(), v => v.receipt.files[0].size = 0, v => v.receipt.files[0].size = 2097153, v => v.receipt.files[0].name = '../state.json', v => v.receipt.artifact_sha256 = hash, v => v.receipt.simulation_id = 'sim_' + 'f'.repeat(32), v => v.progress.completed = 99, v => v.model_calls_started = false, v => v.project_revision++, v => v.actors[0].name = 'other', v => v.projection_sha256 = 'f'.repeat(64)]) { const v = readyFixture(); mutate(v); await assert.rejects(validatePreparationResult(v, { graph: 'graph_1', payload: p, known }), code('invalid_reply')) }
})
test('private transport has only explicit POST routes, preserves flags and fences lost Start before recovery', async () => {
  const calls = [], known = fixture()
  const client = createWorkbenchClient({ fetchImpl: async (url, options) => {
    calls.push({ url, options })
    if (url.includes('/data/')) return envelope(graph)
    if (url.endsWith('/plan')) return envelope(known)
    if (url.endsWith('/start')) throw new Error('provider private-token raw detail')
    return envelope(readyFixture())
  } })
  await client.connect(credentials); assert.equal(calls.length, 1)
  await client.preparationPlan(payload, source); const p = preparationIdentity(known)
  await assert.rejects(client.preparationStart(p), code('transport_failure'))
  await assert.rejects(client.preparationStart(p), code('conflict'))
  assert.equal((await client.preparationStatus(p)).state, 'ready'); assert.equal(calls.length, 4)
  assert.deepEqual(calls.slice(1).map(c => new URL(c.url).pathname), ['/api/preparation/graph_1/plan', '/api/preparation/graph_1/start', '/api/preparation/graph_1/status'])
  for (const { options } of calls.slice(1)) { assert.equal(options.method, 'POST'); assert.equal(options.redirect, 'error'); assert.equal(options.credentials, 'omit'); assert.equal(options.cache, 'no-store'); assert.equal(options.referrerPolicy, 'no-referrer'); assert.equal(options.headers.Authorization, 'Bearer private-token'); assert.ok(options.signal.aborted) }
  assert.deepEqual(JSON.parse(calls[2].options.body), JSON.parse(calls[3].options.body))
  client.disconnect(); await assert.rejects(client.preparationStatus(p), code('disconnected'))
})
test('malformed 401 clears authorization before body validation; fixed errors contain no raw details', async () => {
  let reply = envelope(fixture()), calls = 0
  const client = createWorkbenchClient({ fetchImpl: async url => { calls++; return url.includes('/data/') ? envelope(graph) : reply } })
  await client.connect(credentials); await client.preparationPlan(payload, source)
  reply = new Response('private-token raw', { status: 401, headers: { 'Content-Type': 'text/html' } })
  await assert.rejects(client.preparationStatus(preparationIdentity(fixture())), code('unauthorized'))
  await assert.rejects(client.preparationStatus(preparationIdentity(fixture())), code('disconnected')); assert.equal(calls, 3)
})
test('unknown/error-extra/duplicate envelopes and oversize bodies fail closed', async () => {
  for (const reply of [new Response('{"success":true,"success":true,"data":{}}', { headers: { 'Content-Type': 'application/json' } }), new Response(JSON.stringify({ success: false, error: { code: 'preparation_failed', detail: 'raw' } }), { status: 500, headers: { 'Content-Type': 'application/json' } }), new Response(JSON.stringify({ success: false, error: { code: 'raw_provider_error' } }), { status: 500, headers: { 'Content-Type': 'application/json' } }), new Response('{}', { headers: { 'Content-Type': 'application/json', 'Content-Length': '263169' } })]) {
    const client = createWorkbenchClient({ fetchImpl: async url => url.includes('/data/') ? envelope(graph) : reply }); await client.connect(credentials)
    await assert.rejects(client.preparationPlan(payload, source), e => ['invalid_reply', 'result_too_large'].includes(e.code)); client.disconnect()
  }
})
test('canonical ASCII plan digest binds Unicode source, actor and scenario on plan and status', async () => {
  assert.equal(producerAscii('中😀\u007f'), '"\\u4e2d\\ud83d\\ude00\\u007f"')
  const value = fixture()
  assert.equal(value.plan_sha256, producerPlanSha(value))
  assert.notEqual(value.plan_sha256, createHash('sha256').update(JSON.stringify({ schema_version: value.schema_version, display_graph_id: value.display_graph_id, scope: value.scope, project_revision: value.project_revision, operation_id: value.operation_id, source: value.source, options: value.options, actors: value.actors, projection_sha256: value.projection_sha256 })).digest('hex'))
  const admitted = await validatePreparationResult(value, { graph: 'graph_1', payload, source, planning: true })
  assert.equal(admitted.plan_sha256, value.plan_sha256)
  const divergent = fixture(); divergent.plan_sha256 = 'f'.repeat(64)
  await assert.rejects(validatePreparationResult(divergent, { graph: 'graph_1', payload, source, planning: true }), code('invalid_reply'))
  const forgedKnown = clone(divergent), forgedIdentity = preparationIdentity(forgedKnown)
  // Even matching known/request hashes cannot substitute for recomputing public identity.
  await assert.rejects(validatePreparationResult(divergent, { graph: 'graph_1', payload: forgedIdentity, known: forgedKnown }), code('invalid_reply'))
})
test('producer name and ceiling bounds reject correctly rehashed malformed plans', async () => {
  for (const mutate of [v => v.actors[0].name = ' ', v => v.actors[0].name = '😀'.repeat(1025), v => v.actors[0].name = 'actor\0name', v => v.actors[0].name = '\ud800', v => v.source.source_name = ' ', v => v.source.source_name = '中'.repeat(257), v => v.source.source_name = 'source\0name', v => v.source.source_name = '\udc00', v => v.options.simulation_requirement = 'requirement\0text', v => v.authorization.ceiling_microusd = '9223372036854775808', v => v.authorization.ceiling_microusd = '9'.repeat(20), v => v.authorization.ceiling_microusd = '１２', v => v.authorization.ceiling_microusd = '01', v => v.authorization.ceiling_microusd = null]) {
    const v = fixture(); mutate(v); v.plan_sha256 = producerPlanSha(v)
    const inspected = { ...source, source_name: v.source.source_name }, request = { ...clone(payload), options: clone(v.options) }
    await assert.rejects(validatePreparationResult(v, { graph: 'graph_1', payload: request, source: inspected, planning: true }), code('invalid_reply'))
  }
  const v = fixture(); v.actors[0].name = '😀'.repeat(1024); v.source.source_name = '中'.repeat(256); v.authorization.ceiling_microusd = '9223372036854775807'; v.plan_sha256 = producerPlanSha(v)
  assert.equal((await validatePreparationResult(v, { graph: 'graph_1', payload, source: { ...source, source_name: v.source.source_name }, planning: true })).actors[0].name, v.actors[0].name)
  v.authorization = { model_calls_enabled: false, ceiling_microusd: null }
  assert.equal((await validatePreparationResult(v, { graph: 'graph_1', payload, source: { ...source, source_name: v.source.source_name }, planning: true })).authorization.ceiling_microusd, null)
})
test('async digest admission freezes reply, source, payload and known before external mutations', async () => {
  const v = fixture(), request = clone(payload), inspected = clone(source), expected = clone(v)
  const pending = validatePreparationResult(v, { graph: 'graph_1', payload: request, source: inspected, planning: true })
  v.actors[0].name = 'late mutation'; v.options.simulation_requirement = 'late scenario'; v.plan_sha256 = 'f'.repeat(64); request.options.seed = 99; inspected.source_name = 'late source'
  assert.deepEqual(await pending, expected)
  const known = fixture(), ready = readyFixture(), identity = preparationIdentity(known), readyExpected = clone(ready)
  const status = validatePreparationResult(ready, { graph: 'graph_1', payload: identity, known })
  ready.receipt.files[0].sha256 = 'f'.repeat(64); ready.actors[0].name = 'late actor'; known.project_revision++; identity.plan_sha256 = 'e'.repeat(64)
  assert.deepEqual(await status, readyExpected)
})
test('actual HTTP409 policy denials preserve protected status recovery and fence another Start', async () => {
  for (const denial of ['model_calls_disabled', 'budget_denied']) {
    const calls = [], known = fixture()
    const client = createWorkbenchClient({ fetchImpl: async (url, options) => {
      calls.push({ url, options })
      if (url.includes('/data/')) return envelope(graph)
      if (url.endsWith('/plan')) return envelope(known)
      if (url.endsWith('/start')) return new Response(JSON.stringify({ success: false, error: { code: denial } }), { status: 409, headers: { 'Content-Type': 'application/json' } })
      const status = fixture(); status.authorization = { model_calls_enabled: false, ceiling_microusd: null }; return envelope(status)
    } })
    await client.connect(credentials); await client.preparationPlan(payload, source)
    const identity = preparationIdentity(known)
    await assert.rejects(client.preparationStart(identity), code(denial))
    await assert.rejects(client.preparationStart(identity), code('conflict'))
    assert.equal((await client.preparationStatus(identity)).operation_id, identity.operation_id)
    assert.equal(calls.filter(c => c.url.endsWith('/start')).length, 1)
    assert.equal(calls.length, 4); assert.equal(calls[3].options.headers.Authorization, 'Bearer private-token')
    assert.deepEqual(JSON.parse(calls[2].options.body), JSON.parse(calls[3].options.body)); client.disconnect()
  }
})
