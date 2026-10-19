// Actual mounted SFC fixtures. Authored only; no browser qualification claim.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createHash, webcrypto } from 'node:crypto'
import { JSDOM } from 'jsdom'
import { parse, compileScript } from '@vue/compiler-sfc'
import { connectedReportsCopy, connectedReportsCopyFor } from '../src/i18n/connectedReports.js'
import { validateConnectedReportRead, validateConnectedReportResult } from '../src/api/connectedReports.js'
globalThis.crypto ||= webcrypto
const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://127.0.0.1:5173/research' })
for (const name of ['window', 'document', 'Element', 'HTMLElement', 'SVGElement', 'Node']) globalThis[name] = name === 'window' ? dom.window : dom.window[name]
const { createApp, h, nextTick, reactive } = await import('vue')
const file = new URL('../src/components/workbench/ConnectedReports.vue', import.meta.url)
const { descriptor } = parse(readFileSync(file, 'utf8'), { filename: file.pathname })
const compiled = compileScript(descriptor, { id: 'connected-reports', inlineTemplate: true, genDefaultAs: '__component' })
let source = compiled.content.replace(/from (['"])vue\1/g, `from ${JSON.stringify(import.meta.resolve('vue'))}`)
source = source.replace(/from (['"])(\.{1,2}\/[^'"]+)\1/g, (_m, _q, relative) => `from ${JSON.stringify(new URL(relative, file).href)}`)
const activeReadValidations = new Set(); let readValidations = 0
globalThis.__u07gTrackReadValidation = (...args) => {
  readValidations++; const promise = validateConnectedReportRead(...args); activeReadValidations.add(promise)
  promise.then(() => activeReadValidations.delete(promise), () => activeReadValidations.delete(promise)); return promise
}
source = source.replace(/\bvalidateConnectedReportRead\(/g, 'globalThis.__u07gTrackReadValidation(')
const activeResultValidations = new Set(); let resultValidations = 0
globalThis.__u07gTrackResultValidation = (...args) => {
  resultValidations++; const promise = validateConnectedReportResult(...args); activeResultValidations.add(promise)
  promise.then(() => activeResultValidations.delete(promise), () => activeResultValidations.delete(promise)); return promise
}
source = source.replace(/\bvalidateConnectedReportResult\(/g, 'globalThis.__u07gTrackResultValidation(')
const component = (await import(`data:text/javascript;base64,${Buffer.from(source + '\nexport default __component').toString('base64')}`)).default
const uid = n => `00000000-0000-0000-0000-${String(n).padStart(12, '0')}`, hash = 'a'.repeat(64), clone = v => JSON.parse(JSON.stringify(v))
function ascii(v) { if (Array.isArray(v)) return '[' + v.map(ascii).join(',') + ']'; if (v && typeof v === 'object') return '{' + Object.keys(v).sort().map(k => ascii(k) + ':' + ascii(v[k])).join(',') + '}'; return JSON.stringify(v).replace(/[\u007f-\uffff]/g, c => '\\u' + c.charCodeAt(0).toString(16).padStart(4, '0')) }
const digest = v => createHash('sha256').update(ascii(v), 'ascii').digest('hex'), bytesHash = v => createHash('sha256').update(v).digest('hex')
const pick = (v, keys) => Object.fromEntries(keys.split(' ').map(k => [k, v[k]]))
const hostile = '<img src=x onerror=alert(1)> <script>secret</script> 中😀'
function selection() {
  const files = ['state.json', 'simulation_config.json', 'source_grounding.json', 'twitter_profiles.csv'].map(name => ({ name, sha256: hash, size: 123 }))
  const preparation = { schema_version: 1, display_graph_id: 'graph_1', scope: { schema_version: 1, workspace_id: uid(1), project_id: uid(2), graph_id: uid(3), run_id: null, branch_id: null, layer: 'source' }, project_revision: 1, operation_id: uid(9), source: { source_revision: uid(5), source_name: hostile, source_sha256: hash }, options: { types: null, max_agents: 10, seed: 0, platforms: ['twitter'], max_rounds: 24, simulation_requirement: hostile }, actors: [{ source_entity_uuid: uid(7), name: hostile, labels: ['Person'] }], projection_sha256: hash, plan_sha256: null, state: 'ready', progress: { stage: 'ready', completed: 100, total: 100 }, error_code: null, authorization: { model_calls_enabled: true, ceiling_microusd: '12345' }, receipt: { simulation_id: 'sim_' + uid(9).replaceAll('-', ''), artifact_sha256: digest({ schema_version: 1, files }), files }, graph_snapshot_atomic: false, model_calls_started: true, simulation_executed: false }
  preparation.plan_sha256 = digest(pick(preparation, 'schema_version display_graph_id scope project_revision operation_id source options actors projection_sha256'))
  const launch = { schema_version: 1, display_graph_id: 'graph_1', scope: clone(preparation.scope), preparation: { operation_id: preparation.operation_id, plan_sha256: preparation.plan_sha256, simulation_id: preparation.receipt.simulation_id, artifact_sha256: preparation.receipt.artifact_sha256 }, request: { schema_version: 1, principal: 'local-research', project_id: uid(2), project_revision: 1, simulation_id: preparation.receipt.simulation_id, run_id: uid(10), artifact_sha256: preparation.receipt.artifact_sha256, runtime_sha256: hash, platforms: ['twitter'], seed: 0, max_rounds: 24 }, limits: { max_calls: 20, max_input_bytes: 2097152, max_output_tokens: 4096, max_run_seconds: 600 }, ceiling_microusd: '12345', model_label: 'scripted', launch_sha256: null, state: 'completed', error_code: null, authorization: { model_calls_enabled: false }, workflow: null, receipt: null, cancel_requested: false, cleanup: { known: true, pending: false, owner_thread_alive: false } }
  launch.launch_sha256 = digest(pick(launch, 'schema_version display_graph_id scope preparation request limits ceiling_microusd model_label')); launch.receipt = { run_id: uid(10), attempt_id: uid(12), instance_id: uid(13), request_fingerprint: digest(launch.request), outcome: 'completed', evidence_sha256: hash }
  return { launch, preparation }
}
function report(payload = { report_id: uid(20), requirement: hostile, output_language: 'en', native_windows: null }, s = selection()) {
  const l = s.launch, w = payload.native_windows?.[0], windows = w ? [{ offset: w.offset, count: w.count }] : [{ offset: 0, count: 2 }]
  const v = { schema_version: 1, report_id: payload.report_id, plan_sha256: null, binding: { display_graph_id: 'graph_1', principal: 'local-research', scope: s.preparation.scope, project_revision: 1, source: s.preparation.source, preparation: l.preparation, native: { run_id: uid(10), launch_sha256: l.launch_sha256, request_fingerprint: l.receipt.request_fingerprint, evidence_sha256: hash, platforms: ['twitter'] }, coverage: [{ platform: 'twitter', total_records: 2, selected_records: windows[0].count, complete: windows[0].count === 2, windows }], reference_keys: ['source:' + uid(30), ...Array.from({ length: windows[0].count }, (_, i) => `native:twitter:${windows[0].offset + i}:${hash}`)] }, options: pick(payload, 'requirement output_language native_windows'), context_sha256: hash, source_projection_sha256: hash, model_label: 'scripted 中😀', limits: { max_calls: 64, max_input_bytes: 262144, max_output_tokens: 4096, max_run_seconds: 600 }, ceiling_microusd: 12345, authorization: { model_calls_enabled: true, budget_configured: true }, state: 'planned', progress: { stage: 'planned', percent: 0, completed_sections: 0, total_sections: 0 }, workflow: null, receipt: null, receipt_sha256: null, manifest: null, cleanup: { known: false, pending: null, owner_thread_alive: null }, cancel_requested: false, error_code: null }
  if (payload.schema_version === 2) v.binding.source_set = { schema_version: 1, source_revisions: [...payload.source_revisions] }
  v.plan_sha256 = digest(pick(v, 'schema_version report_id binding options context_sha256 source_projection_sha256 model_label limits ceiling_microusd')); return v
}
const prose = '# Narrative 中😀\n\n[[source:' + uid(30) + ']] [[native:twitter:0:' + hash + ']]\n\n' + hostile + '\n\n[unsafe](javascript:alert(1))'
function done(v = report()) {
  v = clone(v); v.state = 'completed'; v.cleanup = { known: true, pending: false, owner_thread_alive: false }; v.progress = { stage: 'completed', percent: 100, completed_sections: 1, total_sections: 1 }
  v.manifest = { schema_version: 1, files: ['meta.json', 'outline.json', 'full_report.md', 'retrieval_evidence.json', 'native_evidence.json', 'section_01.md'].map(name => { const content = name.endsWith('.md') ? prose : '{}'; return { name, size: Buffer.byteLength(content), sha256: bytesHash(content) } }) }
  v.receipt = { schema_version: 1, report_id: v.report_id, plan_sha256: v.plan_sha256, context_sha256: v.context_sha256, manifest_sha256: digest(v.manifest), output_language: v.options.output_language, reference_integrity: 'validated', semantic_support_status: 'not_reviewed' }; v.receipt_sha256 = digest(v.receipt); return v
}
function downloadReply(known, payload) { const name = { report: 'full_report.md', native_evidence: 'native_evidence.json' }[payload.kind], f = known.manifest.files.find(v => v.name === name); return { schema_version: 1, report_id: known.report_id, plan_sha256: known.plan_sha256, receipt_sha256: known.receipt_sha256, artifact: { ...f, mime: name.endsWith('.md') ? 'text/markdown' : 'application/json', content_base64: Buffer.from(name.endsWith('.md') ? prose : '{}').toString('base64') } } }
function mount(initial = {}) {
  const props = reactive({ methods: { plan: async p => report(p), status: async () => done(), clear: () => {} }, selection: null, connected: true, busy: false, displayGraphId: 'graph_1', resetVersion: 0, locale: 'en', ...initial })
  const root = document.createElement('div'); document.body.append(root); const app = createApp({ setup: () => () => h(component, props) }); app.mount(root); let mounted = true
  return { props, root, cleanup() { if (mounted) { mounted = false; app.unmount(); root.remove() } } }
}
async function waitFor(predicate) { const end = Date.now() + 2000; do { await nextTick(); if (predicate()) return; await new Promise(r => setImmediate(r)) } while (Date.now() < end); assert.ok(predicate(), 'bounded component condition did not complete') }
function input(m, selector, value) { const e = m.root.querySelector(selector); e.value = value; e.dispatchEvent(new dom.window.Event(e.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true })) }
async function recover(m, value = done()) { input(m, '#report-recovery-id', value.report_id); input(m, '#report-recovery-hash', value.plan_sha256); m.root.querySelector('.recovery').dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true })); await waitFor(() => m.root.querySelector('.report-record')) }
async function reviewed(m) { await waitFor(() => !m.root.querySelector('fieldset').disabled); input(m, '#report-requirement', hostile); m.root.querySelector('form').dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true })); await waitFor(() => m.root.querySelector('.report-start')) }

const catalog = () => [5, 31, 32].map(n => ({ project_id: uid(2), source_revision: uid(n), source_name: `Document ${n} <literal> 雨`, text_sha256: hash, byte_length: 8, codepoint_length: 4, recorded_at: '2026-10-19T00:00:00Z' }))
function checkSource(m, revision) {
  const field = [...m.root.querySelectorAll('.report-source-checkbox')].find(e => e.value === revision)
  assert.ok(field); field.checked = !field.checked; field.dispatchEvent(new dom.window.Event('change', { bubbles: true }))
}

test('a multi-source preparation pins reports to its frozen documents without a live catalog', async () => {
  const s = selection(), calls = []
  s.preparation.schema_version = 2
  s.preparation.sources = [clone(s.preparation.source), { source_revision: uid(31), source_name: 'Prepared <literal> DOCX 雨', source_sha256: hash }]
  s.preparation.plan_sha256 = digest(pick(s.preparation, 'schema_version display_graph_id scope project_revision operation_id source sources options actors projection_sha256'))
  s.launch.preparation.plan_sha256 = s.preparation.plan_sha256
  s.launch.launch_sha256 = digest(pick(s.launch, 'schema_version display_graph_id scope preparation request limits ceiling_microusd model_label'))
  const m = mount({ selection: s, sourceCatalog: null, methods: { plan: async p => { calls.push(clone(p)); return report(p, s) }, clear: () => {} } })
  try {
    await waitFor(() => m.root.querySelector('.report-prepared-sources'))
    assert.equal(m.root.querySelector('.report-source-checkbox,.report-sources'), null)
    assert.ok(m.root.querySelector('.report-prepared-sources').textContent.includes(uid(31)))
    assert.equal(calls.length, 0); await reviewed(m)
    assert.equal(calls[0].schema_version, 2); assert.deepEqual(calls[0].source_revisions, [uid(5), uid(31)])
    m.props.sourceCatalog = catalog().map(source => ({ ...source, source_name: 'unrelated current catalog' })); await nextTick()
    assert.ok(m.root.querySelector('.report-start')); assert.equal(calls.length, 1)
    await reviewed(m)
    assert.deepEqual(calls[1].source_revisions, [uid(5), uid(31)])
    assert.ok(m.root.querySelector('.report-prepared-sources').textContent.includes('Prepared <literal> DOCX 雨'))
    assert.equal(m.root.querySelector('img,script'), null)
  } finally { m.cleanup() }
})

test('catalog retains fixed anchor, filters project, defaults V1 and sends explicit extras in displayed order', async () => {
  const calls = [], sourceCatalog = [...catalog(), { ...catalog()[1], project_id: uid(99), source_revision: uid(98) }]
  const m = mount({ selection: selection(), sourceCatalog, methods: { plan: async p => { calls.push(clone(p)); return report(p) }, clear: () => {} } })
  try {
    await waitFor(() => !m.root.querySelector('fieldset').disabled)
    assert.ok(m.root.querySelector('.report-anchor').textContent.includes(uid(5)))
    const fields = [...m.root.querySelectorAll('.report-source-checkbox')]
    assert.deepEqual(fields.map(e => e.value), [uid(31), uid(32)])
    assert.ok(fields.every(e => !e.checked && e.closest('label')))
    assert.ok(m.root.querySelector('.report-sources legend').textContent)
    assert.equal(calls.length, 0)
    await reviewed(m); assert.equal(calls[0].schema_version, 1); assert.equal(Object.hasOwn(calls[0], 'source_revisions'), false)
    checkSource(m, uid(32)); checkSource(m, uid(31)); await nextTick()
    assert.equal(m.root.querySelector('.report-start'), null); assert.equal(calls.length, 1)
    await reviewed(m)
    assert.equal(calls[1].schema_version, 2)
    assert.deepEqual(calls[1].source_revisions, [uid(5), uid(31), uid(32)])
    assert.deepEqual([...m.root.querySelectorAll('.report-source-set li')].map(e => e.textContent), calls[1].source_revisions)
    assert.equal(m.root.querySelector('img,script'), null)
  } finally { m.cleanup() }
})

for (const change of ['catalog', 'selection']) {
  test(`${change} change drops selected extras and fences a pending plan without dropping native selection on catalog load`, async () => {
    let finish, requested
    const m = mount({ selection: selection(), sourceCatalog: catalog(), methods: {
      plan: p => { requested = clone(p); return new Promise(resolve => { finish = () => resolve(report(p)) }) }, clear: () => {}
    } })
    try {
      await waitFor(() => !m.root.querySelector('fieldset').disabled)
      checkSource(m, uid(31)); input(m, '#report-requirement', 'Compare')
      m.root.querySelector('form').dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true }))
      await waitFor(() => finish)
      assert.deepEqual(requested.source_revisions, [uid(5), uid(31)])
      if (change === 'catalog') m.props.sourceCatalog = catalog().map(s => ({ ...s, source_name: s.source_name + ' updated' }))
      else m.props.selection = selection()
      await waitFor(() => !m.root.querySelector('fieldset').disabled)
      assert.ok([...m.root.querySelectorAll('.report-source-checkbox')].every(e => !e.checked))
      const before = resultValidations; finish()
      await waitFor(() => resultValidations > before && activeResultValidations.size === 0)
      assert.equal(m.root.querySelector('.report-record,.report-start'), null)
      assert.ok(m.root.querySelector('.report-anchor'))
    } finally { m.cleanup() }
  })
}

