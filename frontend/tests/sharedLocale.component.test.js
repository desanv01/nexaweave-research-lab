// Main executes this suite. Load the real Vite catalog without starting Vite.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { JSDOM } from 'jsdom'
import { parse, compileScript } from '@vue/compiler-sfc'

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://127.0.0.1:5173/' })
for (const name of ['window', 'document', 'Element', 'HTMLElement', 'SVGElement', 'Node']) globalThis[name] = name === 'window' ? dom.window : dom.window[name]
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
function mount() {
  const root = document.createElement('div'); document.body.append(root)
  const app = createApp({ setup: () => () => h('div', [h(picker), h('button', { class: 'after-picker' }, 'After')]) }); app.use(shared.default); app.mount(root)
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
