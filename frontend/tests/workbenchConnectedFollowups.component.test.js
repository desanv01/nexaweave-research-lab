// Mount the real parent/native/report/follow-up components. Preparation fixture
// seeds the private-client cache; it does not qualify inherited execution.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createHash, webcrypto } from 'node:crypto'
import { JSDOM } from 'jsdom'
import { parse, compileScript } from '@vue/compiler-sfc'
import { connectedReportsCopyFor } from '../src/i18n/connectedReports.js'
import { connectedFollowupsCopyFor } from '../src/i18n/connectedFollowups.js'
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
const sources = await compile('../src/components/workbench/SourceLibrary.vue')
const replacements = Object.fromEntries(['EvidenceResults', 'SourceLibrary', 'SourceIngestion', 'ExperimentComparison', 'PopulationWorkbench'].map(n => [`../components/workbench/${n}.vue`, empty]))
const parent = await compile('../src/views/ResearchWorkbench.vue', { ...replacements, '../i18n/index.js': localeUrl, '../components/workbench/SourceLibrary.vue': sources.url, '../components/workbench/SimulationPreparation.vue': preparationSeed, '../components/workbench/NativeLaunch.vue': native.url, '../components/workbench/NativeObservations.vue': observations.url, '../components/workbench/ConnectedReports.vue': reports.url, '../components/workbench/ConnectedFollowup.vue': followups.url })
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

