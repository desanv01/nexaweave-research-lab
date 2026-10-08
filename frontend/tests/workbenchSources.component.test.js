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
const { createApp, h, nextTick } = await import('vue')
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
function mount() {
  const root = document.createElement('div'); document.body.append(root)
  // The picker and original-journey heading consume the same global composer.
  const app = createApp({ setup: () => () => h('div', [h(picker.component), h('h2', { class: 'original-title' }, shared.default.global.t('history.title')), h(parent.component)]) })
  app.use(shared.default); app.component('RouterLink', { props: ['to'], setup: (p, { slots }) => () => h('a', { href: p.to }, slots.default?.()) }); app.mount(root)
  return { root, cleanup() { app.unmount(); root.remove() } }
}
function input(m, selector, value) { const field = m.root.querySelector(selector); field.value = value; field.dispatchEvent(new dom.window.Event(field.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true })) }
async function waitFor(predicate) { const end = Date.now() + 3000; do { await nextTick(); if (predicate()) return; await new Promise(resolve => setImmediate(resolve)) } while (Date.now() < end); assert.ok(predicate(), 'bounded source condition did not complete') }
const uid = n => `00000000-0000-0000-0000-${String(n).padStart(12, '0')}`
const original = Buffer.from('%PDF-1.4\nretained original\n%%EOF\n')
const metadata = { schema_version: 2, binary_retained: true, graph_ingestion_executed: false,
  source: { project_id: uid(2), source_revision: uid(5), source_name: 'Source 猫 😀', text_sha256: 'a'.repeat(64), byte_length: 8, codepoint_length: 3, recorded_at: '2026-10-08T00:00:00Z' },
  binary: { contract_version: 1, project_id: uid(2), source_revision: uid(5), media_type: 'application/pdf', byte_length: original.length, sha256: createHash('sha256').update(original).digest('hex') } }
const reply = data => new Response(JSON.stringify({ success: true, data }), { headers: { 'Content-Type': 'application/json' } })

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
