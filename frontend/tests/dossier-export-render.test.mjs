// Actual compiled Vue component regressions; execution and browser qualification belong to Main.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { JSDOM } from 'jsdom'
import { parse, compileScript } from '@vue/compiler-sfc'
import { copyFor } from '../src/i18n/workbench.js'
const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://127.0.0.1:5173/research' })
for (const name of ['window', 'document', 'Element', 'HTMLElement', 'SVGElement', 'Node']) globalThis[name] = name === 'window' ? dom.window : dom.window[name]
const { createApp, nextTick, h, reactive } = await import('vue')
const file = new URL('../src/components/workbench/DossierExport.vue', import.meta.url)
const { descriptor } = parse(readFileSync(file, 'utf8'), { filename: file.pathname })
const compiled = compileScript(descriptor, { id: 'dossier-export', inlineTemplate: true, genDefaultAs: '__component' })
let code = compiled.content.replace(/from (['"])vue\1/g, `from ${JSON.stringify(import.meta.resolve('vue'))}`)
code = code.replace(/from (['"])(\.{1,2}\/[^'"]+)\1/g, (_match, _quote, relative) => `from ${JSON.stringify(new URL(relative, file).href)}`)
const component = (await import(`data:text/javascript;base64,${Buffer.from(`${code}\nexport default __component`).toString('base64')}`)).default
const uuid = '11111111-1111-1111-1111-111111111111'
function dossier(markdown = '\uFEFF# 中😀\r\n<script>alert(1)</script> &#60;img&#62; https://evil.test') {
  const scope = { schema_version: 1, workspace_id: uuid, project_id: uuid, graph_id: uuid, run_id: null, branch_id: null, layer: 'source' }
  return { schema_version: 1, mode: 'model_free_evidence_dossier', request: { schema_version: 1, title: '../evil', display_graph_ids: ['graph_1'], sections: [{ heading: 'Evidence', query: '中', top_k: 10 }], valid_at: null, recorded_before: null }, sections: [{ ordinal: 1, heading: 'Evidence', source_claim_keys: [], simulation_observation_keys: [], other_claim_keys: [] }], claims: [], references: [], research_trace: [{ ordinal: 1, request_sha256: 'a'.repeat(64), response_sha256: 'b'.repeat(64), query: '中', top_k: 10, display_graph_ids: ['graph_1'], valid_at: null, recorded_before: null, scopes: [{ display_graph_id: 'graph_1', scope, pages: 1, scanned: 0, eligible: 0, excluded: 0, unknown: 0, returned: 0, truncated: false }], passage_coverage: [], competing_claim_candidates: [], linked_citations: 0, resolved_citations: 0, unavailable_citations: 0, historical: false, historical_semantics: 'retained_edges_not_bitemporal_reconstruction', rank_basis: 'lexical_token_overlap' }], summary: { section_count: 1, query_count: 1, distinct_scoped_facts: 0, reference_links: 0, resolved_references: 0, unavailable_references: 0, query_reference_links: 0, query_resolved_references: 0, query_unavailable_references: 0, scanned_per_query_sum: 0, unknown_per_query_sum: 0, truncated_query_scopes: 0, passage_coverage: [], coverage_label: 'retrieved_passage_union_per_retained_revision' }, input_sha256: 'a'.repeat(64), trace_sha256: 'b'.repeat(64), records_sha256: 'c'.repeat(64), model_generated: false, semantic_judge_used: false, claim_support_status: 'not_reviewed', consistency: 'individually_guarded_queries_not_atomic_snapshot', limitations: ['Lexical only'], markdown }
}
function harness(initial = {}) {
  const prior = { create: URL.createObjectURL, revoke: URL.revokeObjectURL, click: dom.window.HTMLAnchorElement.prototype.click, set: globalThis.setTimeout, clear: globalThis.clearTimeout, fetch: globalThis.fetch }
  const blobs = [], revoked = [], clicks = [], timers = new Map(); let fetches = 0, failCreate = false, failClick = false
  URL.createObjectURL = blob => { if (failCreate) throw new Error('private creation secret'); blobs.push(blob); return `blob:owned-${blobs.length}` }
  URL.revokeObjectURL = url => revoked.push(url)
  dom.window.HTMLAnchorElement.prototype.click = function () { clicks.push({ href: this.href, filename: this.download, connected: this.isConnected }); if (failClick) throw new Error('private click secret') }
  // Capture only the component's documented one-second URL cleanup. Blob,
  // Node and framework timers retain their real scheduling and cancellation.
  globalThis.setTimeout = (callback, delay, ...args) => {
    if (delay !== 1000) return prior.set(callback, delay, ...args)
    const handle = Symbol('dossier-url-cleanup')
    timers.set(handle, () => callback(...args))
    return handle
  }
  globalThis.clearTimeout = handle => {
    if (timers.has(handle)) timers.delete(handle)
    else prior.clear(handle)
  }
  globalThis.fetch = () => { fetches++; throw new Error('unexpected network') }
  const props = reactive({ result: dossier(), locale: 'en', ...initial }), root = document.createElement('div'); document.body.append(root)
  const app = createApp({ setup: () => () => h(component, props) }); app.mount(root)
  let closed = false
  return { root, props, blobs, revoked, clicks, timers, get fetches() { return fetches }, failCreate() { failCreate = true }, failClick() { failClick = true }, runCleanup() { for (const callback of [...timers.values()]) callback() }, unmount() { if (!closed) { app.unmount(); root.remove(); closed = true } }, cleanup() { this.unmount(); URL.createObjectURL = prior.create; URL.revokeObjectURL = prior.revoke; dom.window.HTMLAnchorElement.prototype.click = prior.click; globalThis.setTimeout = prior.set; globalThis.clearTimeout = prior.clear; globalThis.fetch = prior.fetch } }
}
async function settle() { await nextTick(); await nextTick() }
test('explicit native buttons download literal bytes only; locale changes preserve bytes and create no calls', async () => {
  const m = harness()
  try {
    assert.equal(m.blobs.length, 0); assert.equal(m.clicks.length, 0); assert.equal(m.fetches, 0)
    assert.equal(m.root.querySelector('script,img,a'), null)
    const button = m.root.querySelector('button'); button.focus(); assert.equal(document.activeElement, button)
    // The native browser's Enter/Space activation produces click; jsdom does not synthesize it.
    assert.equal(button.type, 'button'); button.click(); await settle()
    assert.deepEqual(m.clicks[0], { href: 'blob:owned-1', filename: 'nexaweave-evidence-dossier.md', connected: true })
    assert.equal(m.blobs[0].type, 'text/markdown;charset=utf-8')
    assert.deepEqual(new Uint8Array(await m.blobs[0].arrayBuffer()), new TextEncoder().encode(m.props.result.markdown))
    assert.equal(document.querySelector('a'), null); assert.equal(document.activeElement, button)
    assert.equal(m.root.querySelector('[role="status"]').textContent, copyFor('en').exports.requested)
    for (const locale of ['zh', 'ms']) {
      m.props.locale = locale; await settle()
      assert.equal(m.root.querySelector('button').textContent, copyFor(locale).exports.markdown)
      assert.equal(m.root.querySelector('[role="status"]').textContent, copyFor(locale).exports.requested)
      assert.equal(m.blobs.length, 1); assert.equal(m.fetches, 0)
    }
    m.root.querySelector('button').click(); await settle()
    assert.deepEqual(m.revoked, ['blob:owned-1']); assert.equal(m.timers.size, 1)
    assert.deepEqual(new Uint8Array(await m.blobs[1].arrayBuffer()), new Uint8Array(await m.blobs[0].arrayBuffer()))
    m.root.querySelectorAll('button')[1].click(); await settle()
    assert.equal(m.blobs[2].type, 'application/json;charset=utf-8'); assert.equal(m.clicks[2].filename, 'nexaweave-evidence-dossier.json')
    assert.deepEqual(JSON.parse(await m.blobs[2].text()), JSON.parse(JSON.stringify(m.props.result)))
    m.runCleanup(); assert.equal(m.timers.size, 0); assert.deepEqual(m.revoked, ['blob:owned-1', 'blob:owned-2', 'blob:owned-3'])
    assert.equal(m.clicks.length, 3); assert.equal(m.fetches, 0)
  } finally { m.cleanup() }
})
test('replacement, reset and unmount revoke current URL, cancel timers and clear protected feedback', async () => {
  const m = harness()
  try {
    m.root.querySelector('button').click(); await settle()
    m.props.result = dossier('Replacement'); await settle()
    assert.deepEqual(m.revoked, ['blob:owned-1']); assert.equal(m.timers.size, 0)
    assert.equal(m.root.querySelector('[role="status"]').textContent, ''); assert.equal(m.clicks.length, 1)
    m.root.querySelector('button').click(); await settle(); m.props.result = null; await settle()
    assert.equal(m.root.querySelector('button'), null); assert.equal(m.timers.size, 0); assert.equal(m.revoked.at(-1), 'blob:owned-2')
    m.props.result = dossier(); await settle(); assert.equal(m.root.querySelector('[role="status"]').textContent, '')
    m.root.querySelector('button').click(); await settle(); m.unmount()
    assert.equal(m.timers.size, 0); assert.equal(m.revoked.at(-1), 'blob:owned-3'); assert.equal(m.fetches, 0)
    m.runCleanup(); assert.equal(m.clicks.length, 3)
  } finally { m.cleanup() }
})
test('failed URL creation or click cleans anchors and resources with fixed safe feedback', async () => {
  for (const failure of ['failCreate', 'failClick']) {
    const m = harness()
    try {
      m[failure](); m.root.querySelector('button').click(); await settle()
      assert.equal(m.root.querySelector('[role="status"]').textContent, copyFor('en').exports.failed)
      assert.equal(document.querySelector('a'), null); assert.equal(m.timers.size, 0)
      assert.equal(m.revoked.length, failure === 'failClick' ? 1 : 0)
      assert.ok(!m.root.textContent.includes('secret')); assert.equal(m.fetches, 0)
    } finally { m.cleanup() }
  }
})
test('invalid or oversized dossier produces no blob; research-only state has no export actions', async () => {
  const m = harness({ result: dossier('😀'.repeat(1048577)) })
  try {
    m.root.querySelector('button').click(); await settle()
    assert.equal(m.root.querySelector('[role="status"]').textContent, copyFor('en').exports.tooLarge)
    m.props.result = { ...dossier(), model_generated: true }; await settle(); m.root.querySelector('button').click(); await settle()
    assert.equal(m.root.querySelector('[role="status"]').textContent, copyFor('en').exports.invalid)
    m.props.result = { mode: 'research' }; await settle()
    assert.equal(m.root.querySelector('button'), null); assert.equal(m.blobs.length, 0); assert.equal(m.clicks.length, 0); assert.equal(m.fetches, 0)
  } finally { m.cleanup() }
})
test('localized export keys are complete', () => {
  for (const locale of ['zh', 'ms']) assert.deepEqual(Object.keys(copyFor(locale).exports).sort(), Object.keys(copyFor('en').exports).sort())
})