test('catalog invalidation keeps attempted immutable V2 history and rejects a late Start reply', async () => {
  let finish, known, starts = 0
  const m = mount({ selection: selection(), sourceCatalog: catalog(), methods: {
    plan: async p => known = report(p), start: () => { starts++; return new Promise(resolve => { finish = () => resolve({ ...clone(known), state: 'queued', progress: { stage: 'queued', percent: 0, completed_sections: 0, total_sections: 0 } }) }) }, clear: () => {}
  } })
  try {
    await waitFor(() => !m.root.querySelector('fieldset').disabled); checkSource(m, uid(31)); await reviewed(m)
    const original = clone(known)
    m.root.querySelector('.report-start').click(); await waitFor(() => finish)
    m.props.sourceCatalog = catalog().slice(0, 1); await nextTick()
    const before = resultValidations; finish()
    await waitFor(() => resultValidations > before && activeResultValidations.size === 0)
    const record = m.root.querySelector(`[data-report-id="${original.report_id}"]`)
    assert.ok(record); assert.ok(record.textContent.includes(original.plan_sha256))
    assert.deepEqual([...record.querySelectorAll('.report-source-set li')].map(e => e.textContent), [uid(5), uid(31)])
    assert.equal(record.querySelector('.report-start'), null)
    assert.equal(record.querySelector('h3').textContent, connectedReportsCopy.en.states.planned)
    assert.equal(m.root.querySelector('#report-recovery-hash').value, original.plan_sha256)
    assert.equal(starts, 1)
    m.props.resetVersion++; await nextTick()
    assert.equal(m.root.querySelector('.report-record'), null)
    assert.equal(m.root.querySelector('#report-recovery-hash').value, '')
  } finally { m.cleanup() }
})

