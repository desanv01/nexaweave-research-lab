// Actual mounted follow-up panel; Main performs execution.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createHash, webcrypto } from 'node:crypto'
import { JSDOM } from 'jsdom'
import { parse, compileScript } from '@vue/compiler-sfc'
import { connectedFollowupsCopyFor } from '../src/i18n/connectedFollowups.js'
globalThis.crypto ||= webcrypto
const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://127.0.0.1:5173/research' })
for (const name of ['window', 'document', 'Element', 'HTMLElement', 'SVGElement', 'Node']) globalThis[name] = name === 'window' ? dom.window : dom.window[name]
const { createApp, h, nextTick, reactive } = await import('vue')
const file = new URL('../src/components/workbench/ConnectedFollowup.vue', import.meta.url)
const { descriptor } = parse(readFileSync(file, 'utf8'), { filename: file.pathname })
const compiled = compileScript(descriptor, { id: 'connected-followup', inlineTemplate: true, genDefaultAs: '__component' })
let source = compiled.content.replace(/from (['"])vue\1/g, `from ${JSON.stringify(import.meta.resolve('vue'))}`)
source = source.replace(/from (['"])(\.{1,2}\/[^'"]+)\1/g, (_m, _q, relative) => `from ${JSON.stringify(new URL(relative, file).href)}`)
const component = (await import(`data:text/javascript;base64,${Buffer.from(source + '\nexport default __component').toString('base64')}`)).default
const uid = n => `00000000-0000-0000-0000-${String(n).padStart(12, '0')}`, hash = 'a'.repeat(64)
const clone = v => JSON.parse(JSON.stringify(v))
function ascii(v) { if (Array.isArray(v)) return '[' + v.map(ascii).join(',') + ']'; if (v && typeof v === 'object') return '{' + Object.keys(v).sort().map(k => ascii(k) + ':' + ascii(v[k])).join(',') + '}'; return JSON.stringify(v).replace(/[\u007f-\uffff]/g, c => '\\u' + c.charCodeAt(0).toString(16).padStart(4, '0')) }
const digest = v => createHash('sha256').update(ascii(v), 'ascii').digest('hex'), bytesHash = v => createHash('sha256').update(v).digest('hex')
const pick = (v, keys) => Object.fromEntries(keys.split(' ').map(k => [k, v[k]]))
const answer = 'Source [[source:' + uid(30) + ']] and native [[native:twitter:0:' + hash + ']]. <script>inert 中😀</script>'
function planned(turnId = uid(40), parent = null) {
  const scope = { schema_version: 1, workspace_id: uid(1), project_id: uid(2), graph_id: uid(3), run_id: null, branch_id: null, layer: 'source' }
  const b = { display_graph_id: 'graph_1', principal: 'local-research', scope, report: { report_id: uid(20), plan_sha256: hash, receipt_sha256: hash, manifest_sha256: hash, full_report_sha256: hash }, native_binding: { display_graph_id: 'graph_1', principal: 'local-research', scope, project_revision: 1, source: { source_revision: uid(5), source_name: 'Source', source_sha256: hash }, preparation: { operation_id: uid(9), plan_sha256: hash, simulation_id: 'sim_' + uid(9).replaceAll('-', ''), artifact_sha256: hash }, native: { run_id: uid(10), launch_sha256: hash, request_fingerprint: hash, evidence_sha256: hash, platforms: ['twitter'] }, coverage: [{ platform: 'twitter', total_records: 1, selected_records: 1, complete: true, windows: [{ offset: 0, count: 1 }] }], reference_keys: ['source:' + uid(30), 'native:twitter:0:' + hash] } }
  if (parent) {
    b.native_binding = clone(parent.binding)
    b.report = { report_id: parent.report_id, plan_sha256: parent.plan_sha256, receipt_sha256: parent.receipt_sha256, manifest_sha256: parent.receipt.manifest_sha256, full_report_sha256: parent.manifest.files.find(f => f.name === 'full_report.md').sha256 }
  }
  const head = digest({ schema_version: 1, report_id: b.report.report_id, report_plan_sha256: b.report.plan_sha256, turns: [] })
  const v = { schema_version: 1, turn_id: turnId, plan_sha256: null, binding: b, options: { question: 'Why 中😀?', output_language: 'en', expected_history_sha256: null }, history: { head_sha256: head, total_completed: 0, window_start: 1, pairs: [] }, report_context: { file_sha256: b.report.full_report_sha256, prefix_sha256: hash, prefix_characters: 100, total_characters: 100, truncated: false }, context_sha256: hash, source_projection_sha256: hash, model_label: 'scripted', limits: { max_calls: 8, max_input_bytes: 1048576, max_output_tokens: 4096, max_run_seconds: 600 }, ceiling_microusd: 12345, authorization: { model_calls_enabled: true, budget_configured: true }, state: 'planned', progress: { stage: 'planned', percent: 0, completed_sections: 0, total_sections: 0 }, workflow: null, receipt: null, receipt_sha256: null, manifest: null, published_history_head_sha256: null, cleanup: { known: false, pending: null, owner_thread_alive: null }, cancel_requested: false, error_code: null }
  v.plan_sha256 = digest(pick(v, 'schema_version turn_id binding options history report_context context_sha256 source_projection_sha256 model_label limits ceiling_microusd'))
  return v
}
function complete(turnId = uid(40), parent = null) {
  const v = planned(turnId, parent), r = v.binding.report
  const conversation = { schema_version: 1, report_id: r.report_id, report_plan_sha256: r.plan_sha256, total_completed: 1, pairs: [{ turn_id: v.turn_id, plan_sha256: v.plan_sha256, ordinal: 1, question: v.options.question, answer, answer_sha256: bytesHash(answer), predecessor_head_sha256: v.history.head_sha256, receipt_sha256: null, published_head_sha256: null }] }
  v.manifest = { schema_version: 1, files: Object.entries({ 'turn.json': '{}', 'answer.md': answer, 'conversation.json': JSON.stringify(conversation), 'retrieval_evidence.json': '{}', 'native_evidence.json': '{}', 'tool_trace.json': '{}' }).map(([name, content]) => ({ name, size: Buffer.byteLength(content), sha256: bytesHash(content) })) }
  v.state = 'completed'; v.progress = { stage: 'completed', percent: 100, completed_sections: 1, total_sections: 1 }; v.cleanup = { known: true, pending: false, owner_thread_alive: false }
  v.receipt = { schema_version: 1, turn_id: v.turn_id, plan_sha256: v.plan_sha256, parent_report_id: r.report_id, parent_report_plan_sha256: r.plan_sha256, parent_report_receipt_sha256: r.receipt_sha256, context_sha256: v.context_sha256, history_head_sha256: v.history.head_sha256, ordinal: 1, manifest_sha256: digest(v.manifest), output_language: 'en', reference_integrity: 'validated', semantic_support_status: 'not_reviewed' }
  v.receipt_sha256 = digest(v.receipt)
  v.published_history_head_sha256 = digest({ schema_version: 1, report_id: r.report_id, report_plan_sha256: r.plan_sha256, predecessor_head_sha256: v.history.head_sha256, ordinal: 1, turn_id: v.turn_id, plan_sha256: v.plan_sha256, question_sha256: bytesHash(v.options.question), answer_sha256: bytesHash(answer), receipt_sha256: v.receipt_sha256 })
  return v
}
function mount(initial = {}) {
  const props = reactive({ methods: { status: async () => planned(), clear: () => {} }, selection: null, connected: true, busy: false, displayGraphId: 'graph_1', resetVersion: 0, locale: 'en', ...initial })
  const root = document.createElement('div'); document.body.append(root); const app = createApp({ setup: () => () => h(component, props) }); app.mount(root); let mounted = true
  return { props, root, cleanup() { if (mounted) { mounted = false; app.unmount(); root.remove() } } }
}
async function waitFor(predicate) { const end = Date.now() + 2000; do { await nextTick(); if (predicate()) return; await new Promise(r => setImmediate(r)) } while (Date.now() < end); assert.ok(predicate(), 'bounded mounted condition did not complete') }
function input(m, selector, value) { const e = m.root.querySelector(selector); e.value = value; e.dispatchEvent(new dom.window.Event(e.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true })) }
async function recover(m, value) { input(m, '#followup-recovery-id', value.turn_id); input(m, '#followup-recovery-hash', value.plan_sha256); m.root.querySelector('.recovery').dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true })); await waitFor(() => m.root.querySelector('.turn-record')) }

test('manual older-turn recovery is selection-free and never exposes Start', async () => {
  const known = planned(), m = mount({ methods: { status: async () => known, clear: () => {} } })
  try { await recover(m, known); assert.equal(m.root.querySelector('.followup-start'), null); assert.ok(m.root.textContent.includes(known.history.head_sha256)); assert.ok(m.root.textContent.includes(connectedFollowupsCopyFor('en').recovered)); for (const locale of ['en', 'zh', 'ms']) { m.props.locale = locale; await nextTick(); assert.ok(m.root.textContent.includes(connectedFollowupsCopyFor(locale).title)) } } finally { m.cleanup() }
})
test('completed answer stays inert and owned Blob URL is revoked on clear and disconnect', async () => {
  const known = complete(), created = [], revoked = [], oldCreate = URL.createObjectURL, oldRevoke = URL.revokeObjectURL
  URL.createObjectURL = () => { const value = `blob:followup-${created.length}`; created.push(value); return value }; URL.revokeObjectURL = value => revoked.push(value)
  const file = known.manifest.files[1], download = { schema_version: 1, turn_id: known.turn_id, plan_sha256: known.plan_sha256, receipt_sha256: known.receipt_sha256, artifact: { ...file, mime: 'text/markdown', content_base64: Buffer.from(answer).toString('base64') } }
  const m = mount({ methods: { status: async () => known, read: async () => ({ schema_version: 1, turn: known, content: answer }), download: async () => download, clear: () => {} } })
  try {
    await recover(m, known); m.root.querySelector('.followup-read').click(); await waitFor(() => m.root.querySelector('.answer'))
    assert.equal(m.root.querySelector('.answer script,.answer img,.answer [onerror]'), null)
    m.root.querySelector('.followup-download').click(); await waitFor(() => m.root.querySelector('.followup-save'))
    m.root.querySelector('.followup-clear').click(); await nextTick(); assert.deepEqual(revoked, created); assert.equal(m.root.querySelector('.answer,.followup-save'), null)
    await recover(m, known); m.root.querySelector('.followup-download').click(); await waitFor(() => m.root.querySelector('.followup-save')); m.props.connected = false; await nextTick(); assert.deepEqual(revoked, created)
  } finally { m.cleanup(); URL.createObjectURL = oldCreate; URL.revokeObjectURL = oldRevoke }
})
test('late status reply after clear or unmount cannot restore protected state', async () => {
  for (const action of ['clear', 'unmount']) {
    let release, entered; const begun = new Promise(r => { entered = r }), known = planned()
    const m = mount({ methods: { status: () => { entered(); return new Promise(r => { release = () => r(known) }) }, clear: () => {} } })
    input(m, '#followup-recovery-id', known.turn_id); input(m, '#followup-recovery-hash', known.plan_sha256); m.root.querySelector('.recovery').dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true })); await begun
    if (action === 'clear') m.root.querySelector('.followup-clear').click(); else m.cleanup()
    release(); await nextTick(); assert.equal(m.root.querySelector('.turn-record'), null); m.cleanup()
  }
})

