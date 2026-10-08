// Mount real parent/native/report components. Preparation fixture seeds the
// real private-client admission cache; it does not qualify inherited execution.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createHash, webcrypto } from 'node:crypto'
import { JSDOM } from 'jsdom'
import { parse, compileScript } from '@vue/compiler-sfc'
import { connectedReportsCopyFor } from '../src/i18n/connectedReports.js'
globalThis.crypto ||= webcrypto
const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://127.0.0.1:5173/research' })
for (const name of ['window', 'document', 'Element', 'HTMLElement', 'SVGElement', 'Node']) globalThis[name] = name === 'window' ? dom.window : dom.window[name]
globalThis.localStorage = dom.window.localStorage
const { createApp, h, nextTick } = await import('vue')
const dataModule = code => `data:text/javascript;base64,${Buffer.from(code).toString('base64')}`
const localeFiles = Object.fromEntries(['en', 'zh', 'ms'].map(key => [`../../../locales/${key}.json`, { default: JSON.parse(readFileSync(new URL(`../../locales/${key}.json`, import.meta.url), 'utf8')) }]))
let localeSource = readFileSync(new URL('../src/i18n/index.js', import.meta.url), 'utf8')
localeSource = localeSource.replace(/import languages from [^\n]+/, `const languages = ${readFileSync(new URL('../../locales/languages.json', import.meta.url), 'utf8')}`)
localeSource = localeSource.replace(/import\.meta\.glob\([^\n]+\)/, JSON.stringify(localeFiles)).replace(/from (['"])(vue|vue-i18n)\1/g, (_m, _q, name) => `from ${JSON.stringify(import.meta.resolve(name))}`)
const localeUrl = dataModule(localeSource), sharedLocale = await import(localeUrl)
async function compile(path, replacements = {}) {
  const file = new URL(path, import.meta.url), { descriptor } = parse(readFileSync(file, 'utf8'), { filename: file.pathname })
  const result = compileScript(descriptor, { id: path, inlineTemplate: true, genDefaultAs: '__component' })
  let code = result.content.replace(/from (['"])vue\1/g, `from ${JSON.stringify(import.meta.resolve('vue'))}`)
  code = code.replace(/from (['"])(\.{1,2}\/[^'"]+)\1/g, (_m, _q, relative) => `from ${JSON.stringify(replacements[relative] || new URL(relative, file).href)}`)
  const url = dataModule(code + '\nexport default __component'); return { url, component: (await import(url)).default }
}
const empty = dataModule('export default {render(){return null}}')
const preparationSeed = dataModule(`import {h} from ${JSON.stringify(import.meta.resolve('vue'))}; export default {props:['methods','connected'],emits:['ready'],setup(p,{emit}){return ()=>h('button',{class:'fixture-preparation',disabled:!p.connected,onClick:async()=>{const f=globalThis.__u07gParentFixture;const plan=await p.methods.plan(f.prepPayload(),f.source());const ready=await p.methods.start({schema_version:1,operation_id:plan.operation_id,plan_sha256:plan.plan_sha256});emit('ready',ready)}},'Fixture preparation')}}`)
const native = await compile('../src/components/workbench/NativeLaunch.vue'), observations = await compile('../src/components/workbench/NativeObservations.vue'), reports = await compile('../src/components/workbench/ConnectedReports.vue'), followups = await compile('../src/components/workbench/ConnectedFollowup.vue')
const replacements = Object.fromEntries(['EvidenceResults', 'SourceLibrary', 'SourceIngestion', 'ExperimentComparison', 'PopulationWorkbench'].map(n => [`../components/workbench/${n}.vue`, empty]))
const parent = await compile('../src/views/ResearchWorkbench.vue', { ...replacements, '../i18n/index.js': localeUrl, '../components/workbench/SimulationPreparation.vue': preparationSeed, '../components/workbench/NativeLaunch.vue': native.url, '../components/workbench/NativeObservations.vue': observations.url, '../components/workbench/ConnectedReports.vue': reports.url, '../components/workbench/ConnectedFollowup.vue': followups.url })
const uid = n => `00000000-0000-0000-0000-${String(n).padStart(12, '0')}`, hash = 'a'.repeat(64), clone = v => JSON.parse(JSON.stringify(v))
function ascii(v) { if (Array.isArray(v)) return '[' + v.map(ascii).join(',') + ']'; if (v && typeof v === 'object') return '{' + Object.keys(v).sort().map(k => ascii(k) + ':' + ascii(v[k])).join(',') + '}'; return JSON.stringify(v).replace(/[\u007f-\uffff]/g, c => '\\u' + c.charCodeAt(0).toString(16).padStart(4, '0')) }
const digest = v => createHash('sha256').update(ascii(v), 'ascii').digest('hex'), bytesHash = v => createHash('sha256').update(v).digest('hex'), pick = (v, keys) => Object.fromEntries(keys.split(' ').map(k => [k, v[k]]))
const options = { types: null, max_agents: 10, seed: 0, platforms: ['twitter'], max_rounds: 24, simulation_requirement: 'Study 中😀' }
const sourceRecord = () => ({ project_id: uid(2), source_revision: uid(5), source_name: 'Fixture 中😀', text_sha256: hash, byte_length: 8, codepoint_length: 4, recorded_at: '2026-10-06T00:00:00Z' })
const prepPayload = () => ({ schema_version: 1, operation_id: uid(9), source_revision: uid(5), options: clone(options) })
function preparation(ready = false) {
  const v = { schema_version: 1, display_graph_id: 'graph_1', scope: { schema_version: 1, workspace_id: uid(1), project_id: uid(2), graph_id: uid(3), run_id: null, branch_id: null, layer: 'source' }, project_revision: 1, operation_id: uid(9), source: { source_revision: uid(5), source_name: 'Fixture 中😀', source_sha256: hash }, options: clone(options), actors: [{ source_entity_uuid: uid(7), name: 'Actor', labels: ['Person'] }], projection_sha256: hash, plan_sha256: null, state: ready ? 'ready' : 'planned', progress: { stage: ready ? 'ready' : 'planned', completed: ready ? 100 : 0, total: 100 }, error_code: null, authorization: { model_calls_enabled: true, ceiling_microusd: '12345' }, receipt: null, graph_snapshot_atomic: false, model_calls_started: ready, simulation_executed: false }
  v.plan_sha256 = digest(pick(v, 'schema_version display_graph_id scope project_revision operation_id source options actors projection_sha256'))
  if (ready) { const files = ['state.json', 'simulation_config.json', 'source_grounding.json', 'twitter_profiles.csv'].map(name => ({ name, sha256: hash, size: 123 })); v.receipt = { simulation_id: 'sim_' + uid(9).replaceAll('-', ''), artifact_sha256: digest({ schema_version: 1, files }), files } }
  return v
}
function nativePlan(payload) {
  const p = preparation(true), v = { schema_version: 1, display_graph_id: 'graph_1', scope: clone(p.scope), preparation: { operation_id: p.operation_id, plan_sha256: p.plan_sha256, simulation_id: p.receipt.simulation_id, artifact_sha256: p.receipt.artifact_sha256 }, request: { schema_version: 1, principal: 'local-research', project_id: uid(2), project_revision: 1, simulation_id: p.receipt.simulation_id, run_id: payload.launch_id, artifact_sha256: p.receipt.artifact_sha256, runtime_sha256: hash, platforms: ['twitter'], seed: 0, max_rounds: 24 }, limits: { max_calls: 20, max_input_bytes: 2097152, max_output_tokens: 4096, max_run_seconds: 600 }, ceiling_microusd: '12345', model_label: 'scripted', launch_sha256: null, state: 'planned', error_code: null, authorization: { model_calls_enabled: true }, workflow: null, receipt: null, cancel_requested: false, cleanup: { known: true, pending: false, owner_thread_alive: false } }
  v.launch_sha256 = digest(pick(v, 'schema_version display_graph_id scope preparation request limits ceiling_microusd model_label')); return v
}
function nativeDone(known) { const v = clone(known); v.state = 'completed'; v.receipt = { run_id: v.request.run_id, attempt_id: uid(12), instance_id: uid(13), request_fingerprint: digest(v.request), outcome: 'completed', evidence_sha256: hash }; return v }
function reportPlan(payload, launch) {
  const p = preparation(true), v = { schema_version: 1, report_id: payload.report_id, plan_sha256: null, binding: { display_graph_id: 'graph_1', principal: 'local-research', scope: clone(p.scope), project_revision: 1, source: p.source, preparation: clone(launch.preparation), native: { run_id: launch.request.run_id, launch_sha256: launch.launch_sha256, request_fingerprint: launch.receipt.request_fingerprint, evidence_sha256: hash, platforms: ['twitter'] }, coverage: [{ platform: 'twitter', total_records: 1, selected_records: 1, complete: true, windows: [{ offset: 0, count: 1 }] }], reference_keys: ['source:' + uid(30), 'native:twitter:0:' + hash] }, options: pick(payload, 'requirement output_language native_windows'), context_sha256: hash, source_projection_sha256: hash, model_label: 'scripted', limits: { max_calls: 64, max_input_bytes: 262144, max_output_tokens: 4096, max_run_seconds: 600 }, ceiling_microusd: 12345, authorization: { model_calls_enabled: true, budget_configured: true }, state: 'planned', progress: { stage: 'planned', percent: 0, completed_sections: 0, total_sections: 0 }, workflow: null, receipt: null, receipt_sha256: null, manifest: null, cleanup: { known: false, pending: null, owner_thread_alive: null }, cancel_requested: false, error_code: null }
  v.plan_sha256 = digest(pick(v, 'schema_version report_id binding options context_sha256 source_projection_sha256 model_label limits ceiling_microusd')); return v
}
const prose = '# Fixture report\n\n[[source:' + uid(30) + ']] [[native:twitter:0:' + hash + ']]\n\n中😀 <img src=x onerror=alert(1)>'
function reportDone(known) {
  const v = clone(known); v.state = 'completed'; v.progress = { stage: 'completed', percent: 100, completed_sections: 1, total_sections: 1 }; v.cleanup = { known: true, pending: false, owner_thread_alive: false }
  v.manifest = { schema_version: 1, files: ['meta.json', 'outline.json', 'full_report.md', 'retrieval_evidence.json', 'native_evidence.json', 'section_01.md'].map(name => { const content = name.endsWith('.md') ? prose : '{}'; return { name, size: Buffer.byteLength(content), sha256: bytesHash(content) } }) }
  v.receipt = { schema_version: 1, report_id: v.report_id, plan_sha256: v.plan_sha256, context_sha256: hash, manifest_sha256: digest(v.manifest), output_language: v.options.output_language, reference_integrity: 'validated', semantic_support_status: 'not_reviewed' }; v.receipt_sha256 = digest(v.receipt); return v
}
globalThis.__u07gParentFixture = { prepPayload, source: sourceRecord }
const envelope = data => new Response(JSON.stringify({ success: true, data }), { headers: { 'Content-Type': 'application/json' } })
function mount() { sharedLocale.setUiLocale('en'); const root = document.createElement('div'); document.body.append(root); const app = createApp(parent.component); app.use(sharedLocale.default); app.component('RouterLink', { props: ['to'], setup: (p, { slots }) => () => h('a', { href: p.to }, slots.default?.()) }); app.mount(root); let mounted = true; return { root, cleanup() { if (mounted) { mounted = false; app.unmount(); root.remove() } } } }
function input(m, selector, value) { const e = m.root.querySelector(selector); e.value = value; e.dispatchEvent(new dom.window.Event(e.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true })) }
async function waitFor(predicate) { const end = Date.now() + 3000; do { await nextTick(); if (predicate()) return; await new Promise(r => setImmediate(r)) } while (Date.now() < end); assert.ok(predicate(), 'bounded parent condition did not complete') }
async function connect(m) { input(m, '#graph-id', 'graph_1'); input(m, '#bearer-token', 'private-token'); m.root.querySelector('.connection-form').dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true })); await waitFor(() => !m.root.querySelector('.fixture-preparation').disabled) }
async function seedNative(m) { m.root.querySelector('.fixture-preparation').click(); await waitFor(() => !m.root.querySelector('.native-launch .review').disabled); m.root.querySelector('.native-launch .review').click(); await waitFor(() => m.root.querySelector('.native-launch .refresh') && !m.root.querySelector('.native-launch .refresh').disabled); m.root.querySelector('.native-launch .refresh').click(); await waitFor(() => m.root.querySelector('.use-report') && !m.root.querySelector('.use-report').disabled) }
function transport(options = {}) {
  const calls = []; let launch, report
  return { calls, get report() { return report }, fetch: async (url, request) => {
    calls.push({ url, request }); const p = request.body ? JSON.parse(request.body) : null
    if (url.includes('/data/')) return envelope({ graph_id: 'graph_1', nodes: [], edges: [], node_count: 0, edge_count: 0 })
    if (url.includes('/preparation/')) return envelope(preparation(url.endsWith('/start')))
    if (url.includes('/native-launch/plan/')) { launch = nativePlan(p); return envelope(launch) }
    if (url.includes('/native-launch/status/')) { launch = nativeDone(launch); return envelope(launch) }
    if (url.includes('/connected-report/plan/')) { report = reportPlan(p, launch); return envelope(report) }
    if (url.includes('/connected-report/start/')) { if (options.lost) throw new Error('private transport failure'); if (options.startDenial) return new Response(JSON.stringify({ success: false, error: { code: options.startDenial } }), { status: 409, headers: { 'Content-Type': 'application/json' } }); report = reportDone(report); return envelope(report) }
    if (url.includes('/connected-report/status/')) { if (options.denied) return new Response('', { status: 401 }); report = options.uncertain ? { ...report, state: 'uncertain', error_code: 'report_uncertain', progress: { stage: 'uncertain', percent: 0, completed_sections: 0, total_sections: 0 } } : reportDone(report); return envelope(report) }
    if (url.includes('/connected-report/read/')) return envelope({ schema_version: 1, report, content: prose })
    throw new Error('Unexpected fixture route')
  } }
}

test('actual native report action is independent of inspect; parent captures credentials and localized state', async () => {
  const previous = globalThis.fetch, wire = transport(); globalThis.fetch = wire.fetch; const m = mount()
  try {
    assert.equal(m.root.querySelector('.connected-reports fieldset').disabled, true); await connect(m); assert.equal(m.root.querySelector('#bearer-token').value, ''); await seedNative(m)
    const before = wire.calls.length; m.root.querySelector('.use-report').click(); await waitFor(() => !m.root.querySelector('.connected-reports fieldset').disabled); assert.equal(wire.calls.length, before); assert.equal(m.root.querySelector('.native-observations .load'), null)
    m.root.querySelector('.native-launch .inspect').click(); await waitFor(() => m.root.querySelector('.native-observations .load') && !m.root.querySelector('.native-observations .load').disabled); assert.equal(wire.calls.length, before); assert.equal(m.root.querySelector('.connected-reports fieldset').disabled, false)
    input(m, '#report-requirement', 'Compare 中😀'); m.root.querySelector('.connected-reports form').dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true })); await waitFor(() => m.root.querySelector('.report-start') && !m.root.querySelector('.report-start').disabled)
    m.root.querySelector('.report-start').click(); await waitFor(() => m.root.querySelector('.report-read')); m.root.querySelector('.report-read').click(); await waitFor(() => m.root.querySelector('.narrative')); assert.equal(m.root.querySelector('.narrative img,.narrative script'), null)
    for (const locale of ['en', 'zh', 'ms']) { const count = wire.calls.length; input(m, '#workbench-language', locale); await nextTick(); assert.ok(m.root.textContent.includes(connectedReportsCopyFor(locale).title)); assert.equal(wire.calls.length, count) }
    const reportCalls = wire.calls.filter(c => c.url.includes('/connected-report/')); assert.deepEqual(reportCalls.map(c => new URL(c.url).pathname.split('/')[3]), ['plan', 'start', 'read']); assert.ok(reportCalls.every(c => c.request.headers.Authorization === 'Bearer private-token' && c.request.credentials === 'omit' && !new URL(c.url).search)); assert.equal(m.root.querySelector('.connected-reports [token],.connected-reports [origin]'), null)
  } finally { m.cleanup(); globalThis.fetch = previous }
})
test('parent disconnect/reconnect/manual recovery preserves a lost Start fence and clears all protected views', async () => {
  const previous = globalThis.fetch, options = { lost: true }, wire = transport(options); globalThis.fetch = wire.fetch; const m = mount()
  try {
    await connect(m); await seedNative(m); m.root.querySelector('.use-report').click(); await waitFor(() => !m.root.querySelector('.connected-reports fieldset').disabled); input(m, '#report-requirement', 'Compare'); m.root.querySelector('.connected-reports form').dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true })); await waitFor(() => m.root.querySelector('.report-start') && !m.root.querySelector('.report-start').disabled); m.root.querySelector('.report-start').click(); await waitFor(() => m.root.textContent.includes(connectedReportsCopyFor('en').lost)); const original = clone(wire.report)
    m.root.querySelector('.connection-form button:last-child').click(); await nextTick(); assert.equal(m.root.querySelector('.report-record'), null); assert.equal(m.root.querySelector('#report-recovery-id').value, ''); await connect(m)
    input(m, '#report-recovery-id', original.report_id); input(m, '#report-recovery-hash', original.plan_sha256); m.root.querySelector('.connected-reports .recovery').dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true })); await waitFor(() => m.root.querySelector('.report-read')); assert.equal(m.root.querySelector('.report-start'), null); assert.equal(wire.calls.filter(c => c.url.includes('/connected-report/start/')).length, 1)
    options.denied = true; m.root.querySelector('.report-refresh').click(); await waitFor(() => !m.root.querySelector('.report-record')); assert.equal(m.root.querySelector('.connected-reports .recovery fieldset').disabled, true); assert.equal(m.root.querySelector('#bearer-token').value, '')
  } finally { m.cleanup(); globalThis.fetch = previous }
})
test('parent shows confirmed Start denials without a lost-reply claim and keeps the fence', async () => {
  for (const startDenial of ['budget_denied', 'conflict']) {
    const previous = globalThis.fetch, wire = transport({ startDenial }); globalThis.fetch = wire.fetch; const m = mount()
    try {
      await connect(m); await seedNative(m); m.root.querySelector('.use-report').click(); await waitFor(() => !m.root.querySelector('.connected-reports fieldset').disabled)
      input(m, '#report-requirement', 'Compare'); m.root.querySelector('.connected-reports form').dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true }))
      await waitFor(() => m.root.querySelector('.report-start') && !m.root.querySelector('.report-start').disabled)
      m.root.querySelector('.report-start').click(); await waitFor(() => m.root.textContent.includes(connectedReportsCopyFor('en').errors[startDenial]))
      assert.equal(m.root.textContent.includes(connectedReportsCopyFor('en').lost), false)
      assert.equal(m.root.querySelector('.report-start').disabled, true)
      assert.equal(wire.calls.filter(c => c.url.includes('/connected-report/start/')).length, 1)
    } finally { m.cleanup(); globalThis.fetch = previous }
  }
})
test('parent recovers the original uncertain receipt-free report without another Start', async () => {
  const previous = globalThis.fetch, wire = transport({ lost: true, uncertain: true }); globalThis.fetch = wire.fetch; const m = mount()
  try {
    await connect(m); await seedNative(m); m.root.querySelector('.use-report').click(); await waitFor(() => !m.root.querySelector('.connected-reports fieldset').disabled)
    input(m, '#report-requirement', 'Compare'); m.root.querySelector('.connected-reports form').dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true }))
    await waitFor(() => m.root.querySelector('.report-start') && !m.root.querySelector('.report-start').disabled)
    m.root.querySelector('.report-start').click(); await waitFor(() => m.root.textContent.includes(connectedReportsCopyFor('en').lost))
    const original = clone(wire.report)
    m.root.querySelector('.connection-form button:last-child').click(); await nextTick(); await connect(m)
    input(m, '#report-recovery-id', original.report_id); input(m, '#report-recovery-hash', original.plan_sha256)
    m.root.querySelector('.connected-reports .recovery').dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true }))
    await waitFor(() => m.root.querySelector(`[data-report-id="${original.report_id}"]`)?.textContent.includes(connectedReportsCopyFor('en').states.uncertain))
    const record = m.root.querySelector(`[data-report-id="${original.report_id}"]`)
    assert.ok(record.textContent.includes(original.plan_sha256))
    assert.ok(record.textContent.includes(connectedReportsCopyFor('en').noReceipt))
    assert.equal(record.querySelector('.report-start,.report-read,.report-download'), null)
    assert.equal(wire.calls.filter(c => c.url.includes('/connected-report/start/')).length, 1)
  } finally { m.cleanup(); globalThis.fetch = previous }
})
