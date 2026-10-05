import test from 'node:test'
import assert from 'node:assert/strict'
import { webcrypto, createHash } from 'node:crypto'
import { createWorkbenchClient } from '../src/api/workbench.js'
import { preparationPayload, preparationInteger, preparationIdentity, validatePreparationResult } from '../src/api/simulationPreparation.js'
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