function completedParent() {
  const v = { schema_version: 1, report_id: uid(20), plan_sha256: null, binding: planned().binding.native_binding,
    options: { requirement: 'Compare evidence', output_language: 'en', native_windows: null }, context_sha256: hash, source_projection_sha256: hash,
    model_label: 'scripted', limits: { max_calls: 64, max_input_bytes: 262144, max_output_tokens: 4096, max_run_seconds: 600 }, ceiling_microusd: 12345,
    authorization: { model_calls_enabled: false, budget_configured: true }, state: 'completed', progress: { stage: 'completed', percent: 100, completed_sections: 1, total_sections: 1 }, workflow: null,
    receipt: null, receipt_sha256: null, manifest: { schema_version: 1, files: ['meta.json', 'outline.json', 'full_report.md', 'retrieval_evidence.json', 'native_evidence.json', 'section_01.md'].map(name => ({ name, size: Buffer.byteLength(answer), sha256: bytesHash(answer) })) },
    cleanup: { known: true, pending: false, owner_thread_alive: false }, cancel_requested: false, error_code: null }
  v.plan_sha256 = digest(pick(v, 'schema_version report_id binding options context_sha256 source_projection_sha256 model_label limits ceiling_microusd'))
  v.receipt = { schema_version: 1, report_id: v.report_id, plan_sha256: v.plan_sha256, context_sha256: hash, manifest_sha256: digest(v.manifest), output_language: 'en', reference_integrity: 'validated', semantic_support_status: 'not_reviewed' }
  v.receipt_sha256 = digest(v.receipt); return v
}