test('recovery displays the immutable full source set without a live source catalog', async () => {
  const known = report({ report_id: uid(20), requirement: 'Compare', output_language: 'en', native_windows: null,
    schema_version: 2, source_revisions: [uid(5), uid(31), uid(32)] })
  const m = mount({ methods: { status: async () => known, clear: () => {} } })
  try {
    await recover(m, known)
    assert.deepEqual([...m.root.querySelectorAll('.report-source-set li')].map(e => e.textContent), [uid(5), uid(31), uid(32)])
    assert.equal(m.root.querySelector('.report-start'), null)
  } finally { m.cleanup() }
})

test('mounted selection, options and locale switching never fetch or generate implicitly', async () => {
  let calls = 0; const m = mount({ selection: selection(), methods: { plan: async p => { calls++; return report(p) }, clear: () => {} } })
  try {
    await waitFor(() => !m.root.querySelector('fieldset').disabled)
    for (const locale of ['en', 'zh', 'ms']) { m.props.locale = locale; await nextTick(); const copy = connectedReportsCopyFor(locale); assert.ok(m.root.textContent.includes(copy.title)); assert.deepEqual(Object.keys(copy).sort(), Object.keys(connectedReportsCopy.en).sort()); assert.deepEqual(Object.keys(copy.errors).sort(), Object.keys(connectedReportsCopy.en.errors).sort()); assert.deepEqual(Object.keys(copy.states).sort(), Object.keys(connectedReportsCopy.en.states).sort()) }
    input(m, '#report-output-language', 'ms'); input(m, '#report-coverage', 'selected'); await nextTick(); input(m, '#report-offset-twitter', '1'); input(m, '#report-count-twitter', '1'); assert.equal(calls, 0)
    await reviewed(m); assert.equal(calls, 1); assert.ok(m.root.textContent.includes(connectedReportsCopy.ms.partialCoverage)); assert.equal(m.root.querySelector('img,script'), null)
    assert.equal(m.root.querySelector('.feedback').getAttribute('aria-atomic'), 'true'); assert.equal(document.activeElement, m.root.querySelector('.feedback'))
  } finally { m.cleanup() }
})
test('lost Start retains original identity across option changes and permits only same-ID Refresh', async () => {
  let starts = 0, known; const m = mount({ selection: selection(), methods: { plan: async p => known = report(p), start: async () => { starts++; throw { code: 'transport_failure' } }, status: async () => ({ ...clone(known), state: 'uncertain', error_code: 'report_uncertain', progress: { stage: 'uncertain', percent: 0, completed_sections: 0, total_sections: 0 } }), clear: () => {} } })
  try {
    await reviewed(m); const id = known.report_id; m.root.querySelector('.report-start').click(); await waitFor(() => m.root.querySelector('section').getAttribute('aria-busy') === 'false'); assert.equal(starts, 1); assert.equal(m.root.querySelector('.report-start').disabled, true)
    input(m, '#report-requirement', 'new requirement'); await nextTick(); assert.equal(m.root.querySelector('.report-start'), null); assert.ok(m.root.querySelector(`[data-report-id="${id}"]`)); assert.ok(m.root.textContent.includes(hostile))
    m.root.querySelector('.report-refresh').click(); await waitFor(() => m.root.textContent.includes(connectedReportsCopy.en.states.uncertain)); assert.ok(m.root.textContent.includes(connectedReportsCopy.en.unknown)); assert.equal(starts, 1)
  } finally { m.cleanup() }
})
test('model-disabled reviewed plans cannot Start; manual recovery works without a native selection', async () => {
  let starts = 0; const m = mount({ selection: selection(), methods: { plan: async p => ({ ...report(p), authorization: { model_calls_enabled: false, budget_configured: true } }), start: () => { starts++ }, status: async () => done(), clear: () => {} } })
  try { await reviewed(m); assert.equal(m.root.querySelector('.report-start').disabled, true); assert.ok(m.root.textContent.includes(connectedReportsCopy.en.disabled)); m.root.querySelector('.report-clear').click(); await nextTick(); await recover(m); assert.equal(m.root.querySelector('.report-start'), null); assert.equal(starts, 0); assert.ok(m.root.textContent.includes(connectedReportsCopy.en.recovered)) } finally { m.cleanup() }
})
test('mounted manual recovery displays the exact signed-64 ceiling without JSON rounding', async () => {
  const known = report(); known.ceiling_microusd = 9223372036854775807n
  const identity = pick(known, 'schema_version report_id binding options context_sha256 source_projection_sha256 model_label limits ceiling_microusd')
  const wideAscii = v => typeof v === 'bigint' ? String(v) : Array.isArray(v) ? '[' + v.map(wideAscii).join(',') + ']' : v && typeof v === 'object' ? '{' + Object.keys(v).sort().map(k => ascii(k) + ':' + wideAscii(v[k])).join(',') + '}' : ascii(v)
  known.plan_sha256 = createHash('sha256').update(wideAscii(identity), 'ascii').digest('hex')
  const m = mount({ methods: { status: async () => known, clear: () => {} } })
  try { await recover(m, known); assert.ok(m.root.textContent.includes('9223372036854775807')); m.root.querySelector('.report-refresh').click(); await waitFor(() => m.root.querySelector('section').getAttribute('aria-busy') === 'false'); assert.ok(m.root.querySelector('.report-record')); assert.equal(m.root.querySelector('.report-start'), null) } finally { m.cleanup() }
})
test('validated Markdown stays inert, download URLs are owned and revoked on change/clear/unmount', async () => {
  const created = [], revoked = [], oldCreate = URL.createObjectURL, oldRevoke = URL.revokeObjectURL
  URL.createObjectURL = blob => { assert.ok(blob instanceof Blob); const url = 'blob:report-' + created.length; created.push(url); return url }; URL.revokeObjectURL = url => revoked.push(url)
  const known = done(), m = mount({ methods: { status: async () => known, read: async () => ({ schema_version: 1, report: known, content: prose }), download: async p => downloadReply(known, p), clear: () => {} } })
  try {
    await recover(m); m.root.querySelector('.report-read').click(); await waitFor(() => m.root.querySelector('.narrative')); assert.equal(m.root.querySelector('.narrative img,.narrative script,.narrative [onerror],.narrative a[href^="javascript:"]'), null); assert.ok(m.root.textContent.includes('中😀')); assert.ok(m.root.textContent.includes(connectedReportsCopy.en.interpretation))
    m.root.querySelector('.report-download').click(); await waitFor(() => m.root.querySelector('.report-save')); assert.equal(created.length, 1); assert.equal(m.root.querySelector('.report-save').download, 'full_report.md')
    const select = m.root.querySelector('.download-controls select'); select.value = 'native_evidence'; select.dispatchEvent(new dom.window.Event('change', { bubbles: true })); await nextTick(); assert.deepEqual(revoked, [created[0]]); assert.equal(m.root.querySelector('.report-save'), null)
    m.root.querySelector('.report-download').click(); await waitFor(() => m.root.querySelector('.literal-artifact')); assert.ok(m.root.querySelector('.literal-artifact').textContent.includes('{}')); m.cleanup(); assert.deepEqual(revoked, created)
  } finally { m.cleanup(); URL.createObjectURL = oldCreate; URL.revokeObjectURL = oldRevoke }
})
test('corrupt read/download never creates content or a Blob URL', async () => {
  const known = done(); let urls = 0, oldCreate = URL.createObjectURL
  URL.createObjectURL = () => { urls++; return 'blob:bad' }
  const m = mount({ methods: { status: async () => known, read: async () => ({ schema_version: 1, report: known, content: prose + 'tampered' }), download: async p => { const v = downloadReply(known, p); v.artifact.content_base64 += '\n'; return v }, clear: () => {} } })
  try { await recover(m); m.root.querySelector('.report-read').click(); await waitFor(() => m.root.textContent.includes(connectedReportsCopy.en.errors.invalid_reply)); assert.equal(m.root.querySelector('.narrative'), null); m.root.querySelector('.report-download').click(); await waitFor(() => m.root.querySelector('section').getAttribute('aria-busy') === 'false'); assert.equal(urls, 0); assert.equal(m.root.querySelector('.report-save'), null) } finally { m.cleanup(); URL.createObjectURL = oldCreate }
})
test('clear/disconnect/reset/selection/unmount fence late read replies and release protected state', async () => {
  for (const action of ['clear', 'disconnect', 'reset', 'selection', 'unmount']) {
    let finish, entered, cleared = 0; const begun = new Promise(r => { entered = r }), known = done()
    const m = mount({ methods: { status: async () => known, read: () => { entered(); return new Promise(r => { finish = () => r({ schema_version: 1, report: known, content: prose }) }) }, clear: () => { cleared++ } } })
    try {
      await recover(m); m.root.querySelector('.report-read').click(); await begun; await nextTick(); assert.equal(m.root.querySelector('.report-refresh').disabled, true)
      if (action === 'clear') m.root.querySelector('.report-clear').click(); else if (action === 'disconnect') m.props.connected = false; else if (action === 'reset') m.props.resetVersion++; else if (action === 'selection') m.props.selection = selection(); else m.cleanup()
      await nextTick(); const before = readValidations; finish(); await waitFor(() => readValidations > before && activeReadValidations.size === 0); assert.equal(m.root.querySelector('.narrative'), null); assert.ok(cleared > 0)
    } finally { m.cleanup() }
  }
})
test('owned download URL is revoked on clear, disconnect, reset and authorization failure', async () => {
  const oldCreate = URL.createObjectURL, oldRevoke = URL.revokeObjectURL
  try {
    for (const action of ['clear', 'disconnect', 'reset', 'authorization']) {
      const revoked = []; URL.createObjectURL = () => 'blob:owned-' + action; URL.revokeObjectURL = url => revoked.push(url)
      const known = done(); let denied = false
      const m = mount({ methods: { status: async () => { if (denied) throw { code: 'unauthorized' }; return known }, download: async p => downloadReply(known, p), clear: () => {} } })
      try {
        await recover(m); m.root.querySelector('.report-download').click(); await waitFor(() => m.root.querySelector('.report-save'))
        if (action === 'clear') m.root.querySelector('.report-clear').click(); else if (action === 'disconnect') m.props.connected = false; else if (action === 'reset') m.props.resetVersion++; else { denied = true; m.root.querySelector('.report-refresh').click() }
        await waitFor(() => !m.root.querySelector('.report-save')); assert.deepEqual(revoked, ['blob:owned-' + action])
      } finally { m.cleanup() }
    }
  } finally { URL.createObjectURL = oldCreate; URL.revokeObjectURL = oldRevoke }
})
test('cancellation intent and auth failure display honest recovery and clear protected records', async () => {
  const known = report(); let denied = false
  const m = mount({ methods: { status: async () => known, cancel: async () => { if (denied) throw { code: 'unauthorized' }; return { ...clone(known), cancel_requested: true } }, clear: () => {} } })
  try { await recover(m, known); m.root.querySelector('.report-cancel').click(); await waitFor(() => m.root.textContent.includes(connectedReportsCopy.en.cancellation)); assert.ok(m.root.textContent.includes(connectedReportsCopy.en.unknown)); assert.equal(m.root.querySelector('.report-cancel').disabled, true); m.root.querySelector('.report-clear').click(); await recover(m, known); denied = true; m.root.querySelector('.report-cancel').click(); await waitFor(() => !m.root.querySelector('.report-record')); assert.ok(m.root.textContent.includes(connectedReportsCopy.en.errors.unauthorized)); assert.equal(m.root.querySelector('#report-recovery-id').value, '') } finally { m.cleanup() }
})