test('actual parent transfers a completed report into one-shot follow-up; disconnect and manual recovery retain the Start fence', async () => {
  const previous = globalThis.fetch, options = { lostFollowup: true }, wire = transport(options); globalThis.fetch = wire.fetch; const m = mount()
  try {
    assert.equal(m.root.querySelector('.connected-followup fieldset').disabled, true)
    await connect(m); await seedNative(m); m.root.querySelector('.use-report').click()
    await waitFor(() => !m.root.querySelector('.connected-reports fieldset').disabled)
    input(m, '#report-requirement', 'Compare evidence')
    m.root.querySelector('.connected-reports form').dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true }))
    await waitFor(() => m.root.querySelector('.report-start') && !m.root.querySelector('.report-start').disabled)
    m.root.querySelector('.report-start').click(); await waitFor(() => m.root.querySelector('.report-followup'))
    const before = wire.calls.length; m.root.querySelector('.report-followup').click()
    await waitFor(() => !m.root.querySelector('.connected-followup fieldset').disabled)
    assert.equal(wire.calls.length, before, 'selection does not call a provider')
    input(m, '#followup-question', 'Why 中😀?')
    m.root.querySelector('.connected-followup form').dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true }))
    await waitFor(() => m.root.querySelector('.followup-start') && !m.root.querySelector('.followup-start').disabled)
    m.root.querySelector('.followup-start').click()
    await waitFor(() => m.root.textContent.includes(connectedFollowupsCopyFor('en').lost))
    const original = clone(wire.followup)
    m.root.querySelector('.connection-form button:last-child').click(); await nextTick()
    assert.equal(m.root.querySelector('.turn-record'), null)
    assert.equal(m.root.querySelector('#followup-recovery-id').value, '')
    await connect(m)
    input(m, '#followup-recovery-id', original.turn_id); input(m, '#followup-recovery-hash', original.plan_sha256)
    m.root.querySelector('.connected-followup .recovery').dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true }))
    await waitFor(() => m.root.querySelector('.followup-read'))
    assert.equal(m.root.querySelector('.followup-start'), null)
    assert.equal(wire.calls.filter(c => c.url.includes('/connected-followup/start/')).length, 1)
    for (const locale of ['en', 'zh', 'ms']) { const count = wire.calls.length; input(m, '#workbench-language', locale); await nextTick(); assert.ok(m.root.textContent.includes(connectedFollowupsCopyFor(locale).title)); assert.equal(wire.calls.length, count) }
    const followupCalls = wire.calls.filter(c => c.url.includes('/connected-followup/'))
    assert.deepEqual(followupCalls.map(c => new URL(c.url).pathname.split('/')[3]), ['plan', 'start', 'status'])
    assert.ok(followupCalls.every(c => c.request.headers.Authorization === 'Bearer private-token' && c.request.credentials === 'omit' && !new URL(c.url).search))
    assert.equal(m.root.querySelector('.connected-followup [token],.connected-followup [origin]'), null)
  } finally { m.cleanup(); globalThis.fetch = previous }
})
function followupPlan(payload, report) {
  const r = reportDone(report), binding = { display_graph_id: r.binding.display_graph_id, principal: r.binding.principal, scope: clone(r.binding.scope), report: { report_id: r.report_id, plan_sha256: r.plan_sha256, receipt_sha256: r.receipt_sha256, manifest_sha256: digest(r.manifest), full_report_sha256: r.manifest.files.find(f => f.name === 'full_report.md').sha256 }, native_binding: clone(r.binding) }
  const head = digest({ schema_version: 1, report_id: r.report_id, report_plan_sha256: r.plan_sha256, turns: [] })
  const v = { schema_version: 1, turn_id: payload.turn_id, plan_sha256: null, binding, options: { question: payload.question, output_language: payload.output_language, expected_history_sha256: payload.expected_history_sha256 }, history: { head_sha256: head, total_completed: 0, window_start: 1, pairs: [] }, report_context: { file_sha256: binding.report.full_report_sha256, prefix_sha256: bytesHash(prose), prefix_characters: [...prose].length, total_characters: [...prose].length, truncated: false }, context_sha256: hash, source_projection_sha256: hash, model_label: 'scripted', limits: { max_calls: 8, max_input_bytes: 1048576, max_output_tokens: 4096, max_run_seconds: 600 }, ceiling_microusd: 12345, authorization: { model_calls_enabled: true, budget_configured: true }, state: 'planned', progress: { stage: 'planned', percent: 0, completed_sections: 0, total_sections: 0 }, workflow: null, receipt: null, receipt_sha256: null, manifest: null, published_history_head_sha256: null, cleanup: { known: false, pending: null, owner_thread_alive: null }, cancel_requested: false, error_code: null }
  v.plan_sha256 = digest(pick(v, 'schema_version turn_id binding options history report_context context_sha256 source_projection_sha256 model_label limits ceiling_microusd')); return v
}
function followupDone(known) {
  const v = clone(known), answer = 'Grounded [[source:' + uid(30) + ']] [[native:twitter:0:' + hash + ']]'
  const conversation = { schema_version: 1, report_id: v.binding.report.report_id, report_plan_sha256: v.binding.report.plan_sha256, total_completed: 1, pairs: [{ turn_id: v.turn_id, plan_sha256: v.plan_sha256, ordinal: 1, question: v.options.question, answer, answer_sha256: bytesHash(answer), predecessor_head_sha256: v.history.head_sha256, receipt_sha256: null, published_head_sha256: null }] }
  v.manifest = { schema_version: 1, files: Object.entries({ 'turn.json': '{}', 'answer.md': answer, 'conversation.json': JSON.stringify(conversation), 'retrieval_evidence.json': '{}', 'native_evidence.json': '{}', 'tool_trace.json': '{}' }).map(([name, content]) => ({ name, size: Buffer.byteLength(content), sha256: bytesHash(content) })) }
  v.state = 'completed'; v.progress = { stage: 'completed', percent: 100, completed_sections: 1, total_sections: 1 }; v.cleanup = { known: true, pending: false, owner_thread_alive: false }
  v.receipt = { schema_version: 1, turn_id: v.turn_id, plan_sha256: v.plan_sha256, parent_report_id: v.binding.report.report_id, parent_report_plan_sha256: v.binding.report.plan_sha256, parent_report_receipt_sha256: v.binding.report.receipt_sha256, context_sha256: v.context_sha256, history_head_sha256: v.history.head_sha256, ordinal: 1, manifest_sha256: digest(v.manifest), output_language: v.options.output_language, reference_integrity: 'validated', semantic_support_status: 'not_reviewed' }; v.receipt_sha256 = digest(v.receipt)
  v.published_history_head_sha256 = digest({ schema_version: 1, report_id: v.binding.report.report_id, report_plan_sha256: v.binding.report.plan_sha256, predecessor_head_sha256: v.history.head_sha256, ordinal: 1, turn_id: v.turn_id, plan_sha256: v.plan_sha256, question_sha256: bytesHash(v.options.question), answer_sha256: bytesHash(answer), receipt_sha256: v.receipt_sha256 }); return v
}
globalThis.__u07gParentFixture = { prepPayload, source: sourceRecord }
const envelope = data => new Response(JSON.stringify({ success: true, data }), { headers: { 'Content-Type': 'application/json' } })
function mount() { sharedLocale.setUiLocale('en'); const root = document.createElement('div'); document.body.append(root); const app = createApp(parent.component); app.use(sharedLocale.default); app.component('RouterLink', { props: ['to'], setup: (p, { slots }) => () => h('a', { href: p.to }, slots.default?.()) }); app.mount(root); let mounted = true; return { root, cleanup() { if (mounted) { mounted = false; app.unmount(); root.remove() } } } }
function input(m, selector, value) { const e = m.root.querySelector(selector); e.value = value; e.dispatchEvent(new dom.window.Event(e.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true })) }
async function waitFor(predicate) { const end = Date.now() + 3000; do { await nextTick(); if (predicate()) return; await new Promise(r => setImmediate(r)) } while (Date.now() < end); assert.ok(predicate(), 'bounded parent condition did not complete') }
async function connect(m) { input(m, '#graph-id', 'graph_1'); input(m, '#bearer-token', 'private-token'); m.root.querySelector('.connection-form').dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true })); await waitFor(() => !m.root.querySelector('.fixture-preparation').disabled) }
async function seedNative(m) { m.root.querySelector('.fixture-preparation').click(); await waitFor(() => !m.root.querySelector('.native-launch .review').disabled); m.root.querySelector('.native-launch .review').click(); await waitFor(() => m.root.querySelector('.native-launch .refresh') && !m.root.querySelector('.native-launch .refresh').disabled); m.root.querySelector('.native-launch .refresh').click(); await waitFor(() => m.root.querySelector('.use-report') && !m.root.querySelector('.use-report').disabled) }
function transport(options = {}) {
  const calls = []; let launch, report, followup
  return { calls, get report() { return report }, get followup() { return followup }, fetch: async (url, request) => {
    calls.push({ url, request }); const p = request.body ? JSON.parse(request.body) : null
    if (url.includes('/data/')) return envelope({ graph_id: 'graph_1', nodes: [], edges: [], node_count: 0, edge_count: 0 })
    if (url.includes('/preparation/')) return envelope(preparation(url.endsWith('/start')))
    if (url.includes('/native-launch/plan/')) { launch = nativePlan(p); return envelope(launch) }
    if (url.includes('/native-launch/status/')) { launch = nativeDone(launch); return envelope(launch) }
    if (url.includes('/connected-report/plan/')) { report = reportPlan(p, launch); return envelope(report) }
    if (url.includes('/connected-report/start/')) { if (options.lost) throw new Error('private transport failure'); if (options.startDenial) return new Response(JSON.stringify({ success: false, error: { code: options.startDenial } }), { status: 409, headers: { 'Content-Type': 'application/json' } }); report = reportDone(report); return envelope(report) }
    if (url.includes('/connected-report/status/')) { if (options.denied) return new Response('', { status: 401 }); report = options.uncertain ? { ...report, state: 'uncertain', error_code: 'report_uncertain', progress: { stage: 'uncertain', percent: 0, completed_sections: 0, total_sections: 0 } } : reportDone(report); return envelope(report) }
    if (url.includes('/connected-report/read/')) return envelope({ schema_version: 1, report, content: prose })
    if (url.includes('/connected-followup/plan/')) { followup = followupPlan(p, report); return envelope(followup) }
    if (url.includes('/connected-followup/start/')) { if (options.lostFollowup) throw new Error('lost follow-up Start reply'); followup = followupDone(followup); return envelope(followup) }
    if (url.includes('/connected-followup/status/')) { followup = followupDone(followup); return envelope(followup) }
    if (url.includes('/connected-followup/read/')) return envelope({ schema_version: 1, turn: followup, content: 'Grounded [[source:' + uid(30) + ']] [[native:twitter:0:' + hash + ']]' })
    if (url.includes('/source/original-metadata/') || url.includes('/source/original/')) {
      const bytes = Buffer.from('%PDF-1.4\nfixture original\n%%EOF\n'), source = sourceRecord()
      const data = { schema_version: 2, binary_retained: true, graph_ingestion_executed: false, source, binary: { contract_version: 1, project_id: source.project_id, source_revision: source.source_revision, media_type: 'application/pdf', byte_length: bytes.length, sha256: bytesHash(bytes) } }
      if (url.includes('/source/original/')) data.content_base64 = bytes.toString('base64')
      return envelope(data)
    }
    throw new Error('Unexpected fixture route')
  } }
}

