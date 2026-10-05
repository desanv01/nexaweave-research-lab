import test from 'node:test'
import assert from 'node:assert/strict'
import { createHash, webcrypto } from 'node:crypto'
import { createWorkbenchClient } from '../src/api/workbench.js'
import { nativeLaunchIdentity, nativeLaunchPayload, nativeReadyPreparation, validateNativeLaunchResult } from '../src/api/nativeLaunch.js'
import { preparationIdentity } from '../src/api/simulationPreparation.js'
globalThis.crypto ||= webcrypto
const uid = n => `00000000-0000-0000-0000-${String(n).padStart(12, '0')}`
const hash = 'a'.repeat(64), clone = v => JSON.parse(JSON.stringify(v)), code = expected => e => e.code === expected
// Independent recursive producer, matching Python sorted compact ensure_ascii JSON.
function producer(v) {
  if (Array.isArray(v)) return '[' + v.map(producer).join(',') + ']'
  if (v && typeof v === 'object') return '{' + Object.keys(v).sort().map(k => producer(k) + ':' + producer(v[k])).join(',') + '}'
  const raw = JSON.stringify(v); let out = ''
  for (let i = 0; i < raw.length; i++) out += raw.charCodeAt(i) >= 127 ? '\\u' + raw.charCodeAt(i).toString(16).padStart(4, '0') : raw[i]
  return out
}
const digest = v => createHash('sha256').update(producer(v), 'ascii').digest('hex')
const pick = (v, keys) => Object.fromEntries(keys.split(' ').map(k => [k, v[k]]))
const source = { project_id: uid(2), source_revision: uid(5), source_name: '<script>中😀</script>', text_sha256: hash, byte_length: 8, codepoint_length: 4, recorded_at: '2026-10-05T00:00:00Z' }
const prepPayload = { schema_version: 1, operation_id: uid(9), source_revision: uid(5), options: { types: null, max_agents: 10, seed: 4294967295, platforms: ['twitter', 'reddit'], max_rounds: 24, simulation_requirement: 'Study 中😀' } }
function ready() {
  const files = ['state.json', 'simulation_config.json', 'source_grounding.json', 'twitter_profiles.csv', 'reddit_profiles.json'].map(name => ({ name, sha256: hash, size: 123 }))
  const v = { schema_version: 1, display_graph_id: 'graph_1', scope: { schema_version: 1, workspace_id: uid(1), project_id: uid(2), graph_id: uid(3), run_id: null, branch_id: null, layer: 'source' }, project_revision: 1, operation_id: uid(9), source: { source_revision: uid(5), source_name: source.source_name, source_sha256: hash }, options: clone(prepPayload.options), actors: [{ source_entity_uuid: uid(7), name: '中😀', labels: ['Person'] }], projection_sha256: hash, plan_sha256: null, state: 'ready', progress: { stage: 'ready', completed: 100, total: 100 }, error_code: null, authorization: { model_calls_enabled: true, ceiling_microusd: '12345' }, receipt: { simulation_id: 'sim_' + uid(9).replaceAll('-', ''), artifact_sha256: digest({ schema_version: 1, files }), files }, graph_snapshot_atomic: false, model_calls_started: true, simulation_executed: false }
  v.plan_sha256 = digest(pick(v, 'schema_version display_graph_id scope project_revision operation_id source options actors projection_sha256')); return v
}
function launch() {
  const p = ready()
  const v = { schema_version: 1, display_graph_id: 'graph_1', scope: clone(p.scope), preparation: { operation_id: p.operation_id, plan_sha256: p.plan_sha256, simulation_id: p.receipt.simulation_id, artifact_sha256: p.receipt.artifact_sha256 }, request: { schema_version: 1, principal: 'local-research', project_id: uid(2), project_revision: 1, simulation_id: p.receipt.simulation_id, run_id: uid(10), artifact_sha256: p.receipt.artifact_sha256, runtime_sha256: hash, platforms: ['twitter', 'reddit'], seed: 4294967295, max_rounds: 24 }, limits: { max_calls: 10000, max_input_bytes: 2097152, max_output_tokens: 4096, max_run_seconds: 600 }, ceiling_microusd: '9223372036854775807', model_label: 'scripted 中😀', launch_sha256: null, state: 'planned', error_code: null, authorization: { model_calls_enabled: true }, workflow: null, receipt: null, cancel_requested: false, cleanup: { known: false, pending: null, owner_thread_alive: null } }
  rehash(v); return v
}
function rehash(v) { v.launch_sha256 = digest(pick(v, 'schema_version display_graph_id scope preparation request limits ceiling_microusd model_label')); return v }
const planPayload = () => ({ schema_version: 1, launch_id: uid(10), preparation: { operation_id: uid(9), plan_sha256: ready().plan_sha256 } })
const context = () => ({ graph: 'graph_1', payload: planPayload(), preparation: ready(), planning: true })
function observed(state = 'completed') {
  const v = launch(); v.state = state
  v.workflow = { workflow_id: 'mf-native-v1-' + uid(10).replaceAll('-', '') + '-' + digest(v.request), temporal_run_id: uid(11), native_run_id: uid(10) }
  if (['completed', 'failed', 'cancelled'].includes(state)) v.receipt = { run_id: uid(10), attempt_id: uid(12), instance_id: uid(13), request_fingerprint: digest(v.request), outcome: state, evidence_sha256: hash }
  if (state === 'uncertain') v.error_code = 'native_launch_uncertain'
  return v
}
const envelope = v => new Response(JSON.stringify({ success: true, data: v }), { headers: { 'Content-Type': 'application/json' } })
const graph = { graph_id: 'graph_1', nodes: [], edges: [], node_count: 0, edge_count: 0 }
const credentials = { origin: 'http://127.0.0.1:5001', graph: 'graph_1', token: 'private-token' }
async function admitted(nativeReply = (_url, _options) => envelope(launch()), options = {}) {
  const calls = [], p = ready(), planned = { ...clone(p), state: 'planned', progress: { stage: 'planned', completed: 0, total: 100 }, model_calls_started: false, receipt: null }
  const client = createWorkbenchClient({ ...options, fetchImpl: async (url, request) => {
    calls.push({ url, request })
    if (url.includes('/data/')) return envelope(graph)
    if (url.includes('/preparation/')) return envelope(url.endsWith('/plan') ? planned : p)
    return nativeReply(url, request)
  } })
  await client.connect(credentials); await client.preparationPlan(prepPayload, source); await client.preparationStart(preparationIdentity(p))
  return { client, calls, ready: p }
}
test('strict payloads admit only identifiers, never paths, budgets or model choices', () => {
  assert.deepEqual(JSON.parse(nativeLaunchPayload(planPayload(), true)), planPayload())
  for (const mutate of [v => v.extra = true, v => v.preparation.path = 'secret', v => v.launch_id = uid(10).toUpperCase().replace('00000000', 'AAAAAAAA'), v => v.schema_version = true, v => v.preparation.plan_sha256 = hash.toUpperCase()]) { const v = planPayload(); mutate(v); assert.throws(() => nativeLaunchPayload(v, true), code('invalid_request')) }
  assert.throws(() => nativeLaunchPayload({ ...nativeLaunchIdentity(launch()), ceiling_microusd: '1' }), code('invalid_request'))
})
test('independent ASCII hashes bind Unicode plan and exact native request fingerprints', async () => {
  assert.equal(producer('中😀\u007f'), '"\\u4e2d\\ud83d\\ude00\\u007f"')
  const v = launch(), admitted = await validateNativeLaunchResult(v, context())
  assert.equal(admitted.launch_sha256, v.launch_sha256)
  v.model_label = 'mutated'; assert.notEqual(admitted.model_label, v.model_label)
  const completed = observed(), result = await validateNativeLaunchResult(completed, { ...context(), planning: false, payload: nativeLaunchIdentity(launch()), known: launch() })
  assert.equal(result.receipt.request_fingerprint, digest(completed.request)); assert.equal(result.cleanup.known, false)
  const fake = launch(); fake.launch_sha256 = hash
  await assert.rejects(validateNativeLaunchResult(fake, context()), code('invalid_reply'))
})
test('correctly rehashed out-of-bounds plans and opposite preparation correspondence fail closed', async () => {
  for (const mutate of [v => v.limits.max_calls = true, v => v.limits.max_calls = 10001, v => v.limits.max_input_bytes = 2097153, v => v.limits.max_output_tokens = 4097, v => v.limits.max_run_seconds = 601, v => v.limits.max_calls = 0, v => v.request.seed = -1, v => v.request.seed = 4294967296, v => v.request.seed = 0, v => v.request.max_rounds = 25, v => v.request.max_rounds = 1, v => v.request.project_revision = 2, v => v.request.project_id = uid(44), v => v.request.platforms.reverse(), v => v.request.simulation_id = 'sim_other', v => v.request.artifact_sha256 = hash, v => v.request.principal = ' ', v => v.request.runtime_sha256 = 'A'.repeat(64), v => v.scope.layer = 'simulation', v => v.preparation.operation_id = uid(44), v => v.preparation.plan_sha256 = hash, v => v.ceiling_microusd = '01', v => v.ceiling_microusd = '9223372036854775808', v => v.model_label = '\u001c', v => v.model_label = '\ud800', v => v.model_label = 'a\0b', v => v.model_label = '😀'.repeat(129), v => v.extra = false]) {
    const v = launch(); mutate(v); rehash(v); await assert.rejects(validateNativeLaunchResult(v, context()), code('invalid_reply'))
  }
  const v = launch(); v.authorization.model_calls_enabled = false; v.ceiling_microusd = null; rehash(v)
  assert.equal((await validateNativeLaunchResult(v, context())).authorization.model_calls_enabled, false)
  for (const mutate of [p => p.state = 'planned', p => p.receipt.files[0].sha256 = hash.replace('a', 'b'), p => p.plan_sha256 = hash]) { const p = ready(); mutate(p); await assert.rejects(nativeReadyPreparation(p, 'graph_1'), code('invalid_reply')) }
})
test('state, receipt, workflow and cleanup are complete independent authority checks', async () => {
  const ctx = { ...context(), planning: false, payload: nativeLaunchIdentity(launch()), known: launch() }
  for (const mutate of [v => v.receipt.outcome = 'cancelled', v => v.receipt.run_id = uid(88), v => v.receipt.request_fingerprint = hash, v => v.receipt.extra = true, v => v.receipt.instance_id = 'bad', v => v.receipt.evidence_sha256 = 'X', v => v.state = 'running', v => v.workflow.native_run_id = uid(88), v => v.workflow.workflow_id = 'mf-native-v1-unrelated', v => v.workflow.temporal_run_id = 'bad', v => v.cleanup.pending = false, v => v.cleanup.owner_thread_alive = false, v => v.cleanup.known = true, v => v.cancel_requested = 1, v => v.authorization.extra = true, v => v.error_code = 'private_provider_error']) { const v = observed(); mutate(v); await assert.rejects(validateNativeLaunchResult(v, ctx), code('invalid_reply')) }
  const terminalWithoutProof = observed('failed'); terminalWithoutProof.receipt = null
  assert.equal((await validateNativeLaunchResult(terminalWithoutProof, ctx)).receipt, null)
  const fabricatedCompletion = observed(); fabricatedCompletion.receipt = null
  await assert.rejects(validateNativeLaunchResult(fabricatedCompletion, ctx), code('invalid_reply'))
  const unknown = observed('uncertain'); assert.equal((await validateNativeLaunchResult(unknown, ctx)).cleanup.pending, null)
  const cleaned = observed(); cleaned.cleanup = { known: true, pending: false, owner_thread_alive: false }
  assert.equal((await validateNativeLaunchResult(cleaned, ctx)).cleanup.known, true)
  const regression = observed(); regression.receipt = null
  await assert.rejects(validateNativeLaunchResult(regression, { ...ctx, known: observed() }), code('invalid_reply'))
  const intent = observed('running'); intent.cancel_requested = true
  await assert.rejects(validateNativeLaunchResult(observed('running'), { ...ctx, known: intent }), code('invalid_reply'))
})
test('asynchronous validation freezes all caller-owned correspondence before digest awaits', async () => {
  const v = launch(), ctx = context(), expected = clone(v), pending = validateNativeLaunchResult(v, ctx)
  v.model_label = 'mutation'; ctx.preparation.options.seed = 0; ctx.payload.launch_id = uid(88)
  assert.deepEqual(await pending, expected)
})
test('private client rejects unadmitted READY, then fences lost Start and explicitly recovers exact POST identity', async () => {
  const { client, calls, ready: p } = await admitted((url) => {
    if (url.includes('/start/')) throw new Error('private-token provider payload')
    return envelope(url.includes('/status/') ? observed('running') : launch())
  })
  const forged = clone(p); forged.source.source_name = 'different'
  await assert.rejects(client.nativeLaunchPlan(planPayload(), forged), code('invalid_request')); assert.equal(calls.length, 3)
  await client.nativeLaunchPlan(planPayload(), p); const identity = nativeLaunchIdentity(launch())
  await assert.rejects(client.nativeLaunchStart(identity), code('transport_failure'))
  await assert.rejects(client.nativeLaunchStart(identity), code('conflict'))
  assert.equal((await client.nativeLaunchStatus(identity)).state, 'running')
  const nativeCalls = calls.slice(3)
  assert.deepEqual(nativeCalls.map(c => new URL(c.url).pathname), ['/api/native-launch/plan/graph_1', '/api/native-launch/start/graph_1', '/api/native-launch/status/graph_1'])
  assert.equal(nativeCalls[1].request.body, nativeCalls[2].request.body)
  for (const { request } of nativeCalls) { assert.equal(request.method, 'POST'); assert.equal(request.headers.Authorization, 'Bearer private-token'); assert.equal(request.headers['Content-Type'], 'application/json'); assert.equal(request.redirect, 'error'); assert.equal(request.credentials, 'omit'); assert.equal(request.cache, 'no-store'); assert.equal(request.referrerPolicy, 'no-referrer'); assert.ok(request.signal.aborted) }
  client.clearNativeLaunch(); await assert.rejects(client.nativeLaunchStatus(identity), code('invalid_request'))
  await client.nativeLaunchPlan(planPayload(), p); await assert.rejects(client.nativeLaunchStart(identity), code('conflict'))
  client.disconnect(); await assert.rejects(client.nativeLaunchStatus(identity), code('disconnected'))
})
test('operational 409/503 denials preserve recovery; malformed auth/origin denials clear it', async () => {
  for (const [denial, status] of [['budget_denied', 409], ['model_calls_disabled', 409], ['native_launch_uncertain', 503], ['native_launch_unavailable', 503]]) {
    const { client, ready: p } = await admitted(url => url.includes('/start/') ? new Response(JSON.stringify({ success: false, error: { code: denial } }), { status, headers: { 'Content-Type': 'application/json' } }) : envelope(url.includes('/status/') ? observed('uncertain') : launch()))
    await client.nativeLaunchPlan(planPayload(), p)
    await assert.rejects(client.nativeLaunchStart(nativeLaunchIdentity(launch())), code(denial))
    await assert.rejects(client.nativeLaunchStart(nativeLaunchIdentity(launch())), code('conflict'))
    assert.equal((await client.nativeLaunchStatus(nativeLaunchIdentity(launch()))).state, 'uncertain'); client.disconnect()
  }
  for (const [status, expected] of [[401, 'unauthorized'], [403, 'origin_denied']]) {
    const { client, ready: p } = await admitted(url => url.includes('/status/') ? new Response('secret', { status }) : envelope(launch()))
    await client.nativeLaunchPlan(planPayload(), p)
    await assert.rejects(client.nativeLaunchStatus(nativeLaunchIdentity(launch())), code(expected))
    await assert.rejects(client.nativeLaunchStatus(nativeLaunchIdentity(launch())), code('disconnected'))
  }
})
test('bounded duplicate-key, unsafe envelope, wrong-status, oversize and non-UTF8 replies are rejected', async () => {
  for (const makeReply of [() => new Response('{"success":true,"success":true,"data":{}}', { headers: { 'Content-Type': 'application/json' } }), () => new Response('{"success":false,"error":{"code":"busy","detail":"secret"}}', { status: 503, headers: { 'Content-Type': 'application/json' } }), () => new Response('{"success":false,"error":{"code":"budget_denied"}}', { status: 503, headers: { 'Content-Type': 'application/json' } }), () => new Response('{}', { headers: { 'Content-Type': 'application/json', 'Content-Length': '65665' } }), () => new Response(new Uint8Array([0xff]), { headers: { 'Content-Type': 'application/json' } }), () => new Response(' '.repeat(65665), { headers: { 'Content-Type': 'application/json' } })]) {
    const { client, ready: p } = await admitted(makeReply)
    await assert.rejects(client.nativeLaunchPlan(planPayload(), p), e => ['invalid_reply', 'result_too_large'].includes(e.code)); client.disconnect()
  }
})
test('cancellation intent is not a terminal receipt; in-flight cancellation and disconnect cannot revive context', async () => {
  let resolve, calls = 0
  const { client, ready: p } = await admitted(url => {
    if (url.includes('/cancel/')) { calls++; const v = observed('running'); v.cancel_requested = true; return envelope(v) }
    if (url.includes('/status/')) return new Promise(r => { resolve = r })
    return envelope(launch())
  })
  await client.nativeLaunchPlan(planPayload(), p)
  const v = await client.nativeLaunchCancel(nativeLaunchIdentity(launch()))
  assert.equal(v.cancel_requested, true); assert.equal(v.receipt, null); assert.equal(v.cleanup.known, false); assert.equal(calls, 1)
  const pending = client.nativeLaunchStatus(nativeLaunchIdentity(launch())); client.disconnect(); resolve(envelope(observed()))
  await assert.rejects(pending, code('cancelled')); await assert.rejects(client.nativeLaunchStatus(nativeLaunchIdentity(launch())), code('disconnected'))
})
test('finite deadline aborts a noncooperative lost Start and leaves only same-id status recovery', async () => {
  let startSignal
  const { client, ready: p } = await admitted((url, options) => {
    if (url.includes('/start/')) { startSignal = options.signal; return new Promise(() => {}) }
    return envelope(url.includes('/status/') ? observed('uncertain') : launch())
  }, { deadlineMs: 100 })
  await client.nativeLaunchPlan(planPayload(), p)
  const identity = nativeLaunchIdentity(launch())
  await assert.rejects(client.nativeLaunchStart(identity), code('deadline')); assert.equal(startSignal.aborted, true)
  await assert.rejects(client.nativeLaunchStart(identity), code('conflict'))
  assert.equal((await client.nativeLaunchStatus(identity)).state, 'uncertain'); client.disconnect()
})
test('explicit new review freezes changed operator configuration while a denied identity stays fenced', async () => {
  let enabled = false, first, second, starts = 0
  const { client, ready: p } = await admitted((url, options) => {
    const body = JSON.parse(options.body)
    if (url.includes('/plan/')) {
      const v = launch(); v.request.run_id = body.launch_id
      if (!enabled) { v.authorization.model_calls_enabled = false; v.ceiling_microusd = null }
      rehash(v); if (!first) first = clone(v); else second = clone(v); return envelope(v)
    }
    if (url.includes('/start/')) { starts++; return new Response('{"success":false,"error":{"code":"budget_denied"}}', { status: 409, headers: { 'Content-Type': 'application/json' } }) }
    return envelope(body.launch_id === first.request.run_id ? first : second)
  })
  await client.nativeLaunchPlan(planPayload(), p); await assert.rejects(client.nativeLaunchStart(nativeLaunchIdentity(first)), code('model_calls_disabled')); assert.equal(starts, 0)
  enabled = true; const newReview = { ...planPayload(), launch_id: uid(20) }
  await client.nativeLaunchPlan(newReview, p); assert.notEqual(first.launch_sha256, second.launch_sha256)
  await assert.rejects(client.nativeLaunchStart(nativeLaunchIdentity(second)), code('budget_denied')); await assert.rejects(client.nativeLaunchStart(nativeLaunchIdentity(second)), code('conflict'))
  assert.equal((await client.nativeLaunchStatus(nativeLaunchIdentity(first))).ceiling_microusd, null)
  assert.equal((await client.nativeLaunchStatus(nativeLaunchIdentity(second))).request.run_id, uid(20)); assert.equal(starts, 1); client.disconnect()
})
test('clearing native context leaves pending graph connection and source transport epochs intact', async () => {
  const library = { schema_version: 1, binary_retained: false, graph_ingestion_executed: false, sources: [], has_more: false, window_limit: 20 }
  let finishGraph, finishSource, graphSignal, sourceSignal
  const client = createWorkbenchClient({ fetchImpl: (url, options) => {
    if (url.includes('/data/')) { graphSignal = options.signal; return new Promise(resolve => { finishGraph = resolve }) }
    if (url.includes('/source/library/')) { sourceSignal = options.signal; return new Promise(resolve => { finishSource = resolve }) }
    throw new Error('unexpected route')
  } })
  const connecting = client.connect(credentials)
  client.clearNativeLaunch(); client.clearNativeLaunch()
  assert.equal(graphSignal.aborted, false)
  finishGraph(envelope(graph)); assert.deepEqual(await connecting, graph)
  const reading = client.sourceList()
  client.clearNativeLaunch(); assert.equal(sourceSignal.aborted, false)
  finishSource(envelope(library)); assert.deepEqual(await reading, library)
  client.disconnect()
})
test('native clear aborts its active Start, discards late result and preserves the spent Start fence', async () => {
  let finish, signal, starts = 0
  const { client, ready: p } = await admitted((url, options) => {
    if (url.includes('/start/')) { starts++; signal = options.signal; return new Promise(resolve => { finish = resolve }) }
    return envelope(launch())
  })
  await client.nativeLaunchPlan(planPayload(), p)
  const pending = client.nativeLaunchStart(nativeLaunchIdentity(launch()))
  const rejected = assert.rejects(pending, code('cancelled'))
  client.clearNativeLaunch(); assert.equal(signal.aborted, true)
  finish(envelope(observed())); await rejected
  await assert.rejects(client.nativeLaunchStatus(nativeLaunchIdentity(launch())), code('invalid_request'))
  await client.nativeLaunchPlan(planPayload(), p)
  await assert.rejects(client.nativeLaunchStart(nativeLaunchIdentity(launch())), code('conflict'))
  assert.equal(starts, 1); client.disconnect()
})
