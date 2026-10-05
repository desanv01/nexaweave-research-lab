import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { webcrypto, createHash } from 'node:crypto'
import { JSDOM } from 'jsdom'
import { parse, compileScript } from '@vue/compiler-sfc'
import { preparationCopy, preparationCopyFor } from '../src/i18n/simulationPreparation.js'
import { createWorkbenchClient } from '../src/api/workbench.js'
globalThis.crypto ||= webcrypto
const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://127.0.0.1:5173/research' })
for (const name of ['window', 'document', 'Element', 'HTMLElement', 'SVGElement', 'Node']) globalThis[name] = name === 'window' ? dom.window : dom.window[name]
const { createApp, nextTick, h, reactive } = await import('vue')
const file = new URL('../src/components/workbench/SimulationPreparation.vue', import.meta.url)
const { descriptor } = parse(readFileSync(file, 'utf8'), { filename: file.pathname })
const compiled = compileScript(descriptor, { id: 'preparation', inlineTemplate: true, genDefaultAs: '__component' })
let code = compiled.content.replace(/from (['"])vue\1/g, `from ${JSON.stringify(import.meta.resolve('vue'))}`)
code = code.replace(/from (['"])(\.{1,2}\/[^'"]+)\1/g, (_m, _q, relative) => `from ${JSON.stringify(new URL(relative, file).href)}`)
const component = (await import(`data:text/javascript;base64,${Buffer.from(code + '\nexport default __component').toString('base64')}`)).default
const uid = n => `00000000-0000-0000-0000-${String(n).padStart(12, '0')}`, hash = 'a'.repeat(64)
const hostile = '<img src=x onerror=alert(1)> <script>secret</script> 中😀 https://evil.invalid/'
const source = { project_id: uid(2), source_revision: uid(5), source_name: hostile, text_sha256: hash, byte_length: 8, codepoint_length: 4, recorded_at: '2026-10-05T00:00:00Z' }
const clone = v => JSON.parse(JSON.stringify(v))
function producerAscii(value) {
  if (Array.isArray(value)) return '[' + value.map(producerAscii).join(',') + ']'
  if (value && typeof value === 'object') return '{' + Object.keys(value).sort().map(key => producerAscii(key) + ':' + producerAscii(value[key])).join(',') + '}'
  const raw = JSON.stringify(value); let result = ''
  for (let i = 0; i < raw.length; i++) { const unit = raw.charCodeAt(i); result += unit >= 127 ? '\\u' + unit.toString(16).padStart(4, '0') : raw[i] }
  return result
}
function producerPlanSha(value) {
  return createHash('sha256').update(producerAscii({ schema_version: value.schema_version, display_graph_id: value.display_graph_id, scope: value.scope, project_revision: value.project_revision, operation_id: value.operation_id, source: value.source, options: value.options, actors: value.actors, projection_sha256: value.projection_sha256 }), 'ascii').digest('hex')
}
function planned(payload) {
  const value = { schema_version: 1, display_graph_id: 'graph_1', scope: { schema_version: 1, workspace_id: uid(1), project_id: uid(2), graph_id: uid(3), run_id: null, branch_id: null, layer: 'source' }, project_revision: 1, operation_id: payload.operation_id, source: { source_revision: source.source_revision, source_name: source.source_name, source_sha256: hash }, options: clone(payload.options), actors: [{ source_entity_uuid: uid(7), name: hostile, labels: ['Person'] }], projection_sha256: hash, plan_sha256: null, state: 'planned', progress: { stage: 'planned', completed: 0, total: 100 }, error_code: null, authorization: { model_calls_enabled: true, ceiling_microusd: '12345' }, receipt: null, graph_snapshot_atomic: false, model_calls_started: false, simulation_executed: false }
  value.plan_sha256 = producerPlanSha(value); return value
}
function ready(known) {
  const v = clone(known), files = ['state.json', 'simulation_config.json', 'source_grounding.json', ...(v.options.platforms.includes('twitter') ? ['twitter_profiles.csv'] : []), ...(v.options.platforms.includes('reddit') ? ['reddit_profiles.json'] : [])].map(name => ({ name, sha256: hash, size: 123 }))
  v.state = 'ready'; v.progress = { stage: 'ready', completed: 100, total: 100 }; v.model_calls_started = true
  v.receipt = { simulation_id: 'sim_' + v.operation_id.replaceAll('-', ''), artifact_sha256: createHash('sha256').update(JSON.stringify({ files, schema_version: 1 })).digest('hex'), files }; return v
}
function mount(initial = {}) {
  const props = reactive({ methods: { plan: async p => planned(p) }, connected: true, source: clone(source), displayGraphId: 'graph_1', typeLabels: ['Entity', 'Node', 'Person', 'Person', '<script>'], locale: 'en', ...initial })
  const root = document.createElement('div'); document.body.append(root)
  const app = createApp({ setup: () => () => h(component, props) }); app.mount(root)
  return { props, root, cleanup() { app.unmount(); root.remove() } }
}
async function settle() { for (let i = 0; i < 20; i++) { await new Promise(resolve => setTimeout(resolve, 0)); await nextTick() } }
function input(root, selector, value) { const node = root.querySelector(selector); node.value = value; node.dispatchEvent(new dom.window.Event('input', { bubbles: true })) }
function submit(root) { root.querySelector('form').dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true })) }
function begin(root) { input(root, '#preparation-requirement', 'Study responses 中😀'); submit(root) }
test('actual SFC never fetches on mount/locale change and uses native accessible controls', async () => {
  let calls = 0; const m = mount({ connected: false, methods: { plan: () => { calls++ } } })
  try {
    assert.equal(m.root.querySelector('fieldset').disabled, true); assert.equal(m.root.querySelectorAll('.type-choices input').length, 1)
    for (const locale of ['en', 'zh', 'ms']) { m.props.locale = locale; await settle(); assert.ok(m.root.textContent.includes(preparationCopyFor(locale).title)); assert.ok(m.root.textContent.includes(preparationCopyFor(locale).reservation)) }
    assert.equal(calls, 0); assert.equal(m.root.querySelector('.start'), null)
    for (const input of m.root.querySelectorAll('input:not([type=checkbox]),textarea')) assert.ok(m.root.querySelector(`label[for="${input.id}"]`))
  } finally { m.cleanup() }
})
test('review is explicit, detached, inert, source-bound and invalidated by input changes', async () => {
  let producer, calls = 0
  const m = mount({ methods: { plan: async p => { calls++; return producer = planned(p) } } })
  try {
    begin(m.root); await settle(); assert.equal(calls, 1); assert.ok(m.root.querySelector('.plan')); assert.equal(m.root.querySelector('.start').disabled, false)
    producer.actors[0].name = 'mutation'; await settle(); assert.ok(m.root.textContent.includes(hostile)); assert.equal(m.root.textContent.includes('mutation'), false); assert.equal(m.root.querySelector('img,script,a'), null)
    assert.equal(document.activeElement, m.root.querySelector('.feedback'))
    input(m.root, '#preparation-seed', '1'); await settle(); assert.equal(m.root.querySelector('.start'), null); assert.equal(m.root.querySelector('.plan'), null); assert.ok(m.root.textContent.includes(preparationCopy.en.changed)); assert.equal(calls, 1)
  } finally { m.cleanup() }
})
test('integer boundaries are honest and rounds above accepted native 24 never request', async () => {
  const calls = [], m = mount({ methods: { plan: async p => { calls.push(p); return planned(p) } } })
  try {
    for (const bad of ['25', '0', '1e1', '1.5']) { input(m.root, '#preparation-rounds', bad); begin(m.root); await settle(); assert.equal(calls.length, 0) }
    input(m.root, '#preparation-rounds', '24')
    for (const value of ['0', '4294967295']) { input(m.root, '#preparation-seed', value); begin(m.root); await settle(); assert.equal(calls.at(-1).options.seed, Number(value)); assert.equal(calls.at(-1).options.max_rounds, 24) }
    input(m.root, '#preparation-seed', '4294967296'); begin(m.root); await settle(); assert.equal(calls.length, 2)
  } finally { m.cleanup() }
})
test('lost Start fences duplicate clicks and explicit Refresh reuses same identity to publish ready manifest', async () => {
  let known, starts = 0; const ids = []
  const m = mount({ methods: { plan: async p => known = planned(p), start: async p => { starts++; ids.push(clone(p)); throw Object.assign(new Error('secret detail'), { code: 'transport_failure' }) }, status: async p => { ids.push(clone(p)); return ready(known) } } })
  try {
    begin(m.root); await settle(); m.root.querySelector('.start').click(); await settle(); m.root.querySelector('.start').click(); await settle()
    assert.equal(starts, 1); assert.equal(m.root.querySelector('.start').disabled, true); assert.ok(m.root.textContent.includes(preparationCopy.en.lost)); assert.equal(m.root.textContent.includes('secret detail'), false)
    m.root.querySelector('.refresh').click(); await settle(); assert.deepEqual(ids[0], ids[1]); assert.ok(m.root.textContent.includes(preparationCopy.en.ready)); assert.equal(m.root.querySelectorAll('.manifest li').length, 4); assert.equal(m.root.querySelector('a,button.launch'), null)
  } finally { m.cleanup() }
})
test('source/option changes preserve a started identity while reset/disconnect suppress delayed publication', async () => {
  let known, finish, identity
  const m = mount({ methods: { plan: async p => known = planned(p), start: p => { identity = clone(p); return new Promise(resolve => { finish = resolve }) }, status: async p => { assert.deepEqual(p, identity); return ready(known) } } })
  try {
    begin(m.root); await settle(); m.root.querySelector('.start').click(); await settle()
    input(m.root, '#preparation-seed', '3'); m.props.source = { ...source, source_revision: uid(66) }; await settle()
    finish({ ...clone(known), state: 'queued', progress: { stage: 'queued', completed: 0, total: 100 } }); await settle(); assert.equal(m.root.querySelector('.start'), null); assert.ok(m.root.querySelector('.refresh'))
    m.root.querySelector('.refresh').click(); await settle(); assert.ok(m.root.textContent.includes(preparationCopy.en.ready))
    m.props.resetVersion = 1; await settle(); assert.equal(m.root.querySelector('.plan'), null); assert.equal(m.root.querySelector('.refresh'), null); assert.equal(m.root.querySelector('#preparation-requirement').value, '')
    m.props.connected = false; await settle(); assert.equal(m.root.querySelector('fieldset').disabled, true)
  } finally { m.cleanup() }
})
test('delayed review cannot repopulate reset, source-changed or unmounted views', async () => {
  for (const action of ['reset', 'source', 'unmount']) {
    let finish, p
    const m = mount({ methods: { plan: payload => { p = payload; return new Promise(resolve => { finish = resolve }) } } })
    begin(m.root); await settle()
    if (action === 'reset') m.props.resetVersion = 1
    else if (action === 'source') m.props.source = { ...source, source_revision: uid(66) }
    else m.cleanup()
    await settle(); finish(planned(p)); await settle(); assert.equal(m.root.querySelector('.plan'), null)
    if (action !== 'unmount') m.cleanup()
  }
})
test('disabled authorization, malformed reply, divergent status and malformed-401 callbacks fail closed', async () => {
  for (const problem of ['disabled', 'malformed', 'divergent', 'unauthorized']) {
    let known
    const m = mount({ methods: { plan: async p => { known = planned(p); if (problem === 'disabled') known.authorization.model_calls_enabled = false; if (problem === 'malformed') known.extra = true; return known }, start: async () => { if (problem === 'unauthorized') throw Object.assign(new Error('raw'), { code: 'unauthorized' }); return { ...clone(known), state: 'queued', progress: { stage: 'queued', completed: 0, total: 100 } } }, status: async () => { const result = ready(known); result.project_revision++; return result } } })
    try {
      begin(m.root); await settle()
      if (problem === 'disabled') { assert.equal(m.root.querySelector('.start').disabled, true); assert.ok(m.root.textContent.includes(preparationCopy.en.disabled)) }
      else if (problem === 'malformed') { assert.equal(m.root.querySelector('.start'), null); assert.ok(m.root.textContent.includes(preparationCopy.en.errors.invalid_reply)) }
      else { m.root.querySelector('.start').click(); await settle(); if (problem === 'divergent') { m.root.querySelector('.refresh').click(); await settle(); assert.ok(m.root.textContent.includes(preparationCopy.en.errors.invalid_reply)); assert.equal(m.root.querySelector('.manifest'), null) } else { assert.equal(m.root.querySelector('.plan'), null); assert.equal(m.root.querySelector('.refresh'), null); assert.equal(m.root.querySelector('#preparation-requirement').value, '') } }
    } finally { m.cleanup() }
  }
})
test('locale schemas cover every fixed state, stage and error with native keyboard focus', async () => {
  for (const locale of ['zh', 'ms']) { for (const group of ['states', 'stages', 'errors']) assert.deepEqual(Object.keys(preparationCopy[locale][group]).sort(), Object.keys(preparationCopy.en[group]).sort()) }
  const m = mount()
  try { const button = m.root.querySelector('.review'); button.focus(); assert.equal(document.activeElement, button); assert.equal(button.type, 'submit'); assert.equal(m.root.querySelector('.clear').type, 'button'); assert.equal(m.root.querySelector('.feedback').getAttribute('aria-live'), 'polite') } finally { m.cleanup() }
})
test('actual HTTP409 policy denials retain the mounted operation for Refresh without duplicate Start', async () => {
  for (const denial of ['model_calls_disabled', 'budget_denied']) {
    let known; const requests = []
    const envelope = value => new Response(JSON.stringify({ success: true, data: value }), { headers: { 'Content-Type': 'application/json' } })
    const client = createWorkbenchClient({ fetchImpl: async (url, options) => {
      requests.push({ url, body: options.body, authorization: options.headers.Authorization })
      if (url.includes('/data/')) return envelope({ graph_id: 'graph_1', nodes: [], edges: [], node_count: 0, edge_count: 0 })
      if (url.endsWith('/plan')) { known = planned(JSON.parse(options.body)); return envelope(known) }
      if (url.endsWith('/start')) return new Response(JSON.stringify({ success: false, error: { code: denial } }), { status: 409, headers: { 'Content-Type': 'application/json' } })
      const status = clone(known); status.authorization = { model_calls_enabled: false, ceiling_microusd: null }; return envelope(status)
    } })
    await client.connect({ origin: 'http://127.0.0.1:5001', graph: 'graph_1', token: 'private-token' })
    const m = mount({ methods: { plan: (p, s) => client.preparationPlan(p, s), start: p => client.preparationStart(p), status: p => client.preparationStatus(p) } })
    try {
      begin(m.root); await settle(); m.root.querySelector('.start').click(); await settle()
      assert.ok(m.root.textContent.includes(preparationCopy.en.errors[denial])); assert.equal(m.root.textContent.includes(preparationCopy.en.lost), false)
      assert.equal(m.root.querySelector('.start').disabled, true); m.root.querySelector('.start').click(); await settle()
      assert.equal(requests.filter(r => r.url.endsWith('/start')).length, 1); assert.equal(m.props.connected, true); assert.ok(m.root.querySelector('.refresh'))
      m.root.querySelector('.refresh').click(); await settle()
      assert.equal(requests.length, 4); assert.deepEqual(JSON.parse(requests[2].body), JSON.parse(requests[3].body)); assert.equal(requests[3].authorization, 'Bearer private-token')
      assert.ok(m.root.querySelector('.plan')); assert.ok(m.root.querySelector('.refresh')); assert.ok(m.root.textContent.includes(preparationCopy.en.disabled))
    } finally { m.cleanup(); client.disconnect() }
  }
})
test('valid-looking divergent plan digest never admits a mounted review', async () => {
  const m = mount({ methods: { plan: async p => ({ ...planned(p), plan_sha256: 'f'.repeat(64) }) } })
  try { begin(m.root); await settle(); assert.equal(m.root.querySelector('.plan'), null); assert.equal(m.root.querySelector('.start'), null); assert.ok(m.root.textContent.includes(preparationCopy.en.errors.invalid_reply)) } finally { m.cleanup() }
})
