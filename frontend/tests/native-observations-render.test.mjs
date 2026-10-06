import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createHash, webcrypto } from 'node:crypto'
import { JSDOM } from 'jsdom'
import { parse, compileScript } from '@vue/compiler-sfc'
import { nativeObservationsCopy, nativeObservationsCopyFor } from '../src/i18n/nativeObservations.js'
import { validateNativeObservationsResult } from '../src/api/nativeObservations.js'
globalThis.crypto ||= webcrypto
const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://127.0.0.1:5173/research' })
for (const name of ['window', 'document', 'Element', 'HTMLElement', 'SVGElement', 'Node']) globalThis[name] = name === 'window' ? dom.window : dom.window[name]
const { createApp, h, nextTick, reactive } = await import('vue')
const file = new URL('../src/components/workbench/NativeObservations.vue', import.meta.url)
const { descriptor } = parse(readFileSync(file, 'utf8'), { filename: file.pathname })
const compiled = compileScript(descriptor, { id: 'native-observations', inlineTemplate: true, genDefaultAs: '__component' })
let code = compiled.content.replace(/from (['"])vue\1/g, `from ${JSON.stringify(import.meta.resolve('vue'))}`)
code = code.replace(/from (['"])(\.{1,2}\/[^'"]+)\1/g, (_m, _q, relative) => `from ${JSON.stringify(new URL(relative, file).href)}`)
const activeValidations = new Set(); let validationCalls = 0
globalThis.__u07eTrackValidation = (...args) => {
  validationCalls++; const promise = validateNativeObservationsResult(...args); activeValidations.add(promise)
  promise.then(() => activeValidations.delete(promise), () => activeValidations.delete(promise)); return promise
}
// Observe the actual component's validation promise to await late admission.
code = code.replace(/\bvalidateNativeObservationsResult\(/g, 'globalThis.__u07eTrackValidation(')
const component = (await import(`data:text/javascript;base64,${Buffer.from(code + '\nexport default __component').toString('base64')}`)).default
const uid = n => `00000000-0000-0000-0000-${String(n).padStart(12, '0')}`, hash = 'a'.repeat(64), clone = v => JSON.parse(JSON.stringify(v))
const hostile = '<img src=x onerror=alert(1)> <script>secret</script> 中😀 https://evil.invalid/'
function ascii(v) {
  if (Array.isArray(v)) return '[' + v.map(ascii).join(',') + ']'
  if (v && typeof v === 'object') return '{' + Object.keys(v).sort().map(k => ascii(k) + ':' + ascii(v[k])).join(',') + '}'
  const raw = JSON.stringify(v); let out = ''
  for (let i = 0; i < raw.length; i++) out += raw.charCodeAt(i) >= 127 ? '\\u' + raw.charCodeAt(i).toString(16).padStart(4, '0') : raw[i]
  return out
}
const digest = v => createHash('sha256').update(ascii(v), 'ascii').digest('hex'), bytesHash = v => createHash('sha256').update(v, 'utf8').digest('hex')
const pick = (v, keys) => Object.fromEntries(keys.split(' ').map(k => [k, v[k]]))
const lines = Array.from({ length: 23 }, (_, i) => i === 22 ? '{"unknown":"中😀","n":1.0}' : JSON.stringify({ event_type: hostile, action_type: 'POST ' + i, success: i % 2 === 0, text: hostile }))
function fixture() {
  const files = ['state.json', 'simulation_config.json', 'source_grounding.json', 'twitter_profiles.csv', 'reddit_profiles.json'].map(name => ({ name, sha256: hash, size: 123 }))
  const preparation = { schema_version: 1, display_graph_id: 'graph_1', scope: { schema_version: 1, workspace_id: uid(1), project_id: uid(2), graph_id: uid(3), run_id: null, branch_id: null, layer: 'source' }, project_revision: 1, operation_id: uid(9), source: { source_revision: uid(5), source_name: hostile, source_sha256: hash }, options: { types: null, max_agents: 10, seed: 0, platforms: ['twitter', 'reddit'], max_rounds: 24, simulation_requirement: hostile }, actors: [{ source_entity_uuid: uid(7), name: hostile, labels: ['Person'] }], projection_sha256: hash, plan_sha256: null, state: 'ready', progress: { stage: 'ready', completed: 100, total: 100 }, error_code: null, authorization: { model_calls_enabled: true, ceiling_microusd: '12345' }, receipt: { simulation_id: 'sim_' + uid(9).replaceAll('-', ''), artifact_sha256: digest({ schema_version: 1, files }), files }, graph_snapshot_atomic: false, model_calls_started: true, simulation_executed: false }
  preparation.plan_sha256 = digest(pick(preparation, 'schema_version display_graph_id scope project_revision operation_id source options actors projection_sha256'))
  const manifest = { schema_version: 1, files: ['twitter', 'reddit'].flatMap(p => [{ name: p + '_simulation.db', sha256: bytesHash('db'), size: 2 }, { name: p + '/actions.jsonl', sha256: bytesHash(lines.join('\n') + '\n'), size: Buffer.byteLength(lines.join('\n') + '\n') }]) }
  const launch = { schema_version: 1, display_graph_id: 'graph_1', scope: clone(preparation.scope), preparation: { operation_id: preparation.operation_id, plan_sha256: preparation.plan_sha256, simulation_id: preparation.receipt.simulation_id, artifact_sha256: preparation.receipt.artifact_sha256 }, request: { schema_version: 1, principal: 'local-research', project_id: uid(2), project_revision: 1, simulation_id: preparation.receipt.simulation_id, run_id: uid(10), artifact_sha256: preparation.receipt.artifact_sha256, runtime_sha256: hash, platforms: ['twitter', 'reddit'], seed: 0, max_rounds: 24 }, limits: { max_calls: 20, max_input_bytes: 2097152, max_output_tokens: 4096, max_run_seconds: 600 }, ceiling_microusd: '12345', model_label: hostile, launch_sha256: null, state: 'completed', error_code: null, authorization: { model_calls_enabled: false }, workflow: null, receipt: null, cancel_requested: false, cleanup: { known: true, pending: false, owner_thread_alive: false } }
  launch.launch_sha256 = digest(pick(launch, 'schema_version display_graph_id scope preparation request limits ceiling_microusd model_label'))
  launch.receipt = { run_id: uid(10), attempt_id: uid(12), instance_id: uid(13), request_fingerprint: digest(launch.request), outcome: 'completed', evidence_sha256: digest(manifest) }
  return { selection: { launch, preparation }, manifest }
}
function page(payload, f = fixture()) {
  const records = lines.slice(payload.offset, payload.offset + payload.limit).map((raw_json, i) => ({ index: payload.offset + i, raw_json, record_sha256: bytesHash(raw_json) }))
  return { schema_version: 1, launch: clone(f.selection.launch), manifest: clone(f.manifest), platform: payload.platform, offset: payload.offset, limit: payload.limit, total_records: 23, next_offset: payload.offset + records.length < 23 ? payload.offset + records.length : null, counts: { event_records: 22, action_records: 22, successful_action_records: 11, failed_action_records: 11 }, records }
}
function mount(initial = {}) {
  const props = reactive({ methods: { page: async payload => page(payload), clear: () => {} }, selection: fixture().selection, connected: true, busy: false, displayGraphId: 'graph_1', resetVersion: 0, locale: 'en', ...initial })
  const root = document.createElement('div'); document.body.append(root)
  const app = createApp({ setup: () => () => h(component, props) }); app.mount(root)
  let mounted = true
  return { props, root, cleanup() { if (mounted) { mounted = false; app.unmount(); root.remove() } } }
}
async function waitFor(predicate) {
  const end = Date.now() + 2000
  do { await nextTick(); if (predicate()) return; await new Promise(resolve => setImmediate(resolve)) } while (Date.now() < end)
  assert.ok(predicate(), 'bounded lifecycle condition did not complete')
}
const admitted = m => waitFor(() => m.root.querySelector('.load') && !m.root.querySelector('.load').disabled)
const finished = m => waitFor(() => m.root.querySelector('section').getAttribute('aria-busy') === 'false')

test('mounted selection, platform and locale changes perform no implicit page request', async () => {
  let calls = 0; const m = mount({ methods: { page: () => { calls++ } } })
  try {
    await admitted(m)
    for (const locale of ['en', 'zh', 'ms']) {
      m.props.locale = locale; await nextTick(); const copy = nativeObservationsCopyFor(locale)
      assert.ok(m.root.textContent.includes(copy.title)); assert.ok(m.root.textContent.includes(copy.distinction)); assert.ok(m.root.textContent.includes(copy.bounds))
      assert.deepEqual(Object.keys(copy).sort(), Object.keys(nativeObservationsCopy.en).sort()); assert.deepEqual(Object.keys(copy.errors).sort(), Object.keys(nativeObservationsCopy.en.errors).sort())
    }
    const select = m.root.querySelector('select'); assert.deepEqual([...select.options].map(v => v.value), ['twitter', 'reddit'])
    select.value = 'reddit'; select.dispatchEvent(new dom.window.Event('change', { bubbles: true })); await nextTick()
    assert.equal(calls, 0); assert.equal(m.root.querySelector('.records'), null); assert.equal(m.root.querySelector('.feedback').getAttribute('aria-live'), 'polite')
    assert.ok([...m.root.querySelectorAll('button')].every(b => b.type === 'button'))
  } finally { m.cleanup() }
})
test('explicit page controls render original inert JSON, honest counts, unknown records and focused status', async () => {
  const requests = []; let reply, callerSelection
  const m = mount({ methods: { page: async (p, selection) => { requests.push(clone(p)); callerSelection = selection; return reply = page(p) } } })
  try {
    await admitted(m); m.root.querySelector('.load').focus(); m.root.querySelector('.load').click(); await finished(m)
    assert.equal(requests.length, 1); assert.equal(m.root.querySelectorAll('.records li').length, 20); assert.equal(document.activeElement, m.root.querySelector('.feedback'))
    assert.equal(m.root.querySelector('.previous').disabled, true); assert.equal(m.root.querySelector('.next').disabled, false)
    reply.records[0].raw_json = '{}'; callerSelection.preparation.source.source_name = 'mutated'; await nextTick()
    assert.ok(m.root.textContent.includes(hostile)); assert.equal(m.root.textContent.includes('mutated'), false); assert.equal(m.root.querySelector('a,img,script'), null)
    assert.ok(m.root.textContent.includes(nativeObservationsCopy.en.partial)); assert.ok(m.root.textContent.includes(nativeObservationsCopy.en.action_records))
    m.root.querySelector('.next').click(); await finished(m); assert.equal(requests[1].offset, 20); assert.equal(m.root.querySelectorAll('.records li').length, 3)
    assert.equal(m.root.querySelector('.next').disabled, true); assert.ok(m.root.textContent.includes(nativeObservationsCopy.en.unknown)); assert.ok(m.root.textContent.includes('"n":1.0'))
    m.root.querySelector('.previous').click(); await finished(m); assert.equal(requests[2].offset, 0)
    m.root.querySelector('.refresh').click(); await finished(m); assert.equal(requests[3].offset, 0)
    const select = m.root.querySelector('select'); select.value = 'reddit'; select.dispatchEvent(new dom.window.Event('change', { bubbles: true })); await nextTick()
    assert.equal(m.root.querySelector('.records'), null); assert.equal(requests.length, 4)
    m.root.querySelector('.load').click(); await finished(m); assert.equal(requests[4].platform, 'reddit')
  } finally { m.cleanup() }
})
test('pending contradictory controls are disabled; clear, disconnect, source context and unmount fence late replies', async () => {
  for (const action of ['clear', 'disconnect', 'reset', 'selection', 'graph', 'unmount']) {
    let finish, cleared = 0, requests = 0, requested
    const begun = new Promise(resolve => { requested = resolve })
    const m = mount({ methods: { page: p => { requests++; requested(); return new Promise(resolve => { finish = () => resolve(page(p)) }) }, clear: () => { cleared++ } } })
    try {
      await admitted(m); m.root.querySelector('.load').click(); await begun; await nextTick()
      assert.equal(m.root.querySelector('.load').disabled, true); assert.equal(m.root.querySelector('select').disabled, true)
      if (action === 'clear') m.root.querySelector('.clear').click()
      else if (action === 'disconnect') m.props.connected = false
      else if (action === 'reset') m.props.resetVersion++
      else if (action === 'selection') m.props.selection = null
      else if (action === 'graph') m.props.displayGraphId = 'other'
      else m.cleanup()
      await nextTick(); assert.ok(cleared >= 2); const before = validationCalls; finish()
      await waitFor(() => validationCalls > before && activeValidations.size === 0)
      await nextTick(); assert.equal(m.root.querySelector('.records'), null); assert.equal(requests, 1)
    } finally { m.cleanup() }
  }
})
test('transient refusal retains visibly stale same-receipt page; denial clears all private observations', async () => {
  for (const failure of ['conflict', 'busy', 'observations_unavailable', 'unauthorized', 'origin_denied', 'tombstoned']) {
    let calls = 0
    const m = mount({ methods: { page: async p => { if (calls++) throw Object.assign(new Error('private path provider token'), { code: failure }); return page(p) }, clear: () => {} } })
    try {
      await admitted(m); m.root.querySelector('.load').click(); await finished(m); m.root.querySelector('.refresh').click(); await finished(m)
      assert.ok(m.root.textContent.includes(nativeObservationsCopy.en.errors[failure])); assert.equal(m.root.textContent.includes('private path'), false)
      assert.equal(!!m.root.querySelector('.records'), !['unauthorized', 'origin_denied', 'tombstoned'].includes(failure))
      if (['conflict', 'busy', 'observations_unavailable'].includes(failure)) assert.ok(m.root.textContent.includes(nativeObservationsCopy.en.stale))
      assert.equal(calls, 2)
    } finally { m.cleanup() }
  }
})
test('rehashed hostile malformed record and invalid selected receipt never render evidence', async () => {
  const m = mount({ methods: { page: async p => { const v = page(p); v.records[0].raw_json = '{"x":1,"x":2}'; v.records[0].record_sha256 = bytesHash(v.records[0].raw_json); return v } } })
  try { await admitted(m); m.root.querySelector('.load').click(); await finished(m); assert.equal(m.root.querySelector('.records'), null); assert.ok(m.root.textContent.includes(nativeObservationsCopy.en.errors.invalid_reply)) } finally { m.cleanup() }
  const f = fixture(); f.selection.launch.receipt = null; let calls = 0
  const invalid = mount({ selection: f.selection, methods: { page: () => { calls++ } } })
  try { await waitFor(() => invalid.root.textContent.includes(nativeObservationsCopy.en.errors.invalid_reply)); assert.equal(invalid.root.querySelector('.load'), null); assert.equal(calls, 0) } finally { invalid.cleanup() }
})
test('responsive and keyboard fixture checks finite controls, literal wrapping and unobscured focus hooks', () => {
  const css = descriptor.styles[0].content
  for (const token of ['min-height:44px', 'min-width:0', 'overflow-wrap:anywhere', 'white-space:pre-wrap', ':focus-visible', 'scroll-margin:24px', '@media(max-width:600px)']) assert.ok(css.includes(token))
  assert.equal(descriptor.template.content.includes('v-html'), false)
  // Real 320/768/1440 layout and keyboard activation remain Main browser gates.
})