test('100 retained turns report localized busy without Plan and retain generation-disabled reads', async () => {
  const parent = completedParent(), first = complete(uid(100), parent), turns = new Map()
  first.authorization.model_calls_enabled = false; turns.set(first.turn_id, first)
  for (let i = 1; i < 100; i++) { const value = planned(uid(100 + i), parent); turns.set(value.turn_id, value) }
  let planCalls = 0, readCalls = 0
  const m = mount({ selection: parent, methods: {
    clear: () => {}, status: async payload => turns.get(payload.turn_id),
    plan: async () => { planCalls++; throw new Error('Capacity must reject before Plan') },
    read: async () => { readCalls++; return { schema_version: 1, turn: first, content: answer } }
  } })
  try {
    await waitFor(() => !m.root.querySelector('fieldset').disabled)
    let count = 0
    for (const value of turns.values()) {
      input(m, '#followup-recovery-id', value.turn_id); input(m, '#followup-recovery-hash', value.plan_sha256)
      m.root.querySelector('.recovery').dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true }))
      count++; await waitFor(() => m.root.querySelectorAll('.turn-record').length === count && !m.root.querySelector('.followup-review').disabled && !m.root.querySelector('fieldset').disabled)
    }
    input(m, '#followup-question', 'Another question')
    for (const locale of ['en', 'zh', 'ms']) {
      m.props.locale = locale; await nextTick()
      m.root.querySelector('form').dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true }))
      await waitFor(() => m.root.querySelector('#followup-feedback').textContent.includes(connectedFollowupsCopyFor(locale).errors.busy))
      assert.equal(document.activeElement, m.root.querySelector('#followup-feedback'))
      assert.equal(planCalls, 0); assert.equal(m.root.querySelectorAll('.turn-record').length, 100)
    }
    m.root.querySelector(`[data-turn-id="${first.turn_id}"] .followup-read`).click()
    await waitFor(() => m.root.querySelector('.answer')); assert.equal(readCalls, 1); assert.equal(m.root.querySelector('.followup-start'), null)
  } finally { m.cleanup() }
})
