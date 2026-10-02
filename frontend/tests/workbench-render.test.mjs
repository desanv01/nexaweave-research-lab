// Compile and mount the actual SFC templates with the locked Vue/jsdom tooling.
// No filename/CSS snapshot is treated as browser qualification.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { webcrypto } from 'node:crypto'
import { JSDOM } from 'jsdom'
import { parse, compileScript } from '@vue/compiler-sfc'
import { createWorkbenchClient } from '../src/api/workbench.js'
import { sha256 } from '../src/api/sourceLibrary.js'
import { ingestionFingerprint, ingestionIdentities } from '../src/api/sourceIngestion.js'
import { copyFor, safeError, workbenchCopy } from '../src/i18n/workbench.js'
const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://127.0.0.1:5173/research' })
Object.defineProperty(globalThis, 'crypto', { value: webcrypto, configurable: true })
for (const name of ['window', 'document', 'Element', 'HTMLElement', 'SVGElement', 'Node']) globalThis[name] = name === 'window' ? dom.window : dom.window[name]
const { createApp, nextTick, h } = await import('vue')
async function compile(path, replacements = {}) {
  const file = new URL(path, import.meta.url)
  const { descriptor } = parse(readFileSync(file, 'utf8'), { filename: file.pathname })
  const compiled = compileScript(descriptor, { id: path, inlineTemplate: true, genDefaultAs: '__component' })
  let code = compiled.content.replace(/from (['"])vue\1/g, `from ${JSON.stringify(import.meta.resolve('vue'))}`)
  code = code.replace(/from (['"])(\.{1,2}\/[^'"]+)\1/g, (_match, _quote, relative) => `from ${JSON.stringify(replacements[relative] || new URL(relative, file).href)}`)
  const url = `data:text/javascript;base64,${Buffer.from(`${code}\nexport default __component`).toString('base64')}`
  return { component: (await import(url)).default, url }
}
const evidence = await compile('../src/components/workbench/EvidenceResults.vue')
const sources = await compile('../src/components/workbench/SourceLibrary.vue')
const ingestion = await compile('../src/components/workbench/SourceIngestion.vue')
const workbench = await compile('../src/views/ResearchWorkbench.vue', { '../components/workbench/EvidenceResults.vue': evidence.url, '../components/workbench/SourceLibrary.vue': sources.url, '../components/workbench/SourceIngestion.vue': ingestion.url })
function mount(component, props) {
  const root = document.createElement('div'); document.body.append(root)
  const app = createApp(component, props)
  app.component('RouterLink', { props: ['to'], setup: (p, { slots }) => () => h('a', { href: p.to }, slots.default?.()) })
  app.mount(root)
  return { root, app, cleanup() { app.unmount(); root.remove() } }
}
async function settle() { for (let i = 0; i < 12; i++) { await Promise.resolve(); await nextTick() } }
function input(root, selector, value) {
  const element = root.querySelector(selector); element.value = value
  element.dispatchEvent(new dom.window.Event(element.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true }))
}
const uuid = '11111111-1111-1111-1111-111111111111'
const scope = { schema_version: 1, workspace_id: uuid, project_id: uuid, graph_id: uuid, run_id: null, branch_id: null, layer: 'source' }
const malicious = '<img src=x onerror=alert(1)> 中😀 https://evil.test'
test('real connected ingestion component is initially read-only and manual recovery is disabled until connection', async () => {
  const mounted = mount(workbench.component)
  try {
    assert.ok(mounted.root.textContent.includes(copyFor('en').ingestion.title))
    assert.ok(mounted.root.querySelector('.source-ingestion fieldset').disabled)
    assert.ok(mounted.root.querySelector('#ingestion-operation').disabled)
    assert.equal(mounted.root.querySelector('.plan-review'), null)
    assert.equal(mounted.root.querySelector('.attempt'), null)
  } finally { mounted.cleanup() }
})
function researchResult() {
  const citation = { evidence_id: uuid, project_id: uuid, source_revision: uuid, source_name: '<script>name</script>', source_sha256: 'a'.repeat(64), source_byte_length: 1000, source_codepoint_length: 200, source_recorded_at: '2026-10-01T00:00:00Z', start: 3, end: 3 + [...malicious].length, offset_unit: 'unicode_codepoint', excerpt: malicious, excerpt_sha256: 'b'.repeat(64), declared_page: 2 }
  return { schema_version: 1, source_claims: [{ provider_id: 'edge', kind: 'edge', scope, claim_class: 'source', name: 'predicate', fact: malicious, source_node_id: 'a', target_node_id: 'b', episode_ids: [], evidence_ids: [uuid], created_at: null, valid_at: null, invalid_at: null, expired_at: null, rank_basis: 'lexical_token_overlap', overlap_tokens: 1, query_tokens: 2, citations: [citation], unavailable_evidence_ids: [] }], simulation_observations: [], other_claims: [], scopes: [{ display_graph_id: 'graph_1', scope, pages: 1, scanned: 1, eligible: 1, excluded: 0, unknown: 0, returned: 1, truncated: false }], passage_coverage: [], competing_claim_candidates: [], linked_citations: 1, resolved_citations: 1, unavailable_citations: 0, historical: false, historical_semantics: 'retained_edges_not_bitemporal_reconstruction', rank_basis: 'lexical_token_overlap' }
}
const graphResult = { graph_id: 'graph_1', nodes: [], edges: [], node_count: 0, edge_count: 0 }
const response = data => new Response(JSON.stringify({ success: true, data }), { headers: { 'Content-Type': 'application/json' } })
test('real request-derived result is text; keyboard-operable citation selection and focus return', async () => {
  let calls = 0
  const client = createWorkbenchClient({ fetchImpl: async () => response(++calls === 1 ? graphResult : researchResult()) })
  await client.connect({ origin: 'http://127.0.0.1:5001', graph: 'graph_1', token: 'secret' })
  const result = await client.research({ display_graph_ids: ['graph_1'], text: '中😀' })
  const mounted = mount(evidence.component, { result, locale: 'en' })
  try {
    assert.ok(mounted.root.textContent.includes(malicious))
    assert.equal(mounted.root.querySelector('img,script,a'), null)
    const button = mounted.root.querySelector('.references button'); button.focus()
    // Native button activation is the browser's keyboard contract; dispatch its
    // resulting click here and check the actual Vue handler/focus behavior.
    button.click(); await settle()
    assert.equal(button.getAttribute('aria-expanded'), 'true')
    const inspector = mounted.root.querySelector('#passage-inspector')
    assert.equal(document.activeElement, inspector)
    assert.equal(inspector.querySelector('.excerpt').textContent, malicious)
    assert.ok(inspector.textContent.includes('unicode_codepoint'))
    assert.ok(inspector.textContent.includes('Source revision'))
    assert.equal(inspector.querySelector('img,script,a'), null)
    inspector.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); await settle()
    assert.equal(document.activeElement, button); assert.equal(button.getAttribute('aria-expanded'), 'false')
  } finally { mounted.cleanup(); client.disconnect() }
})
test('localized unavailable passage has no fabricated excerpt', async () => {
  const result = researchResult(); result.source_claims[0].citations = []; result.source_claims[0].unavailable_evidence_ids = [uuid]; result.resolved_citations = 0; result.unavailable_citations = 1
  for (const locale of ['zh', 'ms']) {
    const mounted = mount(evidence.component, { result, locale })
    try {
      mounted.root.querySelector('.references button').click(); await settle()
      assert.ok(mounted.root.querySelector('#passage-inspector').textContent.includes(copyFor(locale).unavailable))
      assert.equal(mounted.root.querySelector('.excerpt'), null)
      assert.ok(mounted.root.textContent.includes(copyFor(locale).limitations))
    } finally { mounted.cleanup() }
  }
})
test('all route copy keys are present in English, Chinese and Malay; unknown errors are fixed', () => {
  const keys = object => Object.keys(object).sort()
  for (const locale of ['zh', 'ms']) {
    assert.deepEqual(keys(workbenchCopy[locale]), keys(workbenchCopy.en))
    assert.deepEqual(keys(workbenchCopy[locale].status), keys(workbenchCopy.en.status))
    assert.deepEqual(keys(workbenchCopy[locale].errors), keys(workbenchCopy.en.errors))
    assert.deepEqual(keys(workbenchCopy[locale].ingestionBanner), keys(workbenchCopy.en.ingestionBanner))
    assert.equal(safeError(copyFor(locale), 'private traceback secret'), copyFor(locale).errors.unavailable)
  }
})
test('actual route connects, renders fetched facts, changes local language and clears protected state on disconnect', async () => {
  const previousFetch = globalThis.fetch, calls = []
  globalThis.fetch = async (url, options) => { calls.push({ url, options }); return response(calls.length === 1 ? graphResult : researchResult()) }
  const mounted = mount(workbench.component)
  try {
    assert.equal(mounted.root.querySelectorAll('.connection [role="status"]').length, 1)
    assert.equal(mounted.root.querySelectorAll('.source-ingestion [role="status"]').length, 1)
    assert.ok(mounted.root.textContent.includes(copyFor('en').status.disconnected))
    assert.ok(mounted.root.querySelector('.query-section fieldset').disabled)
    input(mounted.root, '#graph-id', 'graph_1'); input(mounted.root, '#bearer-token', 'test-token')
    mounted.root.querySelector('.connection-form').dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true })); await settle()
    assert.equal(calls[0].url, 'http://127.0.0.1:5001/api/graph/data/graph_1')
    assert.equal(mounted.root.querySelector('#bearer-token').value, '')
    assert.equal(mounted.root.querySelector('.query-section fieldset').disabled, false)
    input(mounted.root, '#question', '中😀')
    mounted.root.querySelector('.query-section form').dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true })); await settle()
    assert.equal(JSON.parse(calls[1].options.body).text, '中😀')
    assert.ok(mounted.root.querySelector('.claim-text').textContent.includes(malicious))
    input(mounted.root, '#workbench-language', 'ms'); await settle()
    assert.ok(mounted.root.textContent.includes(copyFor('ms').status.success))
    const disconnect = [...mounted.root.querySelectorAll('button')].find(b => b.textContent === copyFor('ms').disconnect)
    disconnect.click(); await settle()
    assert.equal(mounted.root.querySelector('.claim-text'), null)
    assert.equal(mounted.root.querySelector('.graph-overview'), null)
    assert.equal(mounted.root.querySelector('#bearer-token').value, '')
    assert.ok(mounted.root.querySelector('.query-section fieldset').disabled)
    assert.equal(mounted.root.querySelector('img,script'), null)
  } finally { mounted.cleanup(); globalThis.fetch = previousFetch }
})
test('route unmount cancels the owned request and its eventual result cannot replace disconnected state', async () => {
  const previousFetch = globalThis.fetch; let signal, resolve
  globalThis.fetch = async (_url, options) => { signal = options.signal; return new Promise(r => { resolve = r }) }
  const mounted = mount(workbench.component)
  try {
    input(mounted.root, '#graph-id', 'graph_1'); input(mounted.root, '#bearer-token', 'test-token')
    mounted.root.querySelector('.connection-form').dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true })); await settle()
    mounted.cleanup(); assert.equal(signal.aborted, true)
    resolve(response(graphResult)); await settle()
  } finally { globalThis.fetch = previousFetch }
})
test('actual route clears protected results on authorization failure and keeps error text localized', async () => {
  const previousFetch = globalThis.fetch; let count = 0
  globalThis.fetch = async () => ++count === 1 ? response(graphResult) : new Response('private stack token', { status: 401 })
  const mounted = mount(workbench.component)
  try {
    input(mounted.root, '#graph-id', 'graph_1'); input(mounted.root, '#bearer-token', 'test-token')
    mounted.root.querySelector('.connection-form').dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true })); await settle()
    input(mounted.root, '#question', 'question')
    mounted.root.querySelector('.query-section form').dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true })); await settle()
    assert.equal(mounted.root.querySelector('.graph-overview'), null)
    assert.equal(mounted.root.querySelector('#bearer-token').value, '')
    assert.ok(mounted.root.querySelector('.query-section fieldset').disabled)
    input(mounted.root, '#workbench-language', 'zh'); await settle()
    assert.ok(mounted.root.querySelector('[role="status"]').textContent.includes(copyFor('zh').errors.unauthorized))
    assert.ok(!mounted.root.textContent.includes('private stack token'))
  } finally { mounted.cleanup(); globalThis.fetch = previousFetch }
})
test('dossier sections and traces remain in declared order, including empty sections', async () => {
  const requestSections = [{ heading: 'First 中文', query: 'Q1' }, { heading: 'Second Melayu', query: 'Q2' }]
  const result = { mode: 'model_free_evidence_dossier', sections: requestSections.map((s, i) => ({ ordinal: i + 1, heading: s.heading, source_claim_keys: [], simulation_observation_keys: [], other_claim_keys: [] })), claims: [], references: [], summary: { reference_links: 0, resolved_references: 0, unavailable_references: 0, passage_coverage: [] }, research_trace: requestSections.map((s, i) => ({ ordinal: i + 1, query: s.query, request_sha256: 'a'.repeat(64), response_sha256: 'b'.repeat(64), valid_at: null, recorded_before: null, scopes: [], competing_claim_candidates: [] })) }
  const mounted = mount(evidence.component, { result, locale: 'en' })
  try {
    assert.deepEqual([...mounted.root.querySelectorAll('.result-main > ol h3')].map(e => e.textContent), ['First 中文', 'Second Melayu'])
    assert.deepEqual([...mounted.root.querySelectorAll('.trace > .claim-text')].map(e => e.textContent), ['Q1', 'Q2'])
    assert.ok(mounted.root.textContent.includes(copyFor('en').empty))
  } finally { mounted.cleanup() }
})
async function waitForRoute(predicate) {
  for (let i = 0; i < 100; i++) { await settle(); if (predicate()) return; await new Promise(resolve => setTimeout(resolve, 5)) }
  assert.fail('Expected route state did not arrive')
}
const clickText = (root, text) => [...root.querySelectorAll('button')].find(button => button.textContent === text)?.click()
const rejectResponse = (code, status) => new Response(JSON.stringify({ success: false, error: { code } }), { status, headers: { 'Content-Type': 'application/json' } })
test('shared route ingestion banners never encourage resubmission after denial or uncertain execution; safe status and auth clearing remain', async () => {
  for (const [executeCode, executeStatus] of [['model_calls_disabled', 403], ['outcome_unknown', 503]]) {
    const previousFetch = globalThis.fetch, calls = [], text = 'Exact 中😀 source\r\n', bytes = new TextEncoder().encode(text)
    const source = { project_id: uuid, source_revision: uuid, source_name: 'Synthetic source', text_sha256: await sha256(bytes), byte_length: bytes.length, codepoint_length: Array.from(text).length, recorded_at: '2026-10-03T00:00:00.123456+00:00' }
    const sourceDTO = { schema_version: 1, binary_retained: false, graph_ingestion_executed: false, source, text, offset_unit: 'unicode_codepoint', passages: [{ evidence_id: uuid, start: 0, end: source.codepoint_length, page: null, excerpt_sha256: source.text_sha256 }] }
    let planned, statusMode = 'missing', planFailure = true
    globalThis.fetch = async (url, options) => {
      calls.push({ url, method: options.method })
      if (url.includes('/api/graph/data/')) return response(graphResult)
      if (url.includes('/api/source/library/')) return response({ schema_version: 1, binary_retained: false, graph_ingestion_executed: false, sources: [source], has_more: false, window_limit: 20 })
      if (url.includes('/api/source/item/')) return response(sourceDTO)
      if (url.includes('/ingestion/plan/')) {
        if (planFailure) return rejectResponse('source_unavailable', 503)
        const payload = JSON.parse(options.body), ids = await ingestionIdentities(scope, payload.operation_id)
        planned = { schema_version: 1, scope, operation_id: payload.operation_id, episode_id: ids.episode_id, fingerprint: await ingestionFingerprint(scope, sourceDTO, payload), evidence_ids: [uuid], graph_ingestion_executed: false, model_calls_made: false, actual_usage_microusd: null, source_revision: uuid, source_sha256: source.text_sha256, source_byte_length: source.byte_length, source_codepoint_length: source.codepoint_length, ontology_revision: payload.ontology.revision, eligibility_codepoint_limit: 32768, spending_authorized: false }
        return response(planned)
      }
      if (url.includes('/ingestion/execute/')) return rejectResponse(executeCode, executeStatus)
      if (url.includes('/ingestion/operation/')) {
        if (statusMode === 'missing') return rejectResponse('not_found', 404)
        if (statusMode === 'denied') return new Response('private server token', { status: 401 })
        return response({ schema_version: 1, scope, operation_id: planned.operation_id, episode_id: planned.episode_id, fingerprint: planned.fingerprint, evidence_ids: planned.evidence_ids, graph_ingestion_executed: false, model_calls_made: null, actual_usage_microusd: null, state: 'uncertain', budget_state: 'uncertain', ceiling_microusd: 100, receipt: null })
      }
      if (url.includes('/api/graph/research/')) return rejectResponse('busy', 503)
      assert.fail('Unexpected route request: ' + url)
    }
    const mounted = mount(workbench.component)
    const banner = () => mounted.root.querySelector('.connection [role="status"]').textContent
    const postCount = route => calls.filter(call => call.method === 'POST' && call.url.includes(route)).length
    try {
      input(mounted.root, '#graph-id', 'graph_1'); input(mounted.root, '#bearer-token', 'test-token')
      mounted.root.querySelector('.connection-form').dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true })); await settle()
      clickText(mounted.root, copyFor('en').sources.load); await settle()
      mounted.root.querySelector('.source-list button').click(); await waitForRoute(() => !!mounted.root.querySelector('.selected-source'))
      const planForm = mounted.root.querySelector('.source-ingestion > form')
      planForm.dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true })); await waitForRoute(() => banner().includes(copyFor('en').ingestionBanner.plan))
      assert.equal(postCount('/execute/'), 0); assert.ok(!banner().includes(copyFor('en').errors.unavailable))
      planFailure = false
      planForm.dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true })); await waitForRoute(() => !!mounted.root.querySelector('.plan-review'))
      const acknowledgement = mounted.root.querySelector('.acknowledgement input'); acknowledgement.checked = true; acknowledgement.dispatchEvent(new dom.window.Event('change', { bubbles: true })); await settle()
      clickText(mounted.root, copyFor('en').ingestion.execute); await waitForRoute(() => banner().includes(copyFor('en').ingestionBanner.execute))
      assert.equal(postCount('/execute/'), 1)
      assert.ok(!banner().includes(copyFor('en').errors.unavailable)); assert.ok(!banner().includes('Submit again when ready'))
      assert.ok([...mounted.root.querySelectorAll('button')].find(button => button.textContent === copyFor('en').ingestion.execute).disabled)
      assert.equal([...mounted.root.querySelectorAll('button')].find(button => button.textContent === copyFor('en').ingestion.checkStatus).disabled, false)
      clickText(mounted.root, copyFor('en').ingestion.execute); await settle(); assert.equal(postCount('/execute/'), 1)
      for (const locale of ['zh', 'ms', 'en']) {
        const before = calls.length
        input(mounted.root, '#workbench-language', locale); await settle()
        assert.ok(banner().includes(copyFor(locale).ingestionBanner.execute)); assert.ok(!banner().includes(copyFor(locale).errors.unavailable)); assert.equal(calls.length, before)
      }
      clickText(mounted.root, copyFor('en').ingestion.checkStatus); await waitForRoute(() => banner().includes(copyFor('en').ingestionBanner.status))
      assert.equal(postCount('/execute/'), 1); assert.ok(!banner().includes(copyFor('en').errors.unavailable)); assert.ok(mounted.root.textContent.includes(copyFor('en').ingestion.notFound))
      statusMode = 'uncertain'; clickText(mounted.root, copyFor('en').ingestion.checkStatus); await waitForRoute(() => !!mounted.root.querySelector('.outcome'))
      assert.ok(mounted.root.querySelector('.outcome').textContent.includes(copyFor('en').ingestion.graphStates.uncertain)); assert.equal(postCount('/execute/'), 1)
      // Shared read failures retain their existing read retry guidance.
      input(mounted.root, '#question', 'question'); mounted.root.querySelector('.query-section form').dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true })); await settle()
      assert.ok(banner().includes(copyFor('en').errors.unavailable)); assert.equal(postCount('/execute/'), 1)
      statusMode = 'denied'; clickText(mounted.root, copyFor('en').ingestion.checkStatus); await settle()
      assert.ok(banner().includes(copyFor('en').errors.unauthorized)); assert.ok(!banner().includes(copyFor('en').ingestionBanner.status))
      assert.equal(mounted.root.querySelector('.graph-overview'), null); assert.equal(mounted.root.querySelector('.attempt'), null); assert.equal(mounted.root.querySelector('#bearer-token').value, '')
      assert.ok(!mounted.root.textContent.includes('private server token')); assert.equal(postCount('/execute/'), 1)
    } finally { mounted.cleanup(); globalThis.fetch = previousFetch }
  }
})