// Responses are explicitly released; elapsed time is not an ownership signal.
function heldTransport(wire) {
  const held = new Map(), pending = new Map()
  return {
    held, pending,
    fetch: async (url, request) => {
      const response = await wire.fetch(url, request)
      const route = [...held.keys()].find(part => url.includes(part))
      if (!route) return response
      return new Promise(resolve => { pending.set(route, { signal: request.signal, release: () => { pending.delete(route); resolve(response) } }) })
    },
    releaseAll() { for (const item of [...pending.values()]) item.release() }
  }
}
async function reviewFollowup(m) {
  await seedNative(m); m.root.querySelector('.use-report').click()
  await waitFor(() => !m.root.querySelector('.connected-reports fieldset').disabled)
  input(m, '#report-requirement', 'Compare evidence')
  m.root.querySelector('.connected-reports form').dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true }))
  await waitFor(() => m.root.querySelector('.report-start') && !m.root.querySelector('.report-start').disabled)
  m.root.querySelector('.report-start').click(); await waitFor(() => m.root.querySelector('.report-followup'))
  m.root.querySelector('.report-followup').click(); await waitFor(() => !m.root.querySelector('.connected-followup fieldset').disabled)
  input(m, '#followup-question', 'Why 中😀?')
  m.root.querySelector('.connected-followup form').dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true }))
  await waitFor(() => m.root.querySelector('.followup-start') && !m.root.querySelector('.followup-start').disabled)
}
async function loadPdfMetadata(m) {
  input(m, '#source-original-revision', uid(5))
  m.root.querySelector('#source-original-revision').closest('.source-form').querySelector('button').click()
  await waitFor(() => m.root.querySelector('#source-original-revision').closest('.source-form').querySelectorAll('button').length === 2)
  await waitFor(() => !m.root.querySelector('#source-original-revision').closest('.source-form').querySelector('button').disabled)
}

