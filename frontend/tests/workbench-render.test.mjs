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
const dossierExport = await compile('../src/components/workbench/DossierExport.vue')
const evidence = await compile('../src/components/workbench/EvidenceResults.vue', { './DossierExport.vue': dossierExport.url })
const sources = await compile('../src/components/workbench/SourceLibrary.vue')
const ingestion = await compile('../src/components/workbench/SourceIngestion.vue')
const experiments = await compile('../src/components/workbench/ExperimentComparison.vue')
const population = await compile('../src/components/workbench/PopulationWorkbench.vue')
const preparation = await compile('../src/components/workbench/SimulationPreparation.vue')
const nativeLaunch = await compile('../src/components/workbench/NativeLaunch.vue')
const nativeObservations = await compile('../src/components/workbench/NativeObservations.vue')
const workbench = await compile('../src/views/ResearchWorkbench.vue', { '../components/workbench/EvidenceResults.vue': evidence.url, '../components/workbench/SourceLibrary.vue': sources.url, '../components/workbench/SourceIngestion.vue': ingestion.url, '../components/workbench/ExperimentComparison.vue': experiments.url, '../components/workbench/PopulationWorkbench.vue': population.url, '../components/workbench/SimulationPreparation.vue': preparation.url, '../components/workbench/NativeLaunch.vue': nativeLaunch.url, '../components/workbench/NativeObservations.vue': nativeObservations.url })
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

test('P06 inspected page evidence preserves graph eligibility32768 and never automatically plans', async () => {
  for (const length of [32768, 32769]) {
    const inspected = { source: { source_revision: uuid, source_name: 'Extracted PDF', text_sha256: 'a'.repeat(64), byte_length: length, codepoint_length: length }, text: 'x'.repeat(length), passages: [{ evidence_id: uuid, start: 0, end: 1, page: 2, excerpt_sha256: 'b'.repeat(64) }] }
    let plans = 0, executions = 0
    const mounted = mount(ingestion.component, { connected: true, inspected, locale: 'en', methods: { plan: async () => { plans++; throw { code: 'source_unavailable' } }, execute: async () => { executions++ } } })
    try {
      await settle(); assert.equal(plans, 0); assert.equal(executions, 0)
      assert.equal(mounted.root.querySelector('.source-ingestion fieldset').disabled, length > 32768)
      if (length > 32768) assert.ok(mounted.root.textContent.includes(copyFor('en').ingestion.ineligible))
    } finally { mounted.cleanup() }
  }
})

