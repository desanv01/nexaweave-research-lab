import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { JSDOM } from 'jsdom'
import { parse, compileScript } from '@vue/compiler-sfc'

test('initial ontology details localize live while generated data stays literal', async () => {
  const dom = new JSDOM('<!doctype html><html><body><div id="fixture"></div></body></html>')
  for (const name of ['window', 'document', 'Element', 'HTMLElement', 'SVGElement', 'Node', 'Document', 'ShadowRoot']) {
    globalThis[name] = name === 'window' ? dom.window : dom.window[name]
  }
  const { createApp, h, nextTick } = await import('vue')
  const { createI18n } = await import('vue-i18n')
  const { routerKey } = await import('vue-router')
  const file = new URL('../src/components/Step1GraphBuild.vue', import.meta.url)
  const { descriptor, errors } = parse(readFileSync(file, 'utf8'), { filename: file.pathname })
  assert.deepEqual(errors, [])
  const { content } = compileScript(descriptor, { id: 'step1-ontology', inlineTemplate: true, genDefaultAs: '__component' })
  const forbidden = 'data:text/javascript,export function createSimulation(){throw new Error("unexpected simulation creation")}'
  const code = content.replace(/from (['"])([^'"]+)\1/g, (_match, _quote, specifier) => {
    const url = specifier === '../api/simulation' ? forbidden :
      specifier.startsWith('.') ? new URL(specifier, file).href : import.meta.resolve(specifier)
    return 'from ' + JSON.stringify(url)
  })
  const moduleUrl = 'data:text/javascript;base64,' + Buffer.from(code + '\nexport default __component').toString('base64')
  const { default: Step1GraphBuild } = await import(moduleUrl)
  const messages = Object.fromEntries(['en', 'zh', 'ms'].map(locale =>
    [locale, JSON.parse(readFileSync(new URL(`../../locales/${locale}.json`, import.meta.url), 'utf8'))]))
  const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages })
  const entity = { name: '<b>人 😀</b>', description: 'Literal entity description 中文',
    attributes: [{ name: 'Stored field', type: 'text', description: 'Literal field description' }],
    examples: ['<img src=x> example'] }
  const relation = { name: '<em>RELATES_TO</em>', description: 'Literal relation description Melayu',
    source_targets: [{ source: entity.name, target: 'Literal endpoint' }] }
  const projectData = { project_id: 'literal-project', ontology: { entity_types: [entity], edge_types: [relation] } }
  const root = document.querySelector('#fixture')
  const app = createApp({ render: () => h(Step1GraphBuild, { projectData, currentPhase: 1 }) })
  app.use(i18n)
  app.provide(routerKey, { push: () => assert.fail('unexpected navigation') })
  app.mount(root)

  const expected = {
    en: ['ENTITY', 'RELATION', 'ATTRIBUTES', 'EXAMPLES', 'CONNECTIONS',
      'GENERATED ENTITY TYPES', 'GENERATED RELATION TYPES', 'SYSTEM DASHBOARD', 'Close ontology details'],
    zh: ['实体', '关系', '属性', '示例', '关联',
      '生成的实体类型', '生成的关系类型', '系统仪表板', '关闭本体详情'],
    ms: ['ENTITI', 'HUBUNGAN', 'ATRIBUT', 'CONTOH', 'SAMBUNGAN',
      'JENIS ENTITI YANG DIJANA', 'JENIS HUBUNGAN YANG DIJANA', 'PAPAN PEMUKA SISTEM', 'Tutup butiran ontologi']
  }
  const allText = selector => [...root.querySelectorAll(selector)].map(node => node.textContent.trim())
  try {
    for (const [index, item] of [entity, relation].entries()) {
      root.querySelectorAll('.entity-tag.clickable')[index].click()
      await nextTick()
      const overlay = root.querySelector('.ontology-detail-overlay')
      assert.ok(overlay)
      for (const [locale, labels] of Object.entries(expected)) {
        i18n.global.locale.value = locale
        await nextTick()
        assert.equal(root.querySelector('.ontology-detail-overlay'), overlay)
        assert.deepEqual(allText('.detail-type-badge'), [labels[index]])
        assert.deepEqual(allText('.section-label'), index === 0 ? labels.slice(2, 4) : [labels[4]])
        assert.deepEqual(allText('.tag-label'), labels.slice(5, 7))
        assert.deepEqual(allText('.log-title'), [labels[7]])
        assert.deepEqual(allText('.detail-name'), [item.name])
        assert.deepEqual(allText('.detail-desc'), [item.description])
        assert.deepEqual(allText('.entity-tag.clickable'), [entity.name, relation.name])
        if (index === 0) {
          assert.deepEqual(allText('.attr-name'), ['Stored field'])
          assert.deepEqual(allText('.attr-type'), ['(text)'])
          assert.deepEqual(allText('.attr-desc'), ['Literal field description'])
          assert.deepEqual(allText('.example-tag'), entity.examples)
        } else assert.deepEqual(allText('.conn-node'), [entity.name, 'Literal endpoint'])
        assert.deepEqual(allText('.api-note'), [
          'POST /api/graph/ontology/generate', 'POST /api/graph/build', 'POST /api/simulation/create'
        ])
        assert.equal(overlay.querySelector('img, b, em'), null)
        const close = overlay.querySelector('.close-btn')
        assert.ok(close instanceof dom.window.HTMLButtonElement)
        assert.equal(close.getAttribute('aria-label'), labels[8])
        assert.equal(close.tabIndex, 0)
        close.focus()
        assert.equal(document.activeElement, close)
      }
      // Native click activation is covered here. JSDOM does not supply keyboard defaults.
      overlay.querySelector('.close-btn').click()
      await nextTick()
      assert.equal(root.querySelector('.ontology-detail-overlay'), null)
    }
  } finally {
    app.unmount()
    dom.window.close()
  }
})
