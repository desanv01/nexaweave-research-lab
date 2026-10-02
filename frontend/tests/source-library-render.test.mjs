// Actual Vue SFC compilation/mount regression sources, not browser acceptance.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { webcrypto } from 'node:crypto'
import { JSDOM } from 'jsdom'
import { parse, compileScript } from '@vue/compiler-sfc'
import { copyFor, workbenchCopy } from '../src/i18n/workbench.js'
import { sha256 } from '../src/api/sourceLibrary.js'
Object.defineProperty(globalThis, 'crypto', { value: webcrypto, configurable: true })
const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://127.0.0.1:5173/research' })
for (const name of ['window', 'document', 'Element', 'HTMLElement', 'SVGElement', 'Node']) globalThis[name] = name === 'window' ? dom.window : dom.window[name]
const { createApp, nextTick, h, reactive } = await import('vue')
async function compile(path, replacements = {}) {
  const file = new URL(path, import.meta.url), { descriptor } = parse(readFileSync(file, 'utf8'), { filename: file.pathname })
  const compiled = compileScript(descriptor, { id: path, inlineTemplate: true, genDefaultAs: '__component' })
  let code = compiled.content.replace(/from (['"])vue\1/g, `from ${JSON.stringify(import.meta.resolve('vue'))}`)
  code = code.replace(/from (['"])(\.{1,2}\/[^'"]+)\1/g, (_match, _quote, relative) => `from ${JSON.stringify(replacements[relative] || new URL(relative, file).href)}`)
  const url = `data:text/javascript;base64,${Buffer.from(`${code}\nexport default __component`).toString('base64')}`
  return { component: (await import(url)).default, url }
}
const sources = await compile('../src/components/workbench/SourceLibrary.vue')
const evidence = await compile('../src/components/workbench/EvidenceResults.vue')
const ingestion = await compile('../src/components/workbench/SourceIngestion.vue')
const route = await compile('../src/views/ResearchWorkbench.vue', { '../components/workbench/SourceLibrary.vue': sources.url, '../components/workbench/EvidenceResults.vue': evidence.url, '../components/workbench/SourceIngestion.vue': ingestion.url })
function mount(component, initial = {}) {
  const props = reactive(initial), root = document.createElement('div'); document.body.append(root)
  const app = createApp({ setup: () => () => h(component, props) })
  app.component('RouterLink', { props: ['to'], setup: (p, { slots }) => () => h('a', { href: p.to }, slots.default?.()) })
  app.mount(root)
  return { root, props, cleanup() { app.unmount(); root.remove() } }
}
async function settle() { for (let i = 0; i < 16; i++) { await Promise.resolve(); await nextTick() } }
async function cryptoSettle() { await new Promise(r => setTimeout(r, 30)); await settle() }
function input(root, selector, value) { const el = root.querySelector(selector); el.value = value; el.dispatchEvent(new dom.window.Event(el.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true })) }
function button(root, text) { return [...root.querySelectorAll('button')].find(b => b.textContent === text) }
const cp = copyFor('en').sources, enc = new TextEncoder(), revision = '11111111-1111-4111-8111-111111111111', project = '22222222-2222-4222-8222-222222222222'
const malicious = '<img src=x onerror=alert(1)> 中😀 https://evil.test'
test('source selection emits only explicit successful inspection and clears on close and reset', async () => {
  const seen = [], data = await item()
  const mounted = mount(sources.component, { connected: true, resetVersion: 0, locale: 'en', onInspected: value => seen.push(value), methods: { list: async () => library([data.source]), get: async () => data } })
  try {
    assert.equal(seen.length, 0)
    button(mounted.root, cp.load).click(); await settle(); assert.equal(seen.length, 0)
    mounted.root.querySelector('.source-list button').click(); await settle(); assert.equal(seen.at(-1).source.source_revision, revision)
    mounted.root.querySelector('#source-inspector > button').click(); await settle(); assert.equal(seen.at(-1), null)
    mounted.root.querySelector('.source-list button').click(); await settle(); assert.equal(seen.at(-1).source.source_revision, revision)
    mounted.props.resetVersion++; await settle(); assert.equal(seen.at(-1), null)
  } finally { mounted.cleanup() }
})
const flags = { schema_version: 1, binary_retained: false, graph_ingestion_executed: false }
function library(sources = [], has_more = false) { return { ...flags, sources, has_more, window_limit: 20 } }
async function item() {
  const text = '\uFEFF' + malicious, points = Array.from(text)
  return { ...flags, source: { project_id: project, source_revision: revision, source_name: '<script>source</script>', text_sha256: await sha256(enc.encode(text)), byte_length: enc.encode(text).length, codepoint_length: points.length, recorded_at: '2026-10-02T00:00:00Z' }, text, offset_unit: 'unicode_codepoint', passages: await Promise.all([[2, 12], [0, 8]].map(async ([start, end], i) => ({ evidence_id: `33333333-3333-5333-8333-${String(i).padStart(12, '0')}`, start, end, page: null, excerpt_sha256: await sha256(enc.encode(points.slice(start, end).join(''))) }))) }
}
test('EN/ZH/MS source keys are complete; initial empty/loading/window states are truthful', async () => {
  for (const locale of ['zh', 'ms']) assert.deepEqual(Object.keys(workbenchCopy[locale].sources).sort(), Object.keys(cp).sort())
  let loads = 0
  const mounted = mount(sources.component, { connected: true, busy: false, resetVersion: 0, locale: 'en', methods: { list: async () => { loads++; return library() } } })
  try {
    assert.equal(loads, 0); assert.ok(!mounted.root.textContent.includes(cp.empty))
    button(mounted.root, cp.load).click(); await settle()
    assert.equal(loads, 1); assert.ok(mounted.root.textContent.includes(cp.empty)); assert.ok(mounted.root.textContent.includes(cp.stale))
    mounted.props.locale = 'ms'; await settle(); assert.ok(mounted.root.textContent.includes(copyFor('ms').sources.empty))
    mounted.props.connected = false; await settle(); assert.equal(mounted.root.querySelector('.source-window'), null)
    assert.ok(button(mounted.root, copyFor('ms').sources.load).disabled)
  } finally { mounted.cleanup() }
})
test('actual source inspection uses literal text, stored declaration order and codepoint excerpts with focus return', async () => {
  const data = await item(); let requests = []
  const mounted = mount(sources.component, { connected: true, resetVersion: 0, locale: 'en', methods: { list: async () => library([data.source]), get: async p => { requests.push(p); return data } } })
  try {
    button(mounted.root, cp.load).click(); await settle()
    mounted.root.querySelector('.source-list button').click(); await settle()
    assert.deepEqual(requests, [{ source_revision: revision, project_id: project }])
    assert.equal(document.activeElement.id, 'source-inspector')
    assert.equal(mounted.root.querySelector('.exact-text').textContent, data.text)
    const passageButtons = [...mounted.root.querySelectorAll('.source-inspector ol button')]
    assert.ok(passageButtons[0].textContent.startsWith('2–12')); assert.ok(passageButtons[1].textContent.startsWith('0–8'))
    passageButtons[1].click(); await settle()
    assert.equal(document.activeElement.id, 'source-excerpt')
    assert.equal(mounted.root.querySelector('.excerpt-text').textContent, Array.from(data.text).slice(0, 8).join(''))
    mounted.root.querySelector('#source-excerpt').dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); await settle()
    assert.equal(document.activeElement, passageButtons[1])
    assert.equal(mounted.root.querySelector('img,script,a'), null)
  } finally { mounted.cleanup() }
})
test('prepared UUID is reviewed once; uncertain retention is never retried and explicit GET reconciles exact attempt', async () => {
  let posted, posts = 0, gets = 0
  const methods = { retain: async p => { posted = p; posts++; throw { code: 'outcome_unknown' } }, get: async p => {
    gets++; assert.equal(p.source_revision, posted.source_revision)
    return { ...flags, source: { project_id: project, source_revision: posted.source_revision, source_name: posted.source_name, text_sha256: posted.input_sha256, byte_length: enc.encode(posted.content).length, codepoint_length: Array.from(posted.content).length, recorded_at: '2026-10-02T00:00:00Z' }, text: posted.content, offset_unit: 'unicode_codepoint', passages: [] }
  } }
  const mounted = mount(sources.component, { connected: true, resetVersion: 0, locale: 'en', methods })
  try {
    input(mounted.root, '#source-name', 'Exact 中'); input(mounted.root, '#source-text', '\uFEFF中😀'); await settle()
    mounted.root.querySelector('.source-form').dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true })); await cryptoSettle()
    assert.equal(posts, 0)
    const reviewed = mounted.root.querySelector('.prepared').textContent
    button(mounted.root, cp.retain).click(); await cryptoSettle()
    assert.equal(posts, 1); assert.ok(reviewed.includes(posted.source_revision)); assert.ok(reviewed.includes(posted.input_sha256))
    assert.equal(mounted.root.querySelector('.prepared'), null); assert.ok(mounted.root.textContent.includes(cp.unknown))
    assert.equal(mounted.root.querySelector('#source-text').value, '')
    await settle(); assert.equal(posts, 1); assert.equal(gets, 0)
    button(mounted.root, cp.inspectAttempt).click(); await settle()
    assert.equal(gets, 1); assert.equal(posts, 1)
    assert.ok(mounted.root.textContent.includes(cp.noPassages)); assert.equal(mounted.root.querySelector('.source-form fieldset').disabled, false)
  } finally { mounted.cleanup() }
})
test('successful receipt is distinct from graph evidence and never starts any extra request', async () => {
  let posts = 0
  const methods = { retain: async p => { posts++; return { ...flags, source: { project_id: project, source_revision: p.source_revision, source_name: p.source_name, text_sha256: p.input_sha256, byte_length: 5, codepoint_length: 5, recorded_at: '2026-10-02T00:00:00Z' }, offset_unit: 'unicode_codepoint', passages: [], extraction: { format: 'text', input_sha256: p.input_sha256 } } } }
  const mounted = mount(sources.component, { connected: true, resetVersion: 0, locale: 'en', methods })
  try {
    input(mounted.root, '#source-name', 'Name'); input(mounted.root, '#source-text', 'exact'); await settle()
    mounted.root.querySelector('.source-form').dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true })); await cryptoSettle()
    button(mounted.root, cp.retain).click(); await settle()
    assert.equal(posts, 1); assert.ok(mounted.root.querySelector('.receipt').textContent.includes(cp.flags)); assert.ok(mounted.root.textContent.includes(cp.noGraph))
    assert.ok(mounted.root.textContent.includes(cp.textExtraction)); assert.equal(mounted.root.querySelector('.source-inspector'), null)
  } finally { mounted.cleanup() }
})
test('disconnect/reset and unmount prevent late completion from repopulating source drafts or results', async () => {
  let resolve
  const mounted = mount(sources.component, { connected: true, resetVersion: 0, locale: 'en', methods: { list: () => new Promise(r => { resolve = r }) } })
  input(mounted.root, '#source-name', 'Private'); input(mounted.root, '#source-text', 'Private body'); await settle()
  button(mounted.root, cp.load).click(); await settle()
  mounted.props.connected = false; mounted.props.resetVersion++; await settle()
  resolve(library([(await item()).source])); await settle()
  assert.equal(mounted.root.querySelector('.source-window'), null); assert.equal(mounted.root.querySelector('#source-name').value, ''); assert.equal(mounted.root.querySelector('#source-text').value, '')
  mounted.cleanup()
})
test('real shared route: source404 leaves research usable; nonJSON401 clears source and graph private state', async () => {
  const previousFetch = globalThis.fetch; let count = 0
  const graph = { graph_id: 'graph_1', nodes: [], edges: [], node_count: 0, edge_count: 0 }
  globalThis.fetch = async () => {
    count++
    if (count === 1) return new Response(JSON.stringify({ success: true, data: graph }), { headers: { 'Content-Type': 'application/json' } })
    if (count === 2) return new Response('<html>private unavailable</html>', { status: 404 })
    return new Response('secret token traceback', { status: 401 })
  }
  const mounted = mount(route.component)
  try {
    input(mounted.root, '#graph-id', 'graph_1'); input(mounted.root, '#bearer-token', 'test-token')
    mounted.root.querySelector('.connection-form').dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true })); await settle()
    button(mounted.root, cp.load).click(); await settle()
    assert.ok(mounted.root.textContent.includes(cp.unavailable)); assert.equal(mounted.root.querySelector('.query-section fieldset').disabled, false)
    input(mounted.root, '#source-name', 'Private'); input(mounted.root, '#source-text', 'Private body'); await settle()
    button(mounted.root, cp.load).click(); await settle()
    assert.equal(mounted.root.querySelector('.graph-overview'), null); assert.equal(mounted.root.querySelector('#source-name').value, ''); assert.equal(mounted.root.querySelector('#source-text').value, '')
    assert.equal(mounted.root.querySelector('.query-section fieldset').disabled, true)
    assert.ok(!mounted.root.textContent.includes('secret token traceback')); assert.equal(mounted.root.querySelector('#bearer-token').value, '')
  } finally { mounted.cleanup(); globalThis.fetch = previousFetch }
})
test('native controls have visible labels and source layout rules remain bounded; browser measurements belong to Main', async () => {
  const mounted = mount(sources.component, { connected: true, resetVersion: 0, locale: 'en', methods: {} })
  try {
    for (const id of ['source-name', 'source-text']) assert.ok(mounted.root.querySelector(`label[for="${id}"]`))
    for (const b of mounted.root.querySelectorAll('button')) assert.ok(['button', 'submit'].includes(b.getAttribute('type')))
  } finally { mounted.cleanup() }
  const source = readFileSync(new URL('../src/components/workbench/SourceLibrary.vue', import.meta.url), 'utf8')
  assert.ok(source.includes('min-height:44px')); assert.ok(source.includes(':focus-visible')); assert.ok(source.includes('minmax(0,1fr)')); assert.ok(source.includes('prefers-reduced-motion'))
  assert.ok(!source.includes('v-html')); assert.ok(!source.includes('localStorage')); assert.ok(!source.includes('sessionStorage'))
})
test('source_denied renders fixed localized copy without server-provided text', async () => {
  const mounted = mount(sources.component, { connected: true, resetVersion: 0, locale: 'en', methods: { list: async () => { throw { code: 'source_denied', message: 'private token traceback' } } } })
  try {
    button(mounted.root, cp.load).click(); await settle()
    assert.equal(mounted.root.querySelector('.source-feedback').textContent, cp.denied)
    assert.ok(!mounted.root.textContent.includes('private token traceback'))
    for (const locale of ['zh', 'ms']) {
      mounted.props.locale = locale; await settle()
      assert.equal(mounted.root.querySelector('.source-feedback').textContent, copyFor(locale).sources.denied)
    }
  } finally { mounted.cleanup() }
})