function experimentCatalog() {
  return { version: 1, project_id: uuid, project_revision: 1, cohort_manifest_digest: 'a'.repeat(64), public_projection_digest: 'b'.repeat(64), members: [{ member_id: 'member:1', member_label: malicious, case_label: 'Case', run_id: uuid, state: 'failed', cancel_requested: true, seed: '9223372036854775807', max_rounds: 2, platforms: ['twitter'] }] }
}
test('actual nested experiment section uses manual catalog only and clears on 401/403, disconnect and reload', async () => {
  for (const action of ['401', '403', 'disconnect', 'reload']) {
    const previousFetch = globalThis.fetch, calls = []
    let mounted
    globalThis.fetch = async (url, options) => {
      calls.push({ url, options })
      if (url.includes('/api/graph/data/')) return response(graphResult)
      assert.equal(url, 'http://127.0.0.1:5001/api/experiments/catalog'); assert.equal(options.method, 'GET'); assert.equal(options.body, undefined)
      if (calls.length === 3 && ['401', '403'].includes(action)) return new Response('private server secret', { status: Number(action) })
      return response(experimentCatalog())
    }
    try {
      mounted = mount(workbench.component); await settle(); assert.equal(calls.length, 0)
      assert.equal(mounted.root.querySelector('.experiment-comparison .catalog-load').disabled, true)
      input(mounted.root, '#graph-id', 'graph_1'); input(mounted.root, '#bearer-token', 'secret')
      mounted.root.querySelector('.connection-form').dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true })); await settle(); assert.equal(calls.length, 1)
      input(mounted.root, '#workbench-language', 'zh'); await settle(); assert.equal(calls.length, 1)
      mounted.root.querySelector('.catalog-load').click(); await settle(); assert.equal(calls.length, 2)
      assert.ok(mounted.root.querySelector('.catalog-member').textContent.includes('9223372036854775807'))
      assert.ok(mounted.root.querySelector('.catalog-member').textContent.includes(malicious)); assert.equal(mounted.root.querySelector('.experiment-comparison img, .experiment-comparison script, .experiment-comparison a'), null)
      if (['401', '403'].includes(action)) { mounted.root.querySelector('.catalog-load').click(); await settle(); assert.equal(calls.length, 3); assert.equal(mounted.root.querySelector('.graph-overview'), null) }
      else if (action === 'disconnect') { clickText(mounted.root, copyFor('zh').disconnect); await settle() }
      else { mounted.cleanup(); mounted = mount(workbench.component); await settle() }
      assert.equal(mounted.root.querySelector('.catalog-member'), null); assert.equal(mounted.root.querySelector('.comparison-result'), null)
      assert.equal(mounted.root.querySelector('#bearer-token').value, ''); assert.equal(mounted.root.querySelector('.catalog-load').disabled, true)
      assert.ok(!mounted.root.textContent.includes('private server secret'))
      assert.equal(calls.filter(c => c.options.method === 'POST').length, 0)
    } finally { mounted?.cleanup(); globalThis.fetch = previousFetch }
  }
})
test('shared parent cancellation aborts the pending experiment read and late catalog cannot populate UI', async () => {
  const previousFetch = globalThis.fetch; let resolve, signal, calls = 0
  globalThis.fetch = async (_url, options) => ++calls === 1 ? response(graphResult) : new Promise(r => { resolve = r; signal = options.signal })
  const mounted = mount(workbench.component)
  try {
    input(mounted.root, '#graph-id', 'graph_1'); input(mounted.root, '#bearer-token', 'secret')
    mounted.root.querySelector('.connection-form').dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true })); await settle()
    mounted.root.querySelector('.catalog-load').click(); await settle()
    assert.equal(mounted.root.querySelector('.query-section fieldset').disabled, true)
    clickText(mounted.root, copyFor('en').cancel); await settle(); assert.equal(signal.aborted, true)
    resolve(response(experimentCatalog())); await settle(); assert.equal(mounted.root.querySelector('.catalog-member'), null); assert.equal(calls, 2)
    assert.ok(mounted.root.querySelector('.experiment-comparison .feedback').textContent.includes(copyFor('en').experiments.cancelled))
    assert.equal(mounted.root.querySelector('.query-section fieldset').disabled, false)
  } finally { mounted.cleanup(); globalThis.fetch = previousFetch }
})

