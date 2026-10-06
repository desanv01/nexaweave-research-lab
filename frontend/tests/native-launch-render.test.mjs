import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createHash, webcrypto } from 'node:crypto'
import { JSDOM } from 'jsdom'
import { parse, compileScript } from '@vue/compiler-sfc'
import { nativeLaunchCopy, nativeLaunchCopyFor } from '../src/i18n/nativeLaunch.js'
import { createWorkbenchClient } from '../src/api/workbench.js'
import { preparationIdentity } from '../src/api/simulationPreparation.js'
globalThis.crypto ||= webcrypto
const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://127.0.0.1:5173/research' })
for (const name of ['window', 'document', 'Element', 'HTMLElement', 'SVGElement', 'Node']) globalThis[name] = name === 'window' ? dom.window : dom.window[name]
const { createApp, h, nextTick, reactive } = await import('vue')
const file = new URL('../src/components/workbench/NativeLaunch.vue', import.meta.url)
const { descriptor } = parse(readFileSync(file, 'utf8'), { filename: file.pathname })
const compiled = compileScript(descriptor, { id: 'native-launch', inlineTemplate: true, genDefaultAs: '__component' })
let code = compiled.content.replace(/from (['"])vue\1/g, `from ${JSON.stringify(import.meta.resolve('vue'))}`)
code = code.replace(/from (['"])(\.{1,2}\/[^'"]+)\1/g, (_m, _q, relative) => `from ${JSON.stringify(new URL(relative, file).href)}`)
const component = (await import(`data:text/javascript;base64,${Buffer.from(code + '\nexport default __component').toString('base64')}`)).default
const uid = n => `00000000-0000-0000-0000-${String(n).padStart(12, '0')}`, hash = 'a'.repeat(64), clone = v => JSON.parse(JSON.stringify(v))
const hostile = '<img src=x onerror=alert(1)> <script>secret</script> 中😀 https://evil.invalid/'
// Independent producer hashes; do not use any frontend digest helper.
function ascii(v) {
  if (Array.isArray(v)) return '[' + v.map(ascii).join(',') + ']'
  if (v && typeof v === 'object') return '{' + Object.keys(v).sort().map(k => ascii(k) + ':' + ascii(v[k])).join(',') + '}'
  const raw = JSON.stringify(v); let out = ''
  for (let i = 0; i < raw.length; i++) out += raw.charCodeAt(i) >= 127 ? '\\u' + raw.charCodeAt(i).toString(16).padStart(4, '0') : raw[i]
  return out
}
const digest = v => createHash('sha256').update(ascii(v), 'ascii').digest('hex')
const pick = (v, keys) => Object.fromEntries(keys.split(' ').map(k => [k, v[k]]))
function ready() {
  const files = ['state.json', 'simulation_config.json', 'source_grounding.json', 'twitter_profiles.csv', 'reddit_profiles.json'].map(name => ({ name, sha256: hash, size: 123 }))
  const v = { schema_version: 1, display_graph_id: 'graph_1', scope: { schema_version: 1, workspace_id: uid(1), project_id: uid(2), graph_id: uid(3), run_id: null, branch_id: null, layer: 'source' }, project_revision: 1, operation_id: uid(9), source: { source_revision: uid(5), source_name: hostile, source_sha256: hash }, options: { types: null, max_agents: 10, seed: 0, platforms: ['twitter', 'reddit'], max_rounds: 24, simulation_requirement: hostile }, actors: [{ source_entity_uuid: uid(7), name: hostile, labels: ['Person'] }], projection_sha256: hash, plan_sha256: null, state: 'ready', progress: { stage: 'ready', completed: 100, total: 100 }, error_code: null, authorization: { model_calls_enabled: true, ceiling_microusd: '12345' }, receipt: { simulation_id: 'sim_' + uid(9).replaceAll('-', ''), artifact_sha256: digest({ schema_version: 1, files }), files }, graph_snapshot_atomic: false, model_calls_started: true, simulation_executed: false }
  v.plan_sha256 = digest(pick(v, 'schema_version display_graph_id scope project_revision operation_id source options actors projection_sha256')); return v
}
function planned(payload, p) {
  const v = { schema_version: 1, display_graph_id: 'graph_1', scope: clone(p.scope), preparation: { ...payload.preparation, simulation_id: p.receipt.simulation_id, artifact_sha256: p.receipt.artifact_sha256 }, request: { schema_version: 1, principal: 'local-research', project_id: p.scope.project_id, project_revision: p.project_revision, simulation_id: p.receipt.simulation_id, run_id: payload.launch_id, artifact_sha256: p.receipt.artifact_sha256, runtime_sha256: hash, platforms: clone(p.options.platforms), seed: p.options.seed, max_rounds: p.options.max_rounds }, limits: { max_calls: 20, max_input_bytes: 2097152, max_output_tokens: 4096, max_run_seconds: 600 }, ceiling_microusd: '12345', model_label: hostile, launch_sha256: null, state: 'planned', error_code: null, authorization: { model_calls_enabled: true }, workflow: null, receipt: null, cancel_requested: false, cleanup: { known: false, pending: null, owner_thread_alive: null } }
  v.launch_sha256 = digest(pick(v, 'schema_version display_graph_id scope preparation request limits ceiling_microusd model_label')); return v
}
function observed(known, state = 'running') {
  const v = clone(known); v.state = state
  v.workflow = { workflow_id: 'mf-native-v1-' + v.request.run_id.replaceAll('-', '') + '-' + digest(v.request), temporal_run_id: uid(11), native_run_id: v.request.run_id }
  if (['completed', 'failed', 'cancelled'].includes(state)) v.receipt = { run_id: v.request.run_id, attempt_id: uid(12), instance_id: uid(13), request_fingerprint: digest(v.request), outcome: state, evidence_sha256: hash }
  return v
}
function mount(initial = {}) {
  const props = reactive({ methods: { plan: async (payload, p) => planned(payload, p) }, connected: true, ready: ready(), displayGraphId: 'graph_1', resetVersion: 0, locale: 'en', ...initial })
  const root = document.createElement('div'); document.body.append(root)
  const app = createApp({ setup: () => () => h(component, props) }); app.mount(root)
  return { props, root, cleanup() { app.unmount(); root.remove() } }
}
async function settle() { for (let i = 0; i < 20; i++) { await new Promise(resolve => setTimeout(resolve, 0)); await nextTick() } }
async function review(m) { m.root.querySelector('.review').click(); await settle() }
test('actual SFC uses explicit native controls, complete locale copy and no mount/locale fetch', async () => {
  let calls = 0; const m = mount({ connected: false, methods: { plan: () => { calls++ } } })
  try {
    assert.equal(m.root.querySelector('.review').disabled, true)
    for (const locale of ['en', 'zh', 'ms']) { m.props.locale = locale; await settle(); const c = nativeLaunchCopyFor(locale); assert.ok(m.root.textContent.includes(c.title)); assert.ok(m.root.textContent.includes(c.reservation)); assert.ok(m.root.textContent.includes(c.closing)); assert.deepEqual(Object.keys(c).sort(), Object.keys(nativeLaunchCopy.en).sort()); assert.deepEqual(Object.keys(c.errors).sort(), Object.keys(nativeLaunchCopy.en.errors).sort()); assert.deepEqual(Object.keys(c.states).sort(), Object.keys(nativeLaunchCopy.en.states).sort()) }
    assert.equal(calls, 0); assert.equal(m.root.querySelector('button').type, 'button'); assert.equal(m.root.querySelector('.feedback').getAttribute('aria-live'), 'polite')
  } finally { m.cleanup() }
})
test('review detaches READY and producer reply, renders hostile labels inertly and focuses feedback', async () => {
  let producer, input, calls = 0
  const m = mount({ methods: { plan: async (payload, p) => { calls++; input = p; return producer = planned(payload, p) } } })
  try {
    await review(m); assert.equal(calls, 1); assert.equal(m.root.querySelector('.start').disabled, false)
    producer.model_label = 'mutation'; input.source.source_name = 'mutation'; await settle()
    assert.ok(m.root.textContent.includes(hostile)); assert.equal(m.root.textContent.includes('mutation'), false); assert.equal(m.root.querySelector('a,img,script'), null)
    assert.equal(document.activeElement, m.root.querySelector('.feedback')); assert.ok(m.root.textContent.includes(nativeLaunchCopy.en.unknown)); assert.ok(m.root.querySelector('.no-receipt'))
  } finally { m.cleanup() }
})
test('lost Start permanently fences repeated start and Refresh keeps exact native identity', async () => {
  let known, starts = 0; const identities = []
  const m = mount({ methods: { plan: async (p, ready) => known = planned(p, ready), start: async p => { starts++; identities.push(clone(p)); throw Object.assign(new Error('private-token provider secret'), { code: 'transport_failure' }) }, status: async p => { identities.push(clone(p)); return observed(known, 'completed') } } })
  try {
    await review(m); m.root.querySelector('.start').click(); await settle(); m.root.querySelector('.start').click(); await settle()
    assert.equal(starts, 1); assert.equal(m.root.querySelector('.start').disabled, true); assert.equal(m.root.querySelector('.review').disabled, false); assert.ok(m.root.textContent.includes(nativeLaunchCopy.en.lost)); assert.equal(m.root.textContent.includes('private-token'), false)
    m.root.querySelector('.refresh').click(); await settle(); assert.deepEqual(identities[0], identities[1]); assert.ok(m.root.querySelector('.receipt')); assert.ok(m.root.textContent.includes(nativeLaunchCopy.en.states.completed)); assert.ok(m.root.textContent.includes(nativeLaunchCopy.en.unknown))
  } finally { m.cleanup() }
})
test('cancellation shows intent separately from receipt and cleanup observation', async () => {
  let known, cancels = 0
  const m = mount({ methods: { plan: async (p, ready) => known = planned(p, ready), start: async () => observed(known), cancel: async () => { cancels++; const v = observed(known); v.cancel_requested = true; return v }, status: async () => { const v = observed(known, 'cancelled'); v.cancel_requested = true; v.cleanup = { known: true, pending: true, owner_thread_alive: true }; return v } } })
  try {
    await review(m); assert.equal(m.root.querySelector('.cancel').disabled, false); m.root.querySelector('.start').click(); await settle()
    m.root.querySelector('.cancel').click(); await settle(); assert.equal(cancels, 1); assert.ok(m.root.querySelector('.cancellation')); assert.equal(m.root.querySelector('.receipt'), null); assert.ok(m.root.textContent.includes(nativeLaunchCopy.en.noReceipt)); assert.equal(m.root.querySelector('.cancel').disabled, true)
    m.root.querySelector('.refresh').click(); await settle(); assert.ok(m.root.querySelector('.receipt')); assert.ok(m.root.textContent.includes(nativeLaunchCopy.en.states.cancelled)); assert.ok(m.root.querySelector('.cleanup').textContent.includes(nativeLaunchCopy.en.pendingCleanup)); assert.ok(m.root.querySelector('.cleanup').textContent.includes(nativeLaunchCopy.en.alive))
  } finally { m.cleanup() }
})
test('prepared source changes cannot relabel a started native run', async () => {
  let known, identity
  const m = mount({ methods: { plan: async (p, ready) => known = planned(p, ready), start: async p => { identity = clone(p); return observed(known) }, status: async p => { assert.deepEqual(p, identity); return observed(known) } } })
  try {
    await review(m); m.root.querySelector('.start').click(); await settle(); m.props.ready = null; await settle()
    assert.ok(m.root.textContent.includes(hostile)); assert.ok(m.root.textContent.includes(nativeLaunchCopy.en.changed)); assert.equal(m.root.querySelector('.start'), null)
    m.root.querySelector('.refresh').click(); await settle(); assert.ok(m.root.querySelector('.plan'))
  } finally { m.cleanup() }
})
test('late review/start/status replies cannot revive reset, disconnect or unmounted view', async () => {
  for (const phase of ['plan', 'start', 'status']) for (const action of ['reset', 'disconnect', 'unmount']) {
    let known, finish, expected
    const methods = { plan: async (p, ready) => known = planned(p, ready), start: async () => observed(known), status: async () => observed(known) }
    const original = methods[phase]
    methods[phase] = async (...args) => { expected = await original(...args); return new Promise(resolve => { finish = resolve }) }
    const m = mount({ methods })
    await review(m)
    if (phase !== 'plan') { m.root.querySelector(phase === 'start' ? '.start' : '.refresh').click(); await settle() }
    if (action === 'reset') m.props.resetVersion++
    else if (action === 'disconnect') m.props.connected = false
    else m.cleanup()
    await settle(); finish(expected); await settle(); assert.equal(m.root.querySelector('.plan'), null)
    if (action !== 'unmount') m.cleanup()
  }
})
test('auth denial clears private rendered context; operational denial retains refresh and fence', async () => {
  for (const denial of ['unauthorized', 'origin_denied', 'budget_denied', 'native_launch_uncertain']) {
    let known, clears = 0
    const m = mount({ methods: { plan: async (p, ready) => known = planned(p, ready), start: async () => { throw Object.assign(new Error('raw-private-error-only'), { code: denial }) }, clear: () => { clears++ }, status: async () => observed(known) } })
    try {
      await review(m); m.root.querySelector('.start').click(); await settle()
      if (['unauthorized', 'origin_denied'].includes(denial)) { assert.equal(m.root.querySelector('.plan'), null); assert.ok(clears) }
      else { assert.ok(m.root.querySelector('.refresh')); assert.equal(m.root.querySelector('.start').disabled, true); m.root.querySelector('.refresh').click(); await settle(); assert.ok(m.root.querySelector('.plan')) }
      assert.equal(m.root.textContent.includes('raw-private-error-only'), false)
    } finally { m.cleanup() }
  }
})
test('invalid READY, cross-bound response and malformed receipt never render trusted execution', async () => {
  const badReady = ready(); badReady.receipt.artifact_sha256 = hash
  let calls = 0; const m = mount({ ready: badReady, methods: { plan: () => { calls++ } } })
  try { await review(m); assert.equal(calls, 0); assert.equal(m.root.querySelector('.plan'), null) } finally { m.cleanup() }
  const n = mount({ methods: { plan: async (p, ready) => { const v = planned(p, ready); v.request.seed = 1; v.launch_sha256 = digest(pick(v, 'schema_version display_graph_id scope preparation request limits ceiling_microusd model_label')); return v } } })
  try { await review(n); assert.equal(n.root.querySelector('.plan'), null); assert.ok(n.root.textContent.includes(nativeLaunchCopy.en.errors.invalid_reply)) } finally { n.cleanup() }
  let known
  const r = mount({ methods: { plan: async (p, ready) => known = planned(p, ready), start: async () => { const v = observed(known, 'completed'); v.receipt.request_fingerprint = hash; return v } } })
  try { await review(r); r.root.querySelector('.start').click(); await settle(); assert.equal(r.root.querySelector('.receipt'), null); assert.equal(r.root.querySelector('.start').disabled, true); assert.ok(r.root.textContent.includes(nativeLaunchCopy.en.errors.invalid_reply)) } finally { r.cleanup() }
})
test('disabled authorization and clear preserve explicit controls without automatic calls', async () => {
  let calls = 0, clears = 0
  const m = mount({ methods: { plan: async (p, ready) => { calls++; const v = planned(p, ready); v.authorization.model_calls_enabled = false; return v }, clear: () => { clears++ } } })
  try {
    await review(m); assert.equal(m.root.querySelector('.start').disabled, true); assert.ok(m.root.textContent.includes(nativeLaunchCopy.en.disabled)); m.props.locale = 'ms'; await settle(); assert.equal(calls, 1)
    m.root.querySelector('.clear').click(); await settle(); assert.equal(m.root.querySelector('.plan'), null); assert.equal(clears, 1); assert.equal(calls, 1)
    assert.ok(descriptor.styles[0].content.includes('min-height:44px')); assert.ok(descriptor.styles[0].content.includes('min-width:0')); assert.ok(descriptor.styles[0].content.includes('overflow-wrap:anywhere')); assert.ok(descriptor.styles[0].content.includes(':focus-visible'))
  } finally { m.cleanup() }
})
test('lost Review reuses the same declaration rather than creating another one-use run plan', async () => {
  const payloads = []; let calls = 0
  const m = mount({ methods: { plan: async (p, ready) => { payloads.push(clone(p)); if (!calls++) throw Object.assign(new Error(), { code: 'transport_failure' }); return planned(p, ready) } } })
  try { await review(m); await review(m); assert.deepEqual(payloads[0], payloads[1]); assert.ok(m.root.querySelector('.plan')) } finally { m.cleanup() }
})
test('disabled or denied immutable review can be superseded explicitly without erasing old recovery', async () => {
  let enabled = false, known, starts = 0; const declarations = [], statuses = []
  const m = mount({ methods: {
    plan: async (p, ready) => { declarations.push(clone(p)); const v = planned(p, ready); if (!enabled) { v.authorization.model_calls_enabled = false; v.ceiling_microusd = null; v.launch_sha256 = digest(pick(v, 'schema_version display_graph_id scope preparation request limits ceiling_microusd model_label')) } known = v; return v },
    start: async () => { starts++; throw Object.assign(new Error(), { code: 'budget_denied' }) },
    status: async p => { statuses.push(clone(p)); const original = planned(declarations.find(d => d.launch_id === p.launch_id), ready()); return original }
  } })
  try {
    await review(m); assert.equal(m.root.querySelector('.start').disabled, true)
    enabled = true; await review(m); assert.notEqual(declarations[0].launch_id, declarations[1].launch_id); assert.equal(m.root.querySelector('.start').disabled, false); assert.equal(starts, 0)
    const denied = clone(known); m.root.querySelector('.start').click(); await settle(); assert.equal(starts, 1); assert.equal(m.root.querySelector('.start').disabled, true)
    assert.ok(m.root.textContent.includes(nativeLaunchCopy.en.errors.budget_denied)); assert.equal(m.root.textContent.includes(nativeLaunchCopy.en.lost), false)
    await review(m); assert.notEqual(declarations[1].launch_id, declarations[2].launch_id); assert.equal(starts, 1); assert.equal(m.root.querySelectorAll('.plan').length, 2)
    const old = m.root.querySelector('[data-record="durable"]'); old.querySelector('.refresh').click(); await settle(); assert.equal(statuses[0].launch_id, denied.request.run_id); assert.equal(statuses[0].launch_sha256, denied.launch_sha256)
    assert.equal(m.root.querySelector('[data-record="plan"] .start').disabled, false)
  } finally { m.cleanup() }
})
test('cancelling an undispatched review fences Start while retaining intent-only recovery', async () => {
  let known, starts = 0
  const m = mount({ methods: { plan: async (p, ready) => known = planned(p, ready), start: () => { starts++ }, cancel: async () => { const v = clone(known); v.cancel_requested = true; v.state = 'cancelled'; return v } } })
  try { await review(m); m.root.querySelector('.cancel').click(); await settle(); m.root.querySelector('.start').click(); await settle(); assert.equal(starts, 0); assert.equal(m.root.querySelector('.start').disabled, true); assert.equal(m.root.querySelector('.receipt'), null); assert.ok(m.root.querySelector('.cancellation')); assert.ok(m.root.textContent.includes(nativeLaunchCopy.en.unknown)) } finally { m.cleanup() }
})
test('confirmed model/budget denial during Start or Cancel shows policy feedback without lost-reply wording', async () => {
  for (const method of ['start', 'cancel']) for (const denial of ['model_calls_disabled', 'budget_denied']) {
    let requests = 0
    const m = mount({ methods: { plan: async (p, ready) => planned(p, ready), [method]: async () => { requests++; throw Object.assign(new Error('raw-private-only'), { code: denial }) } } })
    try {
      await review(m); assert.equal(m.root.querySelector('.' + method).disabled, false); m.root.querySelector('.' + method).click(); await settle()
      assert.equal(requests, 1); assert.ok(m.root.textContent.includes(nativeLaunchCopy.en.errors[denial])); assert.equal(m.root.textContent.includes(nativeLaunchCopy.en.lost), false); assert.equal(m.root.textContent.includes('raw-private-only'), false); assert.equal(m.root.querySelector('.start').disabled, true); assert.ok(m.root.querySelector('.refresh'))
    } finally { m.cleanup() }
  }
})
test('mounted native resets, disconnect and unmount do not abort pending graph or source requests', async () => {
  const graph = { graph_id: 'graph_1', nodes: [], edges: [], node_count: 0, edge_count: 0 }
  const library = { schema_version: 1, binary_retained: false, graph_ingestion_executed: false, sources: [], has_more: false, window_limit: 20 }
  const response = v => new Response(JSON.stringify({ success: true, data: v }), { headers: { 'Content-Type': 'application/json' } })
  const credentials = { origin: 'http://127.0.0.1:5001', graph: 'graph_1', token: 'private-token' }
  for (const phase of ['graph', 'source']) for (const action of ['reset', 'disconnect', 'unmount']) {
    let finish, signal
    const client = createWorkbenchClient({ fetchImpl: (url, options) => {
      if (phase === 'source' && url.includes('/data/')) return Promise.resolve(response(graph))
      signal = options.signal; return new Promise(resolve => { finish = resolve })
    } })
    if (phase === 'source') await client.connect(credentials)
    const m = mount({ methods: { clear: () => client.clearNativeLaunch() } })
    const pending = phase === 'graph' ? client.connect(credentials) : client.sourceList()
    if (action === 'reset') m.props.resetVersion++
    else if (action === 'disconnect') m.props.connected = false
    else m.cleanup()
    await settle(); assert.equal(signal.aborted, false)
    finish(response(phase === 'graph' ? graph : library)); assert.deepEqual(await pending, phase === 'graph' ? graph : library)
    if (action !== 'unmount') m.cleanup()
    client.disconnect()
  }
})
test('mounted native clear aborts an actual private native request and rejects late admission', async () => {
  const graph = { graph_id: 'graph_1', nodes: [], edges: [], node_count: 0, edge_count: 0 }, p = ready()
  const response = v => new Response(JSON.stringify({ success: true, data: v }), { headers: { 'Content-Type': 'application/json' } })
  let finish, signal, nativeKnown
  const client = createWorkbenchClient({ fetchImpl: (url, options) => {
    if (url.includes('/data/')) return Promise.resolve(response(graph))
    if (url.includes('/preparation/')) { const reviewed = { ...clone(p), state: 'planned', progress: { stage: 'planned', completed: 0, total: 100 }, model_calls_started: false, receipt: null }; return Promise.resolve(response(url.endsWith('/plan') ? reviewed : p)) }
    if (url.includes('/native-launch/plan/')) { const payload = JSON.parse(options.body); nativeKnown = planned(payload, p); return Promise.resolve(response(nativeKnown)) }
    signal = options.signal; return new Promise(resolve => { finish = resolve })
  } })
  await client.connect({ origin: 'http://127.0.0.1:5001', graph: 'graph_1', token: 'private-token' })
  await client.preparationPlan({ schema_version: 1, operation_id: p.operation_id, source_revision: p.source.source_revision, options: p.options }, { project_id: p.scope.project_id, source_revision: p.source.source_revision, source_name: p.source.source_name, text_sha256: p.source.source_sha256, byte_length: 8, codepoint_length: 4, recorded_at: '2026-10-05T00:00:00Z' })
  await client.preparationStart(preparationIdentity(p))
  const m = mount({ ready: p, methods: { plan: (payload, ready) => client.nativeLaunchPlan(payload, ready), start: payload => client.nativeLaunchStart(payload), clear: () => client.clearNativeLaunch() } })
  try {
    await review(m); m.root.querySelector('.start').click(); await settle(); assert.equal(signal.aborted, false)
    m.root.querySelector('.clear').click(); await settle(); assert.equal(signal.aborted, true)
    finish(response(observed(nativeKnown, 'completed'))); await settle(); assert.equal(m.root.querySelector('.plan'), null); assert.equal(m.root.querySelector('.receipt'), null)
    await assert.rejects(client.nativeLaunchStatus({ schema_version: 1, launch_id: nativeKnown.request.run_id, launch_sha256: nativeKnown.launch_sha256 }), e => e.code === 'invalid_request')
  } finally { m.cleanup(); client.disconnect() }
})

test('Inspect emits only a detached completed receipt selection and makes no page or status request', async () => {
  let known, selected, statusCalls = 0, clears = 0
  const m = mount({
    onInspect: value => { selected = value }, onCleared: () => { clears++ },
    methods: { plan: async (p, ready) => known = planned(p, ready), status: async () => { statusCalls++; return observed(known, 'completed') } }
  })
  try {
    await review(m); assert.equal(m.root.querySelector('.inspect'), null)
    m.root.querySelector('.refresh').click(); await settle(); assert.equal(statusCalls, 1)
    const button = m.root.querySelector('.inspect'); assert.ok(button); assert.equal(button.type, 'button'); assert.equal(button.textContent, nativeLaunchCopy.en.inspect)
    button.focus(); button.click(); await settle(); assert.equal(statusCalls, 1)
    assert.equal(selected.launch.state, 'completed'); assert.equal(selected.launch.receipt.outcome, 'completed'); assert.equal(selected.preparation.state, 'ready')
    const originalSource = selected.preparation.source.source_name
    m.props.ready.source.source_name = 'later mutation'; await settle(); assert.equal(selected.preparation.source.source_name, originalSource); assert.ok(clears >= 1)
    selected.launch.model_label = 'consumer mutation'; await settle(); assert.equal(m.root.textContent.includes('consumer mutation'), false)
    m.root.querySelector('.clear').click(); await settle(); assert.ok(clears >= 2); assert.equal(m.root.querySelector('.inspect'), null)
  } finally { m.cleanup() }
})
test('failed, cancelled and uncertain native outcomes never expose completed observation selection', async () => {
  for (const state of ['running', 'failed', 'cancelled', 'uncertain']) {
    let known, inspections = 0
    const m = mount({ onInspect: () => { inspections++ }, methods: { plan: async (p, ready) => known = planned(p, ready), status: async () => observed(known, state) } })
    try { await review(m); m.root.querySelector('.refresh').click(); await settle(); assert.equal(m.root.querySelector('.inspect'), null); assert.equal(inspections, 0) } finally { m.cleanup() }
  }
})