for (const method of ['start', 'read']) for (const pdf of ['metadata', 'download']) {
  test(`PDF ${pdf} preserves a held follow-up ${method} in the actual parent`, async () => {
    const previous = globalThis.fetch, oldCreate = URL.createObjectURL, oldRevoke = URL.revokeObjectURL, oldClick = dom.window.HTMLAnchorElement.prototype.click
    const wire = transport(), gate = heldTransport(wire), route = `/connected-followup/${method}/`
    globalThis.fetch = gate.fetch; URL.createObjectURL = () => 'blob:owned-pdf'; URL.revokeObjectURL = () => {}; dom.window.HTMLAnchorElement.prototype.click = () => {}
    const m = mount()
    try {
      await connect(m); await loadPdfMetadata(m); await reviewFollowup(m)
      if (method === 'read') { m.root.querySelector('.followup-start').click(); await waitFor(() => m.root.querySelector('.followup-read') && !m.root.querySelector('.followup-read').disabled) }
      gate.held.set(route, true); m.root.querySelector(`.followup-${method}`).click(); await waitFor(() => gate.pending.has(route))
      const held = gate.pending.get(route), identity = clone(wire.followup)
      const beforeLanguage = wire.calls.length, outputLanguage = m.root.querySelector('#followup-language').value
      for (const locale of ['ms', 'zh', 'en']) {
        input(m, '#workbench-language', locale); await nextTick()
        assert.equal(sharedLocale.default.global.locale.value, locale); assert.equal(localStorage.getItem('locale'), locale); assert.equal(document.documentElement.lang, locale)
        assert.equal(m.root.querySelector('#followup-language').value, outputLanguage)
        assert.equal(m.root.querySelector('#followup-recovery-hash').value, identity.plan_sha256)
        assert.equal(held.signal.aborted, false); assert.equal(wire.calls.length, beforeLanguage)
        assert.equal(m.root.querySelector('.followup-refresh').disabled, true)
      }
      const controls = m.root.querySelector('#source-original-revision').closest('.source-form').querySelectorAll('button')
      const pdfRoute = pdf === 'metadata' ? '/source/original-metadata/' : '/source/original/'
      const before = wire.calls.filter(c => c.url.includes(pdfRoute)).length
      controls[pdf === 'metadata' ? 0 : 1].click()
      await waitFor(() => wire.calls.filter(c => c.url.includes(pdfRoute)).length === before + 1)
      await waitFor(() => !controls[0].disabled)
      assert.equal(held.signal.aborted, false, 'PDF read must preserve follow-up transport ownership')
      assert.equal(m.root.querySelector('#followup-recovery-id').value, identity.turn_id)
      held.release(); await waitFor(() => method === 'read' ? m.root.querySelector('.connected-followup .answer') : m.root.querySelector('.followup-read') && !m.root.querySelector('.followup-read').disabled)
      assert.equal(wire.calls.filter(c => c.url.includes('/connected-followup/start/')).length, 1)
      assert.equal(m.root.querySelector('.followup-start'), null)
      assert.ok(!m.root.textContent.includes(connectedFollowupsCopyFor('en').lost))
    } finally { m.cleanup(); gate.releaseAll(); globalThis.fetch = previous; URL.createObjectURL = oldCreate; URL.revokeObjectURL = oldRevoke; dom.window.HTMLAnchorElement.prototype.click = oldClick }
  })
}

