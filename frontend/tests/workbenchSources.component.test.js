// Real workbench and PDF source UI; Main performs execution.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { JSDOM } from 'jsdom'
import { createHash, webcrypto } from 'node:crypto'
import { parse, compileScript } from '@vue/compiler-sfc'
import { copyFor } from '../src/i18n/workbench.js'

globalThis.crypto ||= webcrypto
const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://127.0.0.1:5173/research' })
for (const name of ['window', 'document', 'Element', 'HTMLElement', 'SVGElement', 'Node', 'Document', 'ShadowRoot']) globalThis[name] = name === 'window' ? dom.window : dom.window[name]
globalThis.localStorage = dom.window.localStorage
const { createApp, h, nextTick, reactive } = await import('vue')
const dataModule = code => `data:text/javascript;base64,${Buffer.from(code).toString('base64')}`
const localeFiles = Object.fromEntries(['en', 'zh', 'ms'].map(key => [`../../../locales/${key}.json`, { default: JSON.parse(readFileSync(new URL(`../../locales/${key}.json`, import.meta.url), 'utf8')) }]))
let localeSource = readFileSync(new URL('../src/i18n/index.js', import.meta.url), 'utf8')
localeSource = localeSource.replace(/import languages from [^\n]+/, `const languages = ${readFileSync(new URL('../../locales/languages.json', import.meta.url), 'utf8')}`)
localeSource = localeSource.replace(/import\.meta\.glob\([^\n]+\)/, JSON.stringify(localeFiles)).replace(/from (['"])(vue|vue-i18n)\1/g, (_m, _q, name) => `from ${JSON.stringify(import.meta.resolve(name))}`)
const localeUrl = dataModule(localeSource), shared = await import(localeUrl)
async function compile(path, replacements = {}) {
  const file = new URL(path, import.meta.url), { descriptor } = parse(readFileSync(file, 'utf8'), { filename: file.pathname })
  let code = compileScript(descriptor, { id: path, inlineTemplate: true, genDefaultAs: '__component' }).content
  code = code.replace(/from (['"])vue\1/g, `from ${JSON.stringify(import.meta.resolve('vue'))}`)
  code = code.replace(/from (['"])(\.{1,2}\/[^'"]+|@\/i18n\/index\.js)\1/g, (_m, _q, relative) => `from ${JSON.stringify(replacements[relative] || new URL(relative, file).href)}`)
  const url = dataModule(code + '\nexport default __component'); return { url, component: (await import(url)).default }
}
const sources = await compile('../src/components/workbench/SourceLibrary.vue')
const picker = await compile('../src/components/LanguageSwitcher.vue', { '@/i18n/index.js': localeUrl })
const empty = dataModule('export default {render(){return null}}')
const replacements = Object.fromEntries(['EvidenceResults', 'SourceIngestion', 'ExperimentComparison', 'PopulationWorkbench', 'SimulationPreparation', 'NativeLaunch', 'NativeObservations', 'ConnectedReports', 'ConnectedFollowup'].map(name => [`../components/workbench/${name}.vue`, empty]))
const twoHop = await compile('../src/components/workbench/TwoHopResearch.vue')
const parent = await compile('../src/views/ResearchWorkbench.vue', { '../components/workbench/TwoHopResearch.vue': twoHop.url, ...replacements, '../i18n/index.js': localeUrl, '../components/workbench/SourceLibrary.vue': sources.url })
const ingestion = await compile('../src/components/workbench/SourceIngestion.vue')
const population = await compile('../src/components/workbench/PopulationWorkbench.vue')
const preparation = await compile('../src/components/workbench/SimulationPreparation.vue')
const refreshParent = await compile('../src/views/ResearchWorkbench.vue', { '../components/workbench/TwoHopResearch.vue': twoHop.url, ...replacements, '../i18n/index.js': localeUrl,
  '../components/workbench/SourceLibrary.vue': sources.url, '../components/workbench/SourceIngestion.vue': ingestion.url,
  '../components/workbench/PopulationWorkbench.vue': population.url, '../components/workbench/SimulationPreparation.vue': preparation.url })
function mount(component = parent.component) {
  const root = document.createElement('div'); document.body.append(root)
  // The picker and original-journey heading consume the same global composer.
  const app = createApp({ setup: () => () => h('div', [h(picker.component), h('h2', { class: 'original-title' }, shared.default.global.t('history.title')), h(component)]) })
  app.use(shared.default); app.component('RouterLink', { props: ['to'], setup: (p, { slots }) => () => h('a', { href: p.to }, slots.default?.()) }); app.mount(root)
  let mounted = true
  return { root, cleanup() { if (mounted) { mounted = false; app.unmount(); root.remove() } } }
}
function input(m, selector, value) { const field = m.root.querySelector(selector); field.value = value; field.dispatchEvent(new dom.window.Event(field.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true })) }
async function waitFor(predicate) { const end = Date.now() + 3000; do { await nextTick(); if (predicate()) return; await new Promise(resolve => setImmediate(resolve)) } while (Date.now() < end); assert.ok(predicate(), 'bounded source condition did not complete') }
const uid = n => `00000000-0000-0000-0000-${String(n).padStart(12, '0')}`
const original = Buffer.from('%PDF-1.4\nretained original\n%%EOF\n')
const metadata = { schema_version: 2, binary_retained: true, graph_ingestion_executed: false,
  source: { project_id: uid(2), source_revision: uid(5), source_name: 'Source 猫 😀', text_sha256: 'a'.repeat(64), byte_length: 8, codepoint_length: 3, recorded_at: '2026-10-08T00:00:00Z' },
  binary: { contract_version: 1, project_id: uid(2), source_revision: uid(5), media_type: 'application/pdf', byte_length: original.length, sha256: createHash('sha256').update(original).digest('hex') } }
const reply = data => new Response(JSON.stringify({ success: true, data }), { headers: { 'Content-Type': 'application/json' } })

function projection(label, type, count) {
  return { graph_id: 'graph_1', edges: [], edge_count: 0, node_count: count,
    nodes: Array.from({ length: count }, (_, i) => ({ uuid: uid(100 + i), name: `${label} ${i}`, labels: ['Entity', type],
      summary: 'Retained graph projection', fact: null, attributes: {}, episodes: [], evidence_ids: [],
      created_at: null, valid_at: null, invalid_at: null, expired_at: null, score: null })) }
}
function graphTransport() {
  const calls = [], state = { mode: 'success', release: null }; let graphReads = 0
  const text = 'rain', digest = createHash('sha256').update(text).digest('hex')
  const source = { ...metadata.source, byte_length: 4, codepoint_length: 4, text_sha256: digest }
  const initial = projection('Initial actor', 'Person', 11), fresh = projection('Refreshed actor', 'Organization', 3)
  return { calls, state, initial, fresh, source, fetch: async (url, options) => {
    calls.push({ url, options })
    if (url.includes('/data/')) {
      graphReads++
      if (graphReads === 1) return reply(initial)
      if (state.mode === 'hold') return new Promise(resolve => { state.release = response => resolve(response || reply(projection('Late actor', 'LateType', 1))) })
      if (state.mode === 'invalid') return reply({ ...fresh, node_count: 999 })
      if ([401, 403].includes(state.mode)) return new Response('private graph traceback', { status: state.mode })
      return reply(fresh)
    }
    if (url.includes('/source/library/')) return reply({ schema_version: 1, binary_retained: false, graph_ingestion_executed: false,
      sources: [source, { ...source, source_revision: uid(31), source_name: 'Additional retained document' }], has_more: false, window_limit: 20 })
    if (url.includes('/source/item/')) return reply({ schema_version: 1, binary_retained: false, graph_ingestion_executed: false,
      source, text, offset_unit: 'unicode_codepoint', passages: [{ evidence_id: uid(8), start: 0, end: 4, page: null, excerpt_sha256: digest }] })
    throw new Error('Unexpected graph refresh fixture request')
  } }
}
async function connectRefresh(m) {
  input(m, '#graph-id', 'graph_1'); input(m, '#bearer-token', 'private-token')
  m.root.querySelector('.connection-form').dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true }))
  await waitFor(() => m.root.querySelector('.graph-refresh') && !m.root.querySelector('.graph-refresh').disabled)
}
async function inspectRefreshSource(m, { editEntityName = true } = {}) {
  m.root.querySelector('.source-library .source-actions button').click()
  await waitFor(() => m.root.querySelector('.source-list button') && !m.root.querySelector('.source-list button').disabled)
  m.root.querySelector('.source-list button').click()
  await waitFor(() => m.root.querySelector('#source-inspector') && m.root.querySelector('#ingestion-operation') && !m.root.querySelector('#ingestion-operation').disabled)
  input(m, '#ingestion-operation', uid(77))
  if (editEntityName) input(m, '.source-ingestion input[id^="entity-name-"]', 'Investigator')
}

test('real parent graph refresh updates overview/pages and actor choices without resetting source/catalog/ingestion recovery', async () => {
  const previous = globalThis.fetch, wire = graphTransport(); globalThis.fetch = wire.fetch; shared.setUiLocale('en')
  const m = mount(refreshParent.component)
  try {
    assert.equal(wire.calls.length, 0); await connectRefresh(m); assert.equal(wire.calls.length, 1)
    await inspectRefreshSource(m)
    assert.ok(m.root.querySelector('.preparation-workbench .type-choices').textContent.includes('Person'))
    assert.ok(m.root.querySelector('.population-workbench .type-choices').textContent.includes('Person'))
    m.root.querySelector('.graph-overview nav button:last-child').click(); await nextTick()
    assert.ok(m.root.querySelector('.graph-overview ul').textContent.includes('Initial actor 10'))
    const count = wire.calls.length
    for (const locale of ['zh', 'ms', 'en']) {
      input(m, '#workbench-language', locale); await nextTick()
      assert.equal(m.root.querySelector('.graph-refresh').textContent, copyFor(locale).refreshGraph)
      assert.equal(wire.calls.length, count)
    }
    const button = m.root.querySelector('.graph-refresh'); button.focus(); assert.equal(document.activeElement, button)
    assert.equal(button.type, 'button'); assert.equal(button.getAttribute('aria-describedby'), 'graph-refresh-help')
    button.click(); await waitFor(() => m.root.querySelector('.graph-overview ul').textContent.includes('Refreshed actor 0'))
    assert.equal(wire.calls.length, count + 1); assert.equal(m.root.querySelector('.graph-overview nav'), null)
    assert.ok(m.root.querySelector('.preparation-workbench .type-choices').textContent.includes('Organization'))
    assert.ok(m.root.querySelector('.population-workbench .type-choices').textContent.includes('Organization'))
    assert.equal(m.root.querySelector('#ingestion-operation').value, uid(77))
    assert.equal(m.root.querySelector('.source-ingestion input[id^="entity-name-"]').value, 'Investigator')
    assert.ok(m.root.querySelector('#source-inspector').textContent.includes(wire.source.source_name))
    assert.equal(m.root.querySelectorAll('.preparation-source-checkbox').length, 1)
    assert.equal(wire.calls.filter(c => c.url.includes('/ingestion/')).length, 0)
    assert.equal(wire.calls.at(-1).options.method, 'GET'); assert.equal(wire.calls.at(-1).options.body, undefined)
    assert.equal(wire.calls.at(-1).options.headers.Authorization, 'Bearer private-token')
  } finally { m.cleanup(); globalThis.fetch = previous; shared.setUiLocale('en') }
})

test('ordinary refresh refusal preserves old graph/page and inspected/catalog/ingestion state until an explicit successful retry', async () => {
  const previous = globalThis.fetch, wire = graphTransport(); globalThis.fetch = wire.fetch; shared.setUiLocale('en')
  const m = mount(refreshParent.component)
  try {
    await connectRefresh(m); await inspectRefreshSource(m)
    m.root.querySelector('.graph-overview nav button:last-child').click(); await nextTick()
    wire.state.mode = 'invalid'; const before = wire.calls.length
    m.root.querySelector('.graph-refresh').click()
    await waitFor(() => m.root.querySelector('.status').textContent.includes(copyFor('en').errors.invalid_reply))
    assert.equal(wire.calls.length, before + 1)
    assert.ok(m.root.querySelector('.graph-overview ul').textContent.includes('Initial actor 10'))
    assert.equal(m.root.querySelector('#ingestion-operation').value, uid(77))
    assert.equal(m.root.querySelector('.source-ingestion input[id^="entity-name-"]').value, 'Investigator')
    assert.ok(m.root.querySelector('#source-inspector')); assert.equal(m.root.querySelectorAll('.preparation-source-checkbox').length, 1)
    wire.state.mode = 'success'; m.root.querySelector('.graph-refresh').click()
    await waitFor(() => m.root.querySelector('.graph-overview ul').textContent.includes('Refreshed actor 0'))
    assert.equal(wire.calls.length, before + 2)
  } finally { m.cleanup(); globalThis.fetch = previous }
})

for (const action of ['cancel', 'disconnect', 'reconnect', 'unmount']) {
  test(`parent ${action} fences a late refresh graph and its authority`, async () => {
    const previous = globalThis.fetch, wire = graphTransport(); globalThis.fetch = wire.fetch; shared.setUiLocale('en')
    const m = mount(refreshParent.component)
    try {
      await connectRefresh(m); await inspectRefreshSource(m); wire.state.mode = 'hold'; m.root.querySelector('.graph-refresh').click()
      await waitFor(() => wire.state.release)
      const signal = wire.calls.at(-1).options.signal
      if (action === 'cancel') m.root.querySelector('.connection > button').click()
      else if (action === 'unmount') m.cleanup()
      else {
        m.root.querySelector('.connection-form button:last-child').click(); await nextTick()
        if (action === 'reconnect') { wire.state.mode = 'success'; await connectRefresh(m) }
      }
      assert.equal(signal.aborted, true)
      wire.state.release(action === 'reconnect' ? new Response('late private denial', { status: 401 }) : undefined)
      for (let i = 0; i < 20; i++) { await Promise.resolve(); await nextTick() }
      assert.equal(m.root.textContent.includes('Late actor'), false)
      if (action === 'cancel') {
        assert.ok(m.root.querySelector('.graph-overview ul').textContent.includes('Initial actor 0'))
        assert.equal(m.root.querySelector('#ingestion-operation').value, uid(77))
        assert.ok(m.root.querySelector('#source-inspector')); assert.equal(m.root.querySelectorAll('.preparation-source-checkbox').length, 1)
      }
      else if (action === 'reconnect') assert.ok(m.root.querySelector('.graph-overview ul').textContent.includes('Refreshed actor 0'))
      else assert.equal(m.root.querySelector('.graph-overview'), null)
      assert.equal(m.root.textContent.includes('late private denial'), false)
    } finally { m.cleanup(); globalThis.fetch = previous }
  })
}

for (const status of [401, 403]) {
  test(`parent refresh HTTP${status} clears all protected graph/source/catalog/recovery state`, async () => {
    const previous = globalThis.fetch, wire = graphTransport(); globalThis.fetch = wire.fetch; shared.setUiLocale('en')
    const m = mount(refreshParent.component)
    try {
      await connectRefresh(m); await inspectRefreshSource(m); wire.state.mode = status
      m.root.querySelector('.graph-refresh').click(); await waitFor(() => !m.root.querySelector('.graph-overview'))
      assert.equal(m.root.querySelector('#source-inspector'), null)
      assert.equal(m.root.querySelector('#ingestion-operation').value, '')
      assert.equal(m.root.querySelectorAll('.preparation-source-checkbox').length, 0)
      assert.equal(m.root.querySelector('#bearer-token').value, '')
      assert.equal(m.root.textContent.includes('private graph traceback'), false)
      assert.ok(m.root.querySelector('.status').textContent.includes(copyFor('en').errors[status === 401 ? 'unauthorized' : 'origin_denied']))
      assert.equal(wire.calls.filter(c => c.url.includes('/ingestion/')).length, 0)
    } finally { m.cleanup(); globalThis.fetch = previous }
  })
}

test('source catalog event carries detached validated metadata only and reset fences a late reload', async () => {
  const row = { ...metadata.source }, data = { schema_version: 1, binary_retained: false, graph_ingestion_executed: false,
    sources: [row], has_more: false, window_limit: 20 }
  const events = [], inspected = []; let finish, loads = 0
  const root = document.createElement('div'); document.body.append(root)
  const props = reactive({ connected: true, busy: false, resetVersion: 0, methods: {
    list: async () => { loads++; if (loads === 1) return data; return new Promise(resolve => { finish = () => resolve(data) }) }
  }, onCatalog: value => events.push(value), onInspected: value => inspected.push(value) })
  const app = createApp({ setup: () => () => h(sources.component, props) }); app.mount(root)
  try {
    assert.equal(loads, 0)
    root.querySelector('.source-actions button').click(); await waitFor(() => events.some(Array.isArray))
    const catalog = events.find(Array.isArray)
    assert.deepEqual(catalog, [row]); assert.notEqual(catalog[0], row)
    assert.deepEqual(Object.keys(catalog[0]).sort(), ['project_id', 'source_revision', 'source_name', 'text_sha256', 'byte_length', 'codepoint_length', 'recorded_at'].sort())
    row.source_name = 'Changed after load'
    assert.equal(catalog[0].source_name, metadata.source.source_name)
    assert.equal(inspected.filter(Boolean).length, 0)
    root.querySelector('.source-actions button').click(); await waitFor(() => finish)
    props.resetVersion++; await nextTick()
    assert.equal(events.at(-1), null)
    const count = events.length; finish()
    for (let i = 0; i < 20; i++) { await Promise.resolve(); await nextTick() }
    assert.equal(events.length, count); assert.equal(root.querySelector('.source-window'), null)
    props.connected = false; await nextTick(); assert.equal(events.at(-1), null)
  } finally { app.unmount(); root.remove() }
})

test('invalid source metadata cannot enter the emitted report catalog', async () => {
  const events = [], root = document.createElement('div'); document.body.append(root)
  const app = createApp({ setup: () => () => h(sources.component, { connected: true, methods: {
    list: async () => ({ schema_version: 1, binary_retained: false, graph_ingestion_executed: false,
      sources: [{ ...metadata.source, content_base64: 'private-binary' }], has_more: false, window_limit: 20 })
  }, onCatalog: value => events.push(value) }) }); app.mount(root)
  try {
    root.querySelector('.source-actions button').click()
    await waitFor(() => root.querySelector('#source-feedback').textContent.includes(copyFor('en').sources.invalidReply))
    assert.equal(events.filter(Array.isArray).length, 0)
    assert.equal(root.querySelector('.source-window'), null)
    assert.equal(root.textContent.includes('private-binary'), false)
  } finally { app.unmount(); root.remove() }
})

test('original picker and research selection persist one locale across remount without starting requests', async () => {
  const previous = globalThis.fetch, calls = []
  globalThis.fetch = (...args) => { calls.push(args); throw new Error('Changing locale must not make requests') }
  shared.setUiLocale('zh'); let m = mount()
  try {
    assert.equal(m.root.querySelector('#workbench-language').value, 'zh')
    m.root.querySelector('.switcher-trigger').click(); await nextTick();
    [...m.root.querySelectorAll('[role="menuitemradio"]')].find(option => option.textContent.trim() === 'Bahasa Melayu').click(); await nextTick()
    assert.equal(m.root.querySelector('#workbench-language').value, 'ms'); assert.equal(m.root.querySelector('.original-title').textContent, 'Sejarah Simulasi')
    assert.ok(m.root.querySelector('.workbench').textContent.includes(copyFor('ms').title))
    assert.equal(document.documentElement.lang, 'ms'); assert.equal(localStorage.getItem('locale'), 'ms'); assert.equal(calls.length, 0)
    m.cleanup(); m = mount()
    assert.equal(m.root.querySelector('#workbench-language').value, 'ms'); assert.equal(m.root.querySelector('.workbench').getAttribute('lang'), 'ms')
    input(m, '#workbench-language', 'en'); await nextTick()
    assert.equal(m.root.querySelector('.switcher-trigger').textContent.trim().replace(/[▲▼]/g, '').trim(), 'English')
    assert.equal(m.root.querySelector('.original-title').textContent, 'Simulation History'); assert.equal(localStorage.getItem('locale'), 'en'); assert.equal(calls.length, 0)
  } finally { m.cleanup(); globalThis.fetch = previous; shared.setUiLocale('en') }
})

test('locale changes preserve held PDF lookup, revision, binary identity and explicit download bytes', async () => {
  const previous = globalThis.fetch, oldCreate = URL.createObjectURL, oldRevoke = URL.revokeObjectURL, oldClick = dom.window.HTMLAnchorElement.prototype.click
  const calls = [], blobs = []; let held, release
  globalThis.fetch = async (url, request) => {
    calls.push({ url, request })
    if (url.includes('/data/')) return reply({ graph_id: 'graph_1', nodes: [], edges: [], node_count: 0, edge_count: 0 })
    if (url.includes('/source/original-metadata/')) return new Promise(resolve => { held = request.signal; release = () => resolve(reply(metadata)) })
    if (url.includes('/source/original/')) return reply({ ...metadata, content_base64: original.toString('base64') })
    throw new Error('Unexpected source route')
  }
  URL.createObjectURL = blob => { blobs.push(blob); return 'blob:retained-pdf' }; URL.revokeObjectURL = () => {}; dom.window.HTMLAnchorElement.prototype.click = () => {}
  shared.setUiLocale('en'); const m = mount()
  try {
    input(m, '#graph-id', 'graph_1'); input(m, '#bearer-token', 'private-token')
    m.root.querySelector('.connection-form').dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true }))
    await waitFor(() => !m.root.querySelector('#source-original-revision').closest('.source-form').querySelector('button').disabled)
    input(m, '#source-original-revision', uid(5)); m.root.querySelector('#source-original-revision').closest('.source-form').querySelector('button').click(); await waitFor(() => held)
    const before = calls.length
    for (const locale of ['ms', 'zh', 'en']) {
      input(m, '#workbench-language', locale); await nextTick()
      assert.equal(held.aborted, false); assert.equal(calls.length, before); assert.equal(m.root.querySelector('#source-original-revision').value, uid(5))
      assert.equal(document.documentElement.lang, locale)
    }
    release(); await waitFor(() => { const buttons = m.root.querySelector('#source-original-revision').closest('.source-form').querySelectorAll('button'); return buttons.length === 2 && !buttons[1].disabled })
    input(m, '#workbench-language', 'ms'); await nextTick()
    const panel = m.root.querySelector('#source-original-revision').closest('.source-form')
    assert.ok(panel.textContent.includes(metadata.binary.sha256)); assert.equal(calls.length, before)
    panel.querySelectorAll('button')[1].click(); await waitFor(() => blobs.length === 1)
    assert.deepEqual(Buffer.from(await blobs[0].arrayBuffer()), original)
    assert.equal(calls.filter(call => call.url.includes('/source/original/')).length, 1)
  } finally { m.cleanup(); release?.(); globalThis.fetch = previous; URL.createObjectURL = oldCreate; URL.revokeObjectURL = oldRevoke; dom.window.HTMLAnchorElement.prototype.click = oldClick; shared.setUiLocale('en') }
})

test('selected retained passage resets review and recovers one derived operation in the workbench', async () => {
  const previous = globalThis.fetch
  const calls = []
  const chunks = ['a'.repeat(16383) + '😀', '猫' + 'b'.repeat(16384)]
  const text = chunks.join('')
  const hash = value => createHash('sha256').update(value).digest('hex')
  const source = { ...metadata.source, byte_length: Buffer.byteLength(text),
    codepoint_length: 32769, text_sha256: hash(text) }
  const passages = [
    { evidence_id: uid(8), start: 0, end: 16384, page: null, excerpt_sha256: hash(chunks[0]) },
    { evidence_id: uid(9), start: 16384, end: 32769, page: null, excerpt_sha256: hash(chunks[1]) }
  ]
  const viewFlags = { schema_version: 1, binary_retained: false, graph_ingestion_executed: false }
  const scope = { schema_version: 1, workspace_id: uid(1), project_id: source.project_id, graph_id: uid(3),
    run_id: null, branch_id: null, layer: 'source' }
  // Fixture computes the installed domain-separated IDs independently of the browser.
  const canonical = value => {
    if (Array.isArray(value)) return value.map(canonical)
    if (value && typeof value === 'object') return Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])]))
    return value
  }
  const { schema_version: scopeVersion, ...groupScope } = scope
  assert.equal(scopeVersion, 1)
  const group = 'mf1_' + hash(JSON.stringify(canonical(groupScope)))
  function urlUuid(name) {
    const bytes = createHash('sha1').update(Buffer.from('6ba7b8119dad11d180b400c04fd430c8', 'hex')).update(name).digest()
    bytes[6] = (bytes[6] & 15) | 80
    bytes[8] = (bytes[8] & 63) | 128
    const hex = bytes.subarray(0, 16).toString('hex')
    return [hex.slice(0, 8), hex.slice(8, 12), hex.slice(12, 16), hex.slice(16, 20), hex.slice(20)].join('-')
  }
  function planReply(payload) {
    const index = passages.findIndex(p => p.evidence_id === payload.evidence_id)
    const { evidence_id, start, end, page, excerpt_sha256 } = passages[index]
    const window = { evidence_id, start, end, page, ordinal: index + 1, count: 2,
      sha256: excerpt_sha256, retained_sha256: source.text_sha256, retained_byte_length: source.byte_length,
      retained_codepoint_length: source.codepoint_length }
    const operation_id = urlUuid('mirofish:retained-window:v1:' + JSON.stringify([
      group, source.source_revision, source.text_sha256, start, end, evidence_id, excerpt_sha256, payload.ontology.revision
    ]))
    const episode_id = urlUuid(`mirofish:episode:v1:${group}:${operation_id}`)
    const evidence_ids = [evidence_id]
    const envelope = { schema_version: 1, source_revision: source.source_revision, source_sha256: excerpt_sha256,
      ontology_revision: payload.ontology.revision, operation_id, source_kind: 'document', content: chunks[index],
      source_name: source.source_name, recorded_at: source.recorded_at, asserted_valid_at: null, evidence_ids }
    const fingerprint = hash(JSON.stringify(canonical({ scope, source: envelope, ontology: payload.ontology })))
    return { schema_version: 2, scope, operation_id, episode_id, fingerprint, evidence_ids, actual_usage_microusd: null,
      graph_ingestion_executed: false, model_calls_made: false, spending_authorized: false, window,
      source_revision: source.source_revision, source_sha256: excerpt_sha256, source_byte_length: Buffer.byteLength(chunks[index]),
      source_codepoint_length: end - start, ontology_revision: payload.ontology.revision, eligibility_codepoint_limit: 32768 }
  }

  let mismatch = false, admitted, submitted
  const ontologyAttributes = Array.from({ length: 6 }, (_, i) => ({
    name: `detail_${i}`, type: 'text', description: `Detail ${i + 1}`
  }))
  const originalOntology = {
    entity_types: [
      { name: 'Person', description: 'A reviewed person.', attributes: ontologyAttributes, examples: ['立花 😀'] },
      { name: 'Archive', description: 'A reviewed archive.', attributes: [ontologyAttributes[0]] }
    ],
    edge_types: [{
      name: 'REFERS_TO', description: 'A reviewed reference.', attributes: [ontologyAttributes[0]],
      source_targets: [{ source: 'Person', target: 'Entity' }]
    }]
  }
  const reviewedDescription = 'The edited project description.'
  const lostDescription = 'The edit whose save response was lost.'
  const eligibleDescription = 'The edited and eligible project description.'
  const projectVersion = description => ({
    ...originalOntology, entity_types: [{ ...originalOntology.entity_types[0], description }, originalOntology.entity_types[1]]
  })
  let projectRevision = 5, savedOntology = originalOntology, ontologyWrites = 0
  let holdOntologyLoad = false, releaseOntologyLoad
  const wholeText = chunks[0]
  const wholeSource = { ...source, source_revision: uid(19), text_sha256: hash(wholeText),
    codepoint_length: passages[0].end, byte_length: Buffer.byteLength(wholeText) }
  const wholePassages = [passages[0]]
  let useWhole = false, savedAdmission, savedSubmission, returnReviewHash = false
  let ineligibleStatus = null
  const conflict = () => new Response(JSON.stringify({ success: false, error: { code: 'conflict' } }), {
    status: 409, headers: { 'Content-Type': 'application/json' }
  })
  function savedPlanReply(payload) {
    const { operation_id, project_revision, source_revision } = payload
    const identity = JSON.stringify(canonical({ scope, project_revision, ontology: savedOntology }))
    const ontology_revision = urlUuid('mirofish:saved-ontology:v4:' + identity)
    const ontology = { schema_version: 1, revision: ontology_revision, ...savedOntology }
    const evidence_ids = wholePassages.map(passage => passage.evidence_id)
    const envelope = { schema_version: 1, source_revision, source_sha256: wholeSource.text_sha256,
      ontology_revision, operation_id, source_kind: 'document', content: wholeText,
      source_name: wholeSource.source_name, recorded_at: wholeSource.recorded_at, asserted_valid_at: null, evidence_ids }
    const requestHash = ontology => hash(JSON.stringify(canonical({ scope, source: envelope, ontology })))
    return { ...payload, scope, episode_id: urlUuid(`mirofish:episode:v1:${group}:${operation_id}`),
      fingerprint: hash(['mirofish:source-ingestion:saved-ontology-plan:v4', identity, requestHash(ontology)].join('\0')),
      execution_fingerprint: requestHash({ ...ontology, saved_ontology: 'project_revision_v4' }),
      evidence_ids, actual_usage_microusd: null, graph_ingestion_executed: false, model_calls_made: false,
      spending_authorized: false, source_sha256: wholeSource.text_sha256, source_byte_length: wholeSource.byte_length,
      source_codepoint_length: wholeSource.codepoint_length, ontology_revision, eligibility_codepoint_limit: 32768 }
  }
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options })
    if (url.includes('/data/')) return reply(projection('Actor', 'Person', 1))
    if (url.includes('/source/library/')) return reply({ ...viewFlags, sources: [useWhole ? wholeSource : source], has_more: false, window_limit: 20 })
    if (url.includes('/source/item/')) return reply({ ...viewFlags, offset_unit: 'unicode_codepoint',
      ...(useWhole ? { source: wholeSource, text: wholeText, passages: wholePassages } : { source, text, passages }) })
    if (url.endsWith('/api/source/ontology/graph_1')) {
      assert.equal(options.headers.Authorization, 'Bearer private-token')
      assert.equal(options.credentials, 'omit')
      assert.equal(options.redirect, 'error')
      const result = () => reply({ schema_version: 1, scope, project_revision: projectRevision, ontology: savedOntology })
      if (options.method === 'GET') {
        assert.equal(options.body, undefined)
        assert.equal(options.headers['Content-Type'], undefined)
        if (holdOntologyLoad) return new Promise(resolve => { releaseOntologyLoad = () => resolve(result()) })
        return result()
      }
      assert.equal(options.method, 'POST')
      assert.equal(options.headers['Content-Type'], 'application/json')
      const payload = JSON.parse(options.body)
      ontologyWrites++
      const description = useWhole ? eligibleDescription : ontologyWrites === 3 ? lostDescription : reviewedDescription
      assert.deepEqual(payload, { schema_version: 1, expected_revision: projectRevision, ontology: projectVersion(description) })
      projectRevision++
      if (ontologyWrites === 1) {
        savedOntology = projectVersion('A concurrent edit.')
        return new Response(JSON.stringify({ success: false, error: { code: 'conflict' } }), {
          status: 409, headers: { 'Content-Type': 'application/json' }
        })
      }
      savedOntology = payload.ontology
      if (ontologyWrites === 3) throw new TypeError('fixture drops the saved ontology reply')
      return result()
    }
    if (url.includes('/ingestion/plan/')) {
      const payload = JSON.parse(options.body)
      if (payload.schema_version === 4) {
        assert.equal(options.headers.Authorization, 'Bearer private-token')
        assert.deepEqual(Object.keys(payload).sort(), ['operation_id', 'project_revision', 'schema_version', 'source_revision'])
        assert.equal(payload.source_revision, wholeSource.source_revision)
        if (payload.project_revision !== projectRevision) return conflict()
        if (ineligibleStatus) return new Response(JSON.stringify({ success: false, error: { code: 'ontology_ineligible' } }), {
          status: ineligibleStatus, headers: { 'Content-Type': 'application/json' }
        })
        savedAdmission = { payload, plan: savedPlanReply(payload) }
        return reply(savedAdmission.plan)
      }
      assert.deepEqual(Object.keys(payload).sort(), ['evidence_id', 'ontology', 'schema_version', 'source_revision'])
      assert.equal(payload.schema_version, 2)
      assert.equal(payload.source_revision, source.source_revision)
      const plan = planReply(payload)
      if (mismatch) return reply({ ...plan, window: { ...plan.window, evidence_id: passages[0].evidence_id } })
      admitted = { payload, plan }
      return reply(plan)
    }
    if (url.includes('/ingestion/execute/')) {
      const payload = JSON.parse(options.body)
      if (payload.schema_version === 4) {
        savedSubmission = payload
        const { fingerprint, execution_fingerprint } = savedAdmission.plan
        assert.deepEqual(payload, { ...savedAdmission.payload, fingerprint, execution_fingerprint })
      } else {
        submitted = payload
        assert.deepEqual(submitted, admitted.payload)
      }
      throw new TypeError('fixture drops the execute reply')
    }
    if (url.includes('/ingestion/operation/')) {
      assert.equal(options.method, 'GET')
      assert.equal(options.body, undefined)
      const plan = useWhole ? savedAdmission.plan : admitted.plan
      const { operation_id, episode_id, evidence_ids } = plan
      const fingerprint = useWhole && !returnReviewHash ? plan.execution_fingerprint : plan.fingerprint
      assert.ok(url.endsWith('/ingestion/operation/graph_1/' + operation_id))
      return reply({ schema_version: 1, scope, operation_id, episode_id, fingerprint, evidence_ids,
        graph_ingestion_executed: true, model_calls_made: null, actual_usage_microusd: null,
        state: 'completed', budget_state: 'settled', ceiling_microusd: 10,
        receipt: { group_id: group, episode_id, fingerprint, evidence_ids } })
    }
    throw new Error('Unexpected passage fixture request')
  }

  shared.setUiLocale('en')
  const m = mount(refreshParent.component)
  const picker = () => m.root.querySelector('#ingestion-passage')
  const review = () => m.root.querySelector('.source-ingestion .plan-review')
  const draft = '.source-ingestion textarea[id^="entity-description-"]'
  const description = 'A person investigating a retained source.'
  const manual = '#ingestion-operation'
  const plan = () => m.root.querySelector('.source-ingestion form').dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true }))
  const callsFor = method => calls.filter(call => call.url.includes('/ingestion/' + method + '/'))
  const control = name => m.root.querySelector('#reviewed-ontology-' + name)
  const projectStatus = () => control('status')?.textContent
  const ontologyWire = () => calls.filter(call => call.url.includes('/source/ontology/')).map(call => call.options.method)
  try {
    await connectRefresh(m)
    await inspectRefreshSource(m, { editEntityName: false })
    await waitFor(() => picker() && !picker().disabled)
    assert.equal(picker().options.length, 3)
    assert.equal(callsFor('plan').length, 0)
    input(m, '#ingestion-passage', passages[0].evidence_id)
    input(m, draft, description)
    plan()
    await waitFor(() => review()?.textContent.includes(admitted?.plan.operation_id) && !picker().disabled)
    assert.equal(admitted.payload.evidence_id, passages[0].evidence_id)
    review().querySelector('input[type="checkbox"]').click()
    await waitFor(() => !review().querySelector('button.primary').disabled)

    input(m, '#ingestion-passage', passages[1].evidence_id)
    await nextTick()
    assert.equal(review(), null)
    assert.equal(m.root.querySelector(draft).value, description)
    assert.equal(m.root.querySelector(manual).value, uid(77))
    assert.equal(callsFor('execute').length, 0)

    control('mode').click()
    await nextTick()
    assert.deepEqual(ontologyWire(), [])
    assert.equal(control('mode').getAttribute('aria-pressed'), 'true')
    assert.equal(control('status').getAttribute('role'), 'status')
    control('load').click()
    await waitFor(() => projectStatus().includes('Loaded project revision 5'))
    assert.equal(m.root.querySelector(draft).value, originalOntology.entity_types[0].description)
    assert.equal(m.root.querySelector('.source-ingestion select[id^="edge-pair-target-"]').value, 'Entity')
    assert.equal(m.root.querySelectorAll('.source-ingestion input[id^="entity-attribute-name-"]').length, 7)
    assert.equal(control('save').disabled, true)
    input(m, draft, reviewedDescription)
    await nextTick()
    control('save').click()
    await waitFor(() => projectStatus().includes('may have changed') && document.activeElement === control('load'))
    assert.equal(m.root.querySelector(draft).value, reviewedDescription)
    assert.equal(control('save').disabled, true)
    assert.deepEqual(ontologyWire(), ['GET', 'POST'])

    control('load').click()
    await waitFor(() => projectStatus().includes('revision 6 differs') && control('keep'))
    assert.equal(m.root.querySelector(draft).value, reviewedDescription)
    assert.equal(control('save').disabled, true)
    control('keep').click()
    await nextTick()
    control('save').click()
    await waitFor(() => projectStatus().includes('Saved project revision 7'))
    assert.equal(m.root.querySelector(draft).value, reviewedDescription)
    assert.equal(control('save').disabled, true)

    control('mode').click()
    await nextTick()
    assert.equal(picker().value, passages[1].evidence_id)
    assert.equal(m.root.querySelector(draft).value, description)
    assert.equal(m.root.querySelector(manual).value, uid(77))
    control('mode').click()
    await nextTick()
    control('load').click()
    await waitFor(() => projectStatus().includes('Loaded project revision 7'))
    assert.equal(m.root.querySelector(draft).value, reviewedDescription)
    assert.deepEqual(ontologyWire(), ['GET', 'POST', 'GET', 'POST', 'GET'])
    control('mode').click()
    await nextTick()

    mismatch = true
    plan()
    await waitFor(() => !picker().disabled && m.root.querySelector('.source-ingestion .feedback').textContent.includes(copyFor('en').ingestion.failed))
    assert.equal(review(), null)
    mismatch = false
    plan()
    await waitFor(() => review()?.textContent.includes(admitted?.plan.operation_id) && !picker().disabled)
    assert.equal(admitted.payload.evidence_id, passages[1].evidence_id)
    assert.equal(review().querySelector('input[type="checkbox"]').checked, false)
    assert.ok(review().querySelector('.passage-review').textContent.includes(passages[1].excerpt_sha256))
    review().querySelector('input[type="checkbox"]').click()
    await waitFor(() => !review().querySelector('button.primary').disabled)
    review().querySelector('button.primary').click()
    const status = () => m.root.querySelector('.source-ingestion .attempt button')
    await waitFor(() => submitted && status() && !status().disabled)
    assert.deepEqual(submitted, admitted.payload)
    assert.equal(picker().disabled, true)
    assert.equal(review().querySelector('button.primary').disabled, true)
    status().click()
    await waitFor(() => m.root.querySelector('.source-ingestion .outcome')?.textContent.includes(admitted.plan.operation_id))
    assert.equal(callsFor('plan').length, 3)
    assert.equal(callsFor('execute').length, 1)
    assert.equal(callsFor('operation').length, 1)

    control('mode').click()
    await nextTick()
    input(m, draft, lostDescription)
    await nextTick()
    control('save').click()
    await waitFor(() => projectStatus().includes('could not be confirmed') && document.activeElement === control('load'))
    assert.equal(m.root.querySelector(draft).value, lostDescription)
    assert.equal(control('save').disabled, true)
    assert.ok(m.root.querySelector('.source-ingestion .attempt').textContent.includes(admitted.plan.operation_id))
    control('load').click()
    await waitFor(() => projectStatus().includes('Saved project revision 8'))
    assert.equal(m.root.querySelector(draft).value, lostDescription)
    assert.equal(picker().value, passages[1].evidence_id)
    assert.ok(m.root.querySelector('.source-ingestion .outcome').textContent.includes(admitted.plan.operation_id))
    assert.deepEqual(ontologyWire(), ['GET', 'POST', 'GET', 'POST', 'GET', 'POST', 'GET'])

    holdOntologyLoad = true
    control('load').click()
    await waitFor(() => releaseOntologyLoad)
    m.root.querySelector('.connection-form button[type="button"]:not([aria-pressed])').click()
    releaseOntologyLoad()
    await waitFor(() => !m.root.querySelector('.graph-refresh'))
    await connectRefresh(m)
    control('mode').click()
    await waitFor(() => projectStatus()?.includes('to start editing'))
    assert.equal(control('save'), null)

    holdOntologyLoad = false
    useWhole = true
    await inspectRefreshSource(m, { editEntityName: false })
    control('mode').click()
    await nextTick()
    control('load').click()
    await waitFor(() => projectStatus().includes('Loaded project revision 8'))
    const savedPlanButton = () => m.root.querySelector('#saved-ingestion-plan')
    const planCount = callsFor('plan').length
    const executeCount = callsFor('execute').length
    input(m, draft, 'An unsaved edit.')
    await nextTick()
    assert.equal(savedPlanButton().disabled, true)
    savedPlanButton().click()
    assert.equal(callsFor('plan').length, planCount)
    input(m, draft, lostDescription)
    await nextTick()
    const ineligible = () => m.root.querySelector('#saved-ingestion-ineligible')
    ineligibleStatus = 409
    savedPlanButton().click()
    await waitFor(() => callsFor('plan').length === planCount + 1 && !savedPlanButton().disabled &&
      m.root.querySelector('.source-ingestion > p.feedback:not(#reviewed-ontology-status)').textContent.includes(copyFor('en').ingestion.failed))
    assert.equal(ineligible(), null)
    ineligibleStatus = 422
    savedPlanButton().click()
    await waitFor(() => ineligible()?.textContent.includes('not eligible for ingestion') && !control('load').disabled)
    assert.equal(review(), null)
    assert.equal(savedPlanButton().disabled, true)
    savedPlanButton().click()
    assert.equal(callsFor('plan').length, planCount + 2)
    control('load').click()
    await waitFor(() => projectStatus().includes('Loaded project revision 8') && !control('load').disabled)
    assert.ok(ineligible())
    assert.equal(savedPlanButton().disabled, true)
    input(m, draft, eligibleDescription)
    await nextTick()
    assert.equal(control('save').disabled, false)
    assert.equal(savedPlanButton().disabled, true)
    control('save').click()
    await waitFor(() => projectStatus().includes('Saved project revision 9') && !savedPlanButton().disabled)
    assert.equal(ineligible(), null)
    ineligibleStatus = null
    savedPlanButton().click()
    await waitFor(() => review()?.textContent.includes(savedAdmission?.plan.execution_fingerprint) && !savedPlanButton().disabled)
    assert.ok(review().textContent.includes(savedAdmission.plan.fingerprint))
    for (const [locale, label] of [['zh', '执行指纹'], ['ms', 'Cap jari pelaksanaan'], ['en', 'Execution fingerprint']]) {
      input(m, '#workbench-language', locale)
      await nextTick()
      assert.ok(review().textContent.includes(label))
      assert.equal(callsFor('plan').length, planCount + 3)
    }
    assert.equal(review().querySelector('button.primary').disabled, true)
    review().querySelector('input[type="checkbox"]').click()
    await waitFor(() => !review().querySelector('button.primary').disabled)
    input(m, draft, 'An edit after review.')
    await nextTick()
    assert.equal(review(), null)
    assert.equal(callsFor('execute').length, executeCount)
    input(m, draft, eligibleDescription)
    projectRevision++
    await nextTick()
    savedPlanButton().click()
    await waitFor(() => projectStatus().includes('may have changed'))
    assert.equal(review(), null)
    assert.equal(savedPlanButton().disabled, true)
    control('load').click()
    await waitFor(() => projectStatus().includes('project revision 10') && !savedPlanButton().disabled)
    savedPlanButton().click()
    await waitFor(() => review()?.textContent.includes(savedAdmission?.plan.operation_id) && !savedPlanButton().disabled)
    assert.equal(review().querySelector('input[type="checkbox"]').checked, false)
    review().querySelector('input[type="checkbox"]').click()
    await waitFor(() => !review().querySelector('button.primary').disabled)
    review().querySelector('button.primary').click()
    await waitFor(() => savedSubmission && status() && !status().disabled)
    assert.equal(callsFor('execute').length, executeCount + 1)
    assert.equal(review().querySelector('button.primary').disabled, true)
    returnReviewHash = true
    const recoveryCount = callsFor('operation').length
    status().click()
    await waitFor(() => callsFor('operation').length === recoveryCount + 1 && !status().disabled &&
      m.root.querySelector('.source-ingestion > p.feedback:not(#reviewed-ontology-status)').textContent.includes(copyFor('en').ingestion.failed))
    assert.equal(m.root.querySelector('.source-ingestion .outcome'), null)
    returnReviewHash = false
    input(m, manual, savedSubmission.operation_id)
    m.root.querySelector('.source-ingestion .manual-recovery').dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true }))
    await waitFor(() => m.root.querySelector('.source-ingestion .outcome')?.textContent.includes(savedSubmission.operation_id))
    assert.equal(callsFor('execute').length, executeCount + 1)
  } finally {
    m.cleanup()
    globalThis.fetch = previous
    shared.setUiLocale('en')
  }
})