function retainedExportDossier(request) {
  const normalized = { ...request, valid_at: request.valid_at ?? null, recorded_before: request.recorded_before ?? null }
  return { schema_version: 1, mode: 'model_free_evidence_dossier', request: normalized, sections: request.sections.map((s, i) => ({ ordinal: i + 1, heading: s.heading, source_claim_keys: [], simulation_observation_keys: [], other_claim_keys: [] })), claims: [], references: [], research_trace: request.sections.map((s, i) => ({ ordinal: i + 1, request_sha256: 'a'.repeat(64), response_sha256: 'b'.repeat(64), query: s.query, top_k: s.top_k, display_graph_ids: request.display_graph_ids, valid_at: null, recorded_before: null, scopes: [{ display_graph_id: 'graph_1', scope, pages: 1, scanned: 0, eligible: 0, excluded: 0, unknown: 0, returned: 0, truncated: false }], passage_coverage: [], competing_claim_candidates: [], linked_citations: 0, resolved_citations: 0, unavailable_citations: 0, historical: false, historical_semantics: 'retained_edges_not_bitemporal_reconstruction', rank_basis: 'lexical_token_overlap' })), summary: { section_count: request.sections.length, query_count: request.sections.length, distinct_scoped_facts: 0, reference_links: 0, resolved_references: 0, unavailable_references: 0, query_reference_links: 0, query_resolved_references: 0, query_unavailable_references: 0, scanned_per_query_sum: 0, unknown_per_query_sum: 0, truncated_query_scopes: 0, passage_coverage: [], coverage_label: 'retrieved_passage_union_per_retained_revision' }, input_sha256: 'a'.repeat(64), trace_sha256: 'b'.repeat(64), records_sha256: 'c'.repeat(64), model_generated: false, semantic_judge_used: false, claim_support_status: 'not_reviewed', consistency: 'individually_guarded_queries_not_atomic_snapshot', limitations: ['Lexical only'], markdown: '\uFEFF# 中😀\r\n<script>inert</script> &#60;img&#62;' }
}
test('actual route admits dossier then exports locally; new request, disconnect, authorization failure and unmount clean its URL', async () => {
  for (const lifecycle of ['request', 'disconnect', 'authorization', 'unmount']) {
    const previousFetch = globalThis.fetch, priorCreate = URL.createObjectURL, priorRevoke = URL.revokeObjectURL, priorClick = dom.window.HTMLAnchorElement.prototype.click
    const calls = [], blobs = [], clicks = [], revoked = []; let pending, pendingSignal
    globalThis.fetch = async (url, options) => {
      calls.push({ url, options })
      if (calls.length === 1) return response(graphResult)
      if (calls.length === 2) return response(retainedExportDossier(JSON.parse(options.body)))
      if (lifecycle === 'authorization') return new Response('private secret', { status: 401 })
      pendingSignal = options.signal
      return new Promise(resolve => { pending = resolve })
    }
    URL.createObjectURL = blob => { blobs.push(blob); return `blob:route-${blobs.length}` }
    URL.revokeObjectURL = url => revoked.push(url)
    dom.window.HTMLAnchorElement.prototype.click = function () { clicks.push({ href: this.href, filename: this.download }) }
    const mounted = mount(workbench.component); let closed = false
    try {
      input(mounted.root, '#graph-id', 'graph_1'); input(mounted.root, '#bearer-token', 'test-token')
      mounted.root.querySelector('.connection-form').dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true })); await settle()
      const radio = mounted.root.querySelector('input[value="dossier"]'); radio.checked = true; radio.dispatchEvent(new dom.window.Event('change', { bubbles: true })); await settle()
      input(mounted.root, '#dossier-title', 'Retained evidence'); input(mounted.root, '#heading-0', 'Evidence'); input(mounted.root, '#query-0', '中')
      mounted.root.querySelector('.query-section form').dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true })); await settle()
      assert.equal(calls.length, 2); assert.ok(calls[1].url.endsWith('/api/graph/dossier/graph_1'))
      assert.equal(blobs.length, 0); assert.equal(clicks.length, 0)
      const exports = mounted.root.querySelector('.dossier-export'); assert.ok(exports)
      exports.querySelector('button').click(); await settle()
      assert.equal(clicks[0].filename, 'nexaweave-evidence-dossier.md'); assert.equal(calls.length, 2)
      const mdBytes = new Uint8Array(await blobs[0].arrayBuffer())
      input(mounted.root, '#workbench-language', 'zh'); await settle()
      assert.equal(exports.querySelector('button').textContent, copyFor('zh').exports.markdown)
      assert.equal(clicks.length, 1); assert.equal(calls.length, 2)
      exports.querySelectorAll('button')[1].click(); await settle()
      assert.equal(clicks[1].filename, 'nexaweave-evidence-dossier.json'); assert.equal(calls.length, 2)
      const dto = JSON.parse(await blobs[1].text()); assert.deepEqual(mdBytes, new TextEncoder().encode(dto.markdown))
      assert.deepEqual(revoked, ['blob:route-1'])
      if (lifecycle === 'disconnect') [...mounted.root.querySelectorAll('button')].find(b => b.textContent === copyFor('zh').disconnect).click()
      else if (lifecycle === 'unmount') { mounted.cleanup(); closed = true }
      else mounted.root.querySelector('.query-section form').dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true }))
      await settle()
      assert.deepEqual(revoked, ['blob:route-1', 'blob:route-2'])
      assert.equal(mounted.root.querySelector('.dossier-export'), null)
      assert.equal(mounted.root.textContent.includes(copyFor('zh').exports.requested), false)
      assert.equal(clicks.length, 2)
      if (lifecycle === 'request') {
        assert.equal(calls.length, 3); mounted.cleanup(); closed = true; assert.equal(pendingSignal.aborted, true)
        pending(response(retainedExportDossier(JSON.parse(calls[2].options.body)))); await settle(); assert.equal(clicks.length, 2)
      } else if (lifecycle === 'authorization') {
        assert.equal(calls.length, 3); assert.equal(mounted.root.querySelector('.graph-overview'), null)
        assert.ok(!mounted.root.textContent.includes('private secret'))
      } else assert.equal(calls.length, 2)
    } finally {
      if (!closed) mounted.cleanup()
      globalThis.fetch = previousFetch; URL.createObjectURL = priorCreate; URL.revokeObjectURL = priorRevoke; dom.window.HTMLAnchorElement.prototype.click = priorClick
    }
  }
})