for (const method of ['start', 'read']) {
  test(`global Cancel aborts held PDF and follow-up ${method} and rejects late publication`, async () => {
    const previous = globalThis.fetch, wire = transport(), gate = heldTransport(wire), route = `/connected-followup/${method}/`, pdfRoute = '/source/original-metadata/', reportRoute = '/connected-report/read/'
    globalThis.fetch = gate.fetch; const m = mount()
    try {
      await connect(m); await loadPdfMetadata(m); await reviewFollowup(m)
      if (method === 'read') { m.root.querySelector('.followup-start').click(); await waitFor(() => m.root.querySelector('.followup-read') && !m.root.querySelector('.followup-read').disabled) }
      gate.held.set(route, true); m.root.querySelector(`.followup-${method}`).click(); await waitFor(() => gate.pending.has(route))
      gate.held.set(reportRoute, true); m.root.querySelector('.report-read').click(); await waitFor(() => gate.pending.has(reportRoute))
      gate.held.set(pdfRoute, true); m.root.querySelector('#source-original-revision').closest('.source-form').querySelector('button').click(); await waitFor(() => gate.pending.has(pdfRoute))
      const followupHeld = gate.pending.get(route), pdfHeld = gate.pending.get(pdfRoute), reportHeld = gate.pending.get(reportRoute)
      assert.equal(followupHeld.signal.aborted, false); assert.equal(reportHeld.signal.aborted, false)
      m.root.querySelector('.connection > button').click()
      assert.equal(followupHeld.signal.aborted, true); assert.equal(pdfHeld.signal.aborted, true); assert.equal(reportHeld.signal.aborted, true)
      await waitFor(() => !m.root.querySelector('.followup-refresh').disabled)
      gate.releaseAll(); await nextTick(); await new Promise(r => setImmediate(r)); await nextTick()
      assert.equal(m.root.querySelector('.connected-followup .answer'), null)
      assert.equal(m.root.querySelector('.connected-reports .narrative'), null)
      if (method === 'start') assert.equal(m.root.querySelector('.followup-read'), null)
      assert.equal(wire.calls.filter(c => c.url.includes('/connected-followup/start/')).length, 1)
      assert.ok(m.root.textContent.includes(connectedFollowupsCopyFor('en').errors.cancelled))
    } finally { m.cleanup(); gate.releaseAll(); globalThis.fetch = previous }
  })
}
