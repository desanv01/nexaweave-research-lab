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
for (const name of ['window', 'document', 'Element', 'HTMLElement', 'SVGElement', 'Node']) globalThis[name] = name === 'window' ? dom.window : dom.window[name]
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
const parent = await compile('../src/views/ResearchWorkbench.vue', { ...replacements, '../i18n/index.js': localeUrl, '../components/workbench/SourceLibrary.vue': sources.url })
const ingestion = await compile('../src/components/workbench/SourceIngestion.vue')
const population = await compile('../src/components/workbench/PopulationWorkbench.vue')
const preparation = await compile('../src/components/workbench/SimulationPreparation.vue')
const refreshParent = await compile('../src/views/ResearchWorkbench.vue', { ...replacements, '../i18n/index.js': localeUrl,
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
async function inspectRefreshSource(m) {
  m.root.querySelector('.source-library .source-actions button').click()
  await waitFor(() => m.root.querySelector('.source-list button') && !m.root.querySelector('.source-list button').disabled)
  m.root.querySelector('.source-list button').click()
  await waitFor(() => m.root.querySelector('#source-inspector') && m.root.querySelector('#ingestion-operation') && !m.root.querySelector('#ingestion-operation').disabled)
  input(m, '#ingestion-operation', uid(77))
  input(m, '.source-ingestion input[id^="entity-name-"]', 'Investigator')
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
