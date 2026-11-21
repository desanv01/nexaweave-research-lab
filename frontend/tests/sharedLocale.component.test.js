// Main executes this suite. Load the real Vite catalog without starting Vite.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { JSDOM } from 'jsdom'
import { parse, compileScript } from '@vue/compiler-sfc'

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://127.0.0.1:5173/' })
for (const name of ['window', 'document', 'Element', 'HTMLElement', 'SVGElement', 'Node', 'Document', 'ShadowRoot']) globalThis[name] = name === 'window' ? dom.window : dom.window[name]
globalThis.localStorage = dom.window.localStorage
const { createApp, h, nextTick } = await import('vue')
const dataModule = code => `data:text/javascript;base64,${Buffer.from(code).toString('base64')}`
const catalog = Object.fromEntries(['en', 'zh', 'ms'].map(key => [key, JSON.parse(readFileSync(new URL(`../../locales/${key}.json`, import.meta.url), 'utf8'))]))
const languages = JSON.parse(readFileSync(new URL('../../locales/languages.json', import.meta.url), 'utf8'))
let localeSource = readFileSync(new URL('../src/i18n/index.js', import.meta.url), 'utf8')
localeSource = localeSource.replace(/import languages from [^\n]+/, `const languages = ${JSON.stringify(languages)}`)
localeSource = localeSource.replace(/import\.meta\.glob\([^\n]+\)/, JSON.stringify(Object.fromEntries(Object.entries(catalog).map(([key, value]) => [`../../../locales/${key}.json`, { default: value }]))))
localeSource = localeSource.replace(/from (['"])(vue|vue-i18n)\1/g, (_m, _q, name) => `from ${JSON.stringify(import.meta.resolve(name))}`)
const localeUrl = dataModule(localeSource), shared = await import(localeUrl)
const file = new URL('../src/components/LanguageSwitcher.vue', import.meta.url)
const { descriptor } = parse(readFileSync(file, 'utf8'), { filename: file.pathname })
let pickerSource = compileScript(descriptor, { id: 'locale-picker', inlineTemplate: true, genDefaultAs: '__component' }).content
pickerSource = pickerSource.replace(/from (['"])vue\1/g, `from ${JSON.stringify(import.meta.resolve('vue'))}`).replace(/from (['"])@\/i18n\/index\.js\1/g, `from ${JSON.stringify(localeUrl)}`)
const picker = (await import(dataModule(pickerSource + '\nexport default __component'))).default
function mount(component, props) {
  const root = document.createElement('div'); document.body.append(root)
  const app = createApp({
    setup: () => () => h('div', [h(picker), h('button', { class: 'after-picker' }, 'After'),
      ...(component ? [h(component, props)] : [])])
  })
  app.use(shared.default); app.mount(root)
  return { root, cleanup() { app.unmount(); root.remove() } }
}
const key = (element, value) => element.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: value, bubbles: true, cancelable: true }))

test('Malay catalog preserves every EN/ZH message shape and interpolation contract', () => {
  const leaves = (value, prefix = '') => Object.fromEntries(Object.entries(value).flatMap(([name, child]) => {
    const path = prefix ? `${prefix}.${name}` : name
    return typeof child === 'string' ? [[path, child]] : Object.entries(leaves(child, path))
  }))
  const en = leaves(catalog.en), zh = leaves(catalog.zh), ms = leaves(catalog.ms)
  assert.deepEqual(Object.keys(ms).sort(), Object.keys(en).sort()); assert.deepEqual(Object.keys(ms).sort(), Object.keys(zh).sort())
  const placeholders = text => [...text.matchAll(/\{([^{}]+)\}/g)].map(m => m[1]).sort()
  for (const [path, text] of Object.entries(ms)) {
    assert.ok(text.trim(), path)
    assert.deepEqual(placeholders(text), placeholders(en[path]), path)
    assert.deepEqual(placeholders(text), placeholders(zh[path]), path)
    assert.equal(text.split('|').length, en[path].split('|').length, path)
    assert.deepEqual([...text.matchAll(/@(?:\.[\w]+)?:[\w.]+/g)].map(m => m[0]), [...en[path].matchAll(/@(?:\.[\w]+)?:[\w.]+/g)].map(m => m[0]), path)
  }
  assert.equal(catalog.ms.main.stepNames.length, 5)
  assert.equal(catalog.ms.step5.submitSurvey, 'Hantar Tinjauan')
  assert.equal(catalog.ms.graph.refreshGraph, 'Segarkan Graf')
  assert.ok(catalog.ms.api.graphBuilding.includes('force: true'))
  assert.ok(catalog.ms.report.sectionNoPrefix.includes('Final Answer:'))
  assert.ok(catalog.ms.report.sectionSaved.includes('{reportId}/section_{sectionNum}.md'))
  assert.equal(languages.ms.label, 'Bahasa Melayu'); assert.ok(languages.es && languages.fr && languages.pt && languages.ru && languages.de)
})

test('supported saved locale survives a new controller and invalid saved values use the supported default', () => {
  for (const saved of ['en', 'zh', 'ms', 'bogus', '__proto__', 'es', '', null]) {
    const values = new Map([['locale', saved]]), storage = { getItem: key => values.get(key), setItem: (key, value) => values.set(key, value) }
    const controller = shared.createLocaleController({ catalog, storage: () => storage, document: () => document })
    try {
      const expected = ['en', 'zh', 'ms'].includes(saved) ? saved : 'zh'
      assert.equal(controller.locale.value, expected); assert.equal(document.documentElement.lang, expected)
      assert.equal(controller.setLocale('ms'), true); assert.equal(values.get('locale'), 'ms')
      assert.equal(controller.setLocale('unsupported'), false); assert.equal(controller.locale.value, 'ms')
      const reloaded = shared.createLocaleController({ catalog, storage: () => storage, document: () => document })
      try { assert.equal(reloaded.i18n.global.t('step5.submitSurvey'), 'Hantar Tinjauan') } finally { reloaded.dispose() }
    } finally { controller.dispose() }
  }
})

test('denied storage access or writes preserve reactive selection and document language', () => {
  for (const storage of [() => { throw new Error('Storage refused') }, () => ({ getItem: () => { throw new Error('Read refused') }, setItem: () => { throw new Error('Write refused') } }), () => ({ getItem: () => 'en', setItem: () => { throw new Error('Quota') } })]) {
    const controller = shared.createLocaleController({ catalog, storage, document: () => document })
    try {
      controller.locale.value = 'ms'
      assert.equal(controller.i18n.global.t('common.language'), 'Bahasa'); assert.equal(document.documentElement.lang, 'ms')
      // Existing original components also assign the global composer locale.
      controller.i18n.global.locale.value = 'zh'
      assert.equal(controller.locale.value, 'zh'); assert.equal(document.documentElement.lang, 'zh')
    } finally { controller.dispose() }
  }
})

test('keyboard language selection wraps, selects, closes and restores focus; Tab can leave', async () => {
  shared.setUiLocale('en'); const m = mount()
  try {
    const trigger = m.root.querySelector('.switcher-trigger'); trigger.focus(); key(trigger, 'ArrowDown'); await nextTick()
    let options = [...m.root.querySelectorAll('[role="menuitemradio"]')]
    assert.equal(trigger.getAttribute('aria-expanded'), 'true'); assert.equal(document.activeElement.getAttribute('aria-checked'), 'true')
    key(document.activeElement, 'End'); assert.equal(document.activeElement, options.at(-1))
    key(document.activeElement, 'ArrowDown'); assert.equal(document.activeElement, options[0])
    key(document.activeElement, 'ArrowUp'); assert.equal(document.activeElement, options.at(-1))
    key(document.activeElement, 'Home'); assert.equal(document.activeElement, options[0])
    const malay = options.find(option => option.textContent.trim() === 'Bahasa Melayu'); malay.focus(); malay.click(); await nextTick()
    assert.equal(shared.default.global.locale.value, 'ms'); assert.equal(localStorage.getItem('locale'), 'ms'); assert.equal(document.documentElement.lang, 'ms')
    assert.equal(trigger.getAttribute('aria-label'), 'Bahasa'); assert.equal(document.activeElement, trigger); assert.equal(m.root.querySelector('[role="menu"]'), null)
    key(trigger, 'ArrowUp'); await nextTick(); key(document.activeElement, 'Escape'); await nextTick()
    assert.equal(document.activeElement, trigger); assert.equal(trigger.getAttribute('aria-expanded'), 'false')
    trigger.click(); await nextTick(); m.root.querySelector('.after-picker').focus(); await nextTick()
    assert.equal(m.root.querySelector('[role="menu"]'), null); assert.equal(document.activeElement, m.root.querySelector('.after-picker'))
  } finally { m.cleanup(); shared.setUiLocale('en') }
})

test('report progress and open controls follow EN ZH MS without another report request', async () => {
  const consoleLine = 'Literal console output: 雨 😀'
  const fixtureUrl = dataModule([
    'export const requests = [], routes = [], markdown = [], intervals = new Map()',
    'export let finishAgentLog',
    'let nextTimer = 0',
    'export const useRouter = () => ({ push: route => routes.push(route) })',
    'export function getAgentLog(...args) {',
    "  requests.push(['agent', ...args])",
    '  return new Promise(resolve => { finishAgentLog = resolve })',
    '}',
    'export async function getConsoleLog(...args) {',
    "  requests.push(['console', ...args])",
    '  return { success: true, data: { from_line: 0, logs: [' + JSON.stringify(consoleLine) + '] } }',
    '}',
    'export function renderSafeMarkdown(content) {',
    '  markdown.push(content)',
    '  return "<p>Rendered fixture report</p>"',
    '}',
    'export function startInterval(_tick, delay) { const id = ++nextTimer; intervals.set(id, delay); return id }',
    'export function stopInterval(id) { intervals.delete(id) }'
  ].join('\n'))
  const fixture = await import(fixtureUrl)
  // Render the timeline rows in JSDOM without running CSS transitions.
  const vueUrl = dataModule('export * from ' + JSON.stringify(import.meta.resolve('vue')) + '\n' +
    'export const TransitionGroup = { inheritAttrs: false, setup(_, { slots }) { return () => slots.default?.() } }')
  const reportFile = new URL('../src/components/Step4Report.vue', import.meta.url)
  const { descriptor: reportDescriptor } = parse(readFileSync(reportFile, 'utf8'), { filename: reportFile.pathname })
  const imports = {
    vue: vueUrl,
    'vue-i18n': import.meta.resolve('vue-i18n'),
    'vue-router': fixtureUrl,
    '../api/report': fixtureUrl,
    '../utils/safeMarkdown': fixtureUrl,
    '../i18n/reportProgress.js': new URL('../i18n/reportProgress.js', reportFile).href
  }
  const compiled = compileScript(reportDescriptor, { id: 'report-progress', inlineTemplate: true, genDefaultAs: '__component' })
  const reportSource = compiled.content.replace(/from (['"])([^'"]+)\1/g, (original, _quote, specifier) =>
    Object.hasOwn(imports, specifier) ? 'from ' + JSON.stringify(imports[specifier]) : original)
  const timerImport = 'import { startInterval as setInterval, stopInterval as clearInterval } from ' + JSON.stringify(fixtureUrl)
  const report = await import(dataModule(timerImport + '\n' + reportSource + '\nexport default __component'))

  const reportId = 'report-retained-01'
  const title = 'Operator title: 雨'
  const sectionTitle = 'Manual section: Bukti'
  const markdown = '**Retained evidence** 猫'
  const raw = '{"literal":"雨","tool":"fixture_tool"}'
  const response = 'Literal model response 😀'
  const parameters = { query: 'Manual query 雨' }
  const requests = [['agent', reportId, 0], ['console', reportId, 0]]
  const logs = [
    { action: 'report_start', details: { simulation_id: 'sim-retained-01' } },
    { action: 'planning_complete', details: { outline: { title, summary: 'Operator summary', sections: [{ title: sectionTitle }] } } },
    { action: 'section_start', section_index: 1, section_title: sectionTitle },
    { action: 'tool_call', details: { tool_name: 'fixture_tool', parameters } },
    { action: 'tool_result', details: { tool_name: 'fixture_tool', result: raw, result_length: raw.length } },
    { action: 'llm_response', details: { response } },
    { action: 'section_complete', section_index: 1, section_title: sectionTitle, details: { content: markdown } },
    { action: 'report_complete', elapsed_seconds: 65 }
  ].map((log, index) => ({ ...log, timestamp: new Date(Date.UTC(2026, 0, 1, 0, 0, index)).toISOString() }))

  shared.setUiLocale('en')
  const m = mount(report.default, { reportId, simulationId: 'sim-retained-01' })
  try {
    const panel = m.root.querySelector('.report-panel')
    const text = selector => {
      const node = panel.querySelector(selector)
      assert.ok(node, selector)
      return node.textContent.trim()
    }
    assert.equal(text('.waiting-text'), 'Waiting for Report Agent...')
    assert.deepEqual(fixture.requests, requests)
    assert.deepEqual([...fixture.intervals.values()], [2000, 1500])
    fixture.finishAgentLog({ success: true, data: { from_line: 0, logs } })
    await Promise.resolve()
    await nextTick()
    assert.equal(fixture.intervals.size, 0)

    const buttons = [...panel.querySelectorAll('.timeline-footer .action-btn')]
    assert.deepEqual(buttons.map(button => button.textContent.trim()), ['Show Params', 'Raw Output', 'Show Response'])
    const steps = [...panel.querySelectorAll('.wf-step')]
    assert.equal(steps.length, 3)
    for (const button of buttons) button.click()
    await nextTick()

    const expected = {
      en: { badge: 'Prediction Report', id: 'ID:', metrics: ['Sections', 'Elapsed', 'Tools'], elapsed: '1m 5s',
        status: 'Completed', planning: 'Planning / Outline', done: 'Report Generation Complete',
        buttons: ['Hide Params', 'Structured View', 'Hide Response'], console: 'CONSOLE OUTPUT' },
      zh: { badge: '预测报告', id: '编号：', metrics: ['章节', '用时', '工具'], elapsed: '1分5秒',
        status: '已完成', planning: '规划／提纲', done: '报告生成完成',
        buttons: ['隐藏参数', '结构化视图', '隐藏响应'], console: '控制台输出' },
      ms: { badge: 'Laporan Ramalan', id: 'ID:', metrics: ['Bahagian', 'Tempoh', 'Alat'], elapsed: '1 min 5s',
        status: 'Selesai', planning: 'Perancangan / Rangka', done: 'Penjanaan Laporan Selesai',
        buttons: ['Sembunyikan parameter', 'Paparan berstruktur', 'Sembunyikan respons'], console: 'OUTPUT KONSOL' }
    }
    for (const [locale, copy] of Object.entries(expected)) {
      assert.equal(shared.setUiLocale(locale), true)
      await nextTick()
      assert.equal(text('.report-tag'), copy.badge)
      assert.equal(text('.report-id'), copy.id + ' ' + reportId)
      assert.deepEqual([...panel.querySelectorAll('.metric-label')].map(node => node.textContent.trim()), copy.metrics)
      assert.equal(text('.metric:nth-child(2) .metric-value'), copy.elapsed)
      assert.equal(text('.metric-pill'), copy.status)
      assert.equal(text('.wf-step-title'), copy.planning)
      assert.equal(text('.complete-banner'), copy.done)
      assert.equal(text('.log-title'), copy.console)
      assert.equal(text('.log-id'), reportId)
      assert.equal(text('.log-msg'), consoleLine)
      assert.equal(text('.main-title'), title)
      assert.equal(text('.section-title'), sectionTitle)
      assert.equal(text('.generated-content'), 'Rendered fixture report')
      assert.equal(text('.result-tool'), 'fixture_tool')
      assert.equal(text('.tool-params pre'), JSON.stringify(parameters, null, 2))
      assert.equal(text('.result-raw pre'), raw)
      assert.equal(text('.llm-content pre'), response)
      assert.deepEqual(buttons.map(button => button.textContent.trim()), copy.buttons)
      for (const [index, step] of [...panel.querySelectorAll('.wf-step')].entries()) assert.equal(step, steps[index])
      assert.deepEqual(fixture.requests, requests)
    }
    assert.ok(fixture.markdown.length && fixture.markdown.every(value => value === markdown))
    panel.querySelector('.next-step-btn').click()
    assert.deepEqual(fixture.routes, [{ name: 'Interaction', params: { reportId } }])
  } finally {
    m.cleanup()
    shared.setUiLocale('en')
  }
})
