import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { JSDOM } from 'jsdom'
import { parse, compileScript } from '@vue/compiler-sfc'
import { populationCopy, populationCopyFor, populationErrorKey } from '../src/i18n/populationWorkbench.js'
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
const population = await compile('../src/components/workbench/PopulationWorkbench.vue')
const dossier = await compile('../src/components/workbench/DossierExport.vue')
const evidence = await compile('../src/components/workbench/EvidenceResults.vue', { './DossierExport.vue': dossier.url })
const sources = await compile('../src/components/workbench/SourceLibrary.vue')
const ingestion = await compile('../src/components/workbench/SourceIngestion.vue')
const experiments = await compile('../src/components/workbench/ExperimentComparison.vue')
const workbench = await compile('../src/views/ResearchWorkbench.vue', {
  '../components/workbench/PopulationWorkbench.vue': population.url,
  '../components/workbench/EvidenceResults.vue': evidence.url,
  '../components/workbench/SourceLibrary.vue': sources.url,
  '../components/workbench/SourceIngestion.vue': ingestion.url,
  '../components/workbench/ExperimentComparison.vue': experiments.url
})
function mount(component, initial = {}) {
  const props = reactive(initial), root = document.createElement('div'); document.body.append(root)
  const app = createApp({ setup: () => () => h(component, props) })
  app.component('RouterLink', { props: ['to'], setup: (p, { slots }) => () => h('a', { href: p.to }, slots.default?.()) })
  app.mount(root)
  return { root, props, cleanup() { app.unmount(); root.remove() } }
}
async function settle() { for (let i = 0; i < 12; i++) { await Promise.resolve(); await nextTick() } }
function input(root, selector, value) {
  const node = root.querySelector(selector); node.value = value
  node.dispatchEvent(new dom.window.Event(node.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true }))
}
function submit(root) { root.querySelector('.population-workbench form').dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true })) }
const uid = value => `00000000-0000-0000-0000-${String(value).padStart(12, '0')}`
const hostile = '<img src=x onerror=alert(1)>\n<script>secret</script> 中😀 https://evil.invalid/'
function previewFixture(count = 2) {
  const profiles = Array.from({ length: count }, (_, index) => ({ user_id: index, user_name: `actor_${index}_123`, name: index ? `Organization ${index}` : hostile, bio: hostile, persona: `${hostile}\nComplete synthetic persona`, karma: 1000, friend_count: 100, follower_count: 150, statuses_count: 500, age: index ? 30 : 0, gender: index ? 'other' : null, mbti: null, country: null, profession: null, interested_topics: [], source_entity_uuid: uid(index + 1), source_entity_type: index ? 'Organization' : 'Person', created_at: '2026-10-05' }))
  const grounding = Object.fromEntries(profiles.map(p => [p.source_entity_uuid, { source_entity_uuid: p.source_entity_uuid, labels: ['Entity', p.source_entity_type], summary: hostile, attributes: { original: hostile, nested: [1, true, null] }, episode_ids: [uid(80)], evidence_ids: [uid(90)], facts: [{ edge_uuid: uid(20 + p.user_id), direction: 'outgoing', edge_name: '<b>RELATED</b>', source_node_uuid: p.source_entity_uuid, target_node_uuid: uid(999), fact: `${hostile}\n${'Full source line. '.repeat(1000)}`, episode_ids: [uid(81)], evidence_ids: [uid(91)] }] }]))
  return { graph_id: 'graph_1', generator: 'inherited_rule_based_v1', enrichment: 'none', llm_used: false, simulation_executed: false, snapshot_consistent: false, profile_date: '2026-10-05', eligible_count: count, selected_count: count, synthetic_fields: ['user_name', 'bio', 'persona', 'age', 'gender', 'mbti', 'country', 'profession', 'interested_topics', 'karma', 'friend_count', 'follower_count', 'statuses_count'], profiles, grounding }
}
function exportFixture(preview, platform) {
  let raw
  if (platform === 'twitter') {
    const quote = value => /[",\r\n]/.test(value) ? `"${value.replaceAll('"', '""')}"` : value
    raw = [['user_id', 'name', 'username', 'user_char', 'description'], ...preview.profiles.map((p, i) => [String(i), p.name, p.user_name, `${p.bio} ${p.persona}`.replaceAll('\n', ' ').replaceAll('\r', ' '), p.bio.replaceAll('\n', ' ').replaceAll('\r', ' ')])].map(row => row.map(quote).join(',')).join('\r\n') + '\r\n'
  } else raw = JSON.stringify(preview.profiles.map(p => ({ user_id: p.user_id, username: p.user_name, name: p.name, bio: p.bio, persona: p.persona, karma: p.karma, created_at: p.created_at, ...(p.age ? { age: p.age } : {}), ...(p.gender ? { gender: p.gender } : {}) })))
  return { bytes: new TextEncoder().encode(raw), mime: platform === 'twitter' ? 'text/csv; charset=utf-8' : 'application/json; charset=utf-8', filename: '../../evil.html' }
}
function downloadHooks() {
  const originalCreate = URL.createObjectURL, originalRevoke = URL.revokeObjectURL, originalClick = dom.window.HTMLAnchorElement.prototype.click
  const created = [], revoked = [], clicked = []
  URL.createObjectURL = blob => { created.push(blob); return `blob:owned-${created.length}` }
  URL.revokeObjectURL = url => revoked.push(url)
  dom.window.HTMLAnchorElement.prototype.click = function () {
    const allowed = this.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true, cancelable: true }))
    if (allowed) clicked.push({ href: this.href, name: this.download, connected: this.isConnected })
  }
  return { created, revoked, clicked, restore() { URL.createObjectURL = originalCreate; URL.revokeObjectURL = originalRevoke; dom.window.HTMLAnchorElement.prototype.click = originalClick } }
}
test('mounted panel is explicit, disconnected, uses native controls, and makes no mount or locale request', async () => {
  let calls = 0
  const mounted = mount(population.component, { methods: { preview: () => { calls++ }, export: () => { calls++ } }, connected: false, locale: 'en', typeLabels: ['Node', 'Entity', 'Person', 'Person', 'Organization', '<script>'] })
  try {
    assert.ok(mounted.root.querySelector('fieldset').disabled)
    assert.equal(mounted.root.querySelectorAll('.type-choices input[type="checkbox"]').length, 2)
    assert.equal(mounted.root.querySelectorAll('[role="status"],[aria-live]').length, 0)
    assert.equal(mounted.root.querySelector('#population-inspector'), null)
    for (const locale of ['zh', 'ms', 'en']) { mounted.props.locale = locale; await settle(); assert.ok(mounted.root.textContent.includes(populationCopyFor(locale).title)) }
    assert.equal(calls, 0)
  } finally { mounted.cleanup() }
})
test('actual SFC renders complete source text inertly and returns inspector focus after Escape', async () => {
  const producer = previewFixture(), calls = []
  const mounted = mount(population.component, { methods: { preview: async options => { calls.push(options); return producer } }, connected: true, resetVersion: 0, locale: 'en', typeLabels: ['Person', 'Organization'] })
  try {
    submit(mounted.root); await settle(); assert.equal(calls.length, 1)
    producer.grounding[uid(1)].summary = 'mutated after admission'
    const button = mounted.root.querySelector('.profiles button'); button.focus(); button.click(); await settle()
    const inspector = mounted.root.querySelector('#population-inspector')
    assert.equal(document.activeElement, inspector); assert.equal(button.getAttribute('aria-expanded'), 'true')
    assert.ok(inspector.textContent.includes(populationCopy.en.assumptions)); assert.ok(inspector.textContent.includes(populationCopy.en.grounding))
    assert.ok(inspector.textContent.includes(hostile)); assert.ok(inspector.textContent.includes('Full source line. '.repeat(1000)))
    assert.ok(inspector.textContent.includes(uid(90))); assert.ok(inspector.textContent.includes(uid(91)))
    assert.equal(inspector.querySelector('img,script,a,b'), null); assert.equal(inspector.textContent.includes('mutated after admission'), false)
    inspector.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); await settle()
    assert.equal(document.activeElement, button); assert.equal(mounted.root.querySelector('#population-inspector'), null)
  } finally { mounted.cleanup() }
})
test('strict integer form errors stay local; zero and maximum seed are submitted without rounding', async () => {
  const calls = [], mounted = mount(population.component, { methods: { preview: async options => { calls.push(options); return previewFixture() } }, connected: true, typeLabels: ['Person', 'Organization'] })
  try {
    for (const value of ['1.5', '-1', '4294967296', '1e2', ' 0', '']) {
      input(mounted.root, '#population-seed', value); submit(mounted.root); await settle()
      assert.equal(calls.length, 0); assert.equal(mounted.root.querySelector('#population-seed').getAttribute('aria-invalid'), 'true')
      assert.ok(mounted.root.querySelector('.feedback').textContent.includes(populationCopy.en.invalid)); assert.equal(document.activeElement, mounted.root.querySelector('.feedback'))
    }
    for (const value of ['0', '4294967295']) { input(mounted.root, '#population-seed', value); submit(mounted.root); await settle(); assert.equal(calls.at(-1).seed, Number(value)) }
    input(mounted.root, '#population-maximum', '100.1'); submit(mounted.root); await settle(); assert.equal(calls.length, 2)
    assert.equal(mounted.root.querySelector('.twitter-download'), null)
  } finally { mounted.cleanup() }
})
test('Prepare validates exact bytes without downloading; explicit native Save uses fixed names and no new request', async () => {
  const p = previewFixture(), exports = [], hooks = downloadHooks(), feedback = []
  const mounted = mount(population.component, { methods: { preview: async () => p, export: async (platform, options, admitted) => { exports.push({ platform, options }); return exportFixture(admitted, platform) }, feedback: code => feedback.push(code) }, connected: true, resetVersion: 0, typeLabels: ['Person', 'Organization'] })
  try {
    input(mounted.root, '#population-seed', '4294967295'); submit(mounted.root); await settle()
    mounted.root.querySelector('.twitter-download').click(); await settle()
    assert.equal(hooks.clicked.length, 0); assert.equal(feedback.at(-1), 'prepared')
    let link = mounted.root.querySelector('a.save-link')
    assert.equal(link.download, 'oasis-twitter-profiles.csv'); assert.equal(link.hidden, false); assert.equal(link.textContent, populationCopy.en.saveTwitter)
    link.focus(); assert.equal(document.activeElement, link); link.click(); await settle()
    assert.equal(hooks.clicked.length, 1); assert.equal(exports.length, 1); assert.equal(feedback.at(-1), 'requested')
    mounted.root.querySelector('.reddit-download').click(); await settle()
    assert.equal(hooks.clicked.length, 1); assert.equal(feedback.at(-1), 'prepared'); assert.equal(hooks.revoked.length, 1)
    link = mounted.root.querySelector('a.save-link'); assert.equal(link.download, 'oasis-reddit-profiles.json'); assert.equal(link.textContent, populationCopy.en.saveReddit)
    link.click(); await settle(); assert.equal(exports.length, 2)
    assert.deepEqual(exports.map(e => e.platform), ['twitter', 'reddit']); assert.equal(exports[1].options.seed, 4294967295)
    assert.deepEqual(hooks.clicked.map(c => c.name), ['oasis-twitter-profiles.csv', 'oasis-reddit-profiles.json']); assert.ok(hooks.clicked.every(c => c.connected))
    assert.equal(mounted.root.querySelectorAll('a[download]').length, 1); assert.equal(feedback.at(-1), 'requested')
    assert.deepEqual(new Uint8Array(await hooks.created[0].arrayBuffer()), exportFixture(p, 'twitter').bytes)
    input(mounted.root, '#population-maximum', '20'); await settle()
    assert.equal(mounted.root.querySelector('.twitter-download'), null); assert.equal(mounted.root.querySelector('a.save-link'), null); assert.equal(hooks.revoked.length, 2)
    submit(mounted.root); await settle(); mounted.props.resetVersion++; await settle()
    assert.equal(mounted.root.querySelector('.profiles'), null)
  } finally { mounted.cleanup(); hooks.restore() }
})
test('malformed, foreign and divergent exports create no Blob or download', async () => {
  const hooks = downloadHooks()
  try {
    for (const change of [output => { output.mime = 'text/html' }, output => { output.bytes = Uint8Array.of(255) }, output => { output.bytes = new TextEncoder().encode('user_id,name,username,user_char,description\r\n0,foreign,wrong,x,x\r\n') }]) {
      const mounted = mount(population.component, { methods: { preview: async () => previewFixture(), export: async (_platform, _options, p) => { const output = exportFixture(p, 'twitter'); change(output); return output } }, connected: true })
      try { submit(mounted.root); await settle(); mounted.root.querySelector('.twitter-download').click(); await settle(); assert.ok(mounted.root.textContent.includes(populationCopy.en.invalidReply)); assert.equal(mounted.root.querySelector('a.save-link'), null) } finally { mounted.cleanup() }
    }
    assert.equal(hooks.created.length, 0); assert.equal(hooks.clicked.length, 0)
  } finally { hooks.restore() }
})
test('late preview/export replies after option edit, reset, disconnect or unmount are discarded', async () => {
  const hooks = downloadHooks()
  try {
    let resolvePreview
    const mounted = mount(population.component, { methods: { preview: () => new Promise(resolve => { resolvePreview = resolve }) }, connected: true, resetVersion: 0 })
    submit(mounted.root); await settle(); input(mounted.root, '#population-seed', '1'); resolvePreview(previewFixture()); await settle()
    assert.equal(mounted.root.querySelector('.profiles'), null); mounted.cleanup()
    for (const action of ['reset', 'disconnect', 'unmount']) {
      let resolveExport
      const panel = mount(population.component, { methods: { preview: async () => previewFixture(), export: () => new Promise(resolve => { resolveExport = resolve }) }, connected: true, resetVersion: 0 })
      submit(panel.root); await settle(); panel.root.querySelector('.twitter-download').click(); await settle()
      if (action === 'reset') panel.props.resetVersion++
      else if (action === 'disconnect') panel.props.connected = false
      else panel.cleanup()
      await settle(); resolveExport(exportFixture(previewFixture(), 'twitter')); await settle()
      if (action !== 'unmount') { assert.equal(panel.root.querySelector('.profiles'), null); assert.equal(panel.root.querySelector('a.save-link'), null); panel.cleanup() }
    }
    assert.equal(hooks.clicked.length, 0); assert.equal(hooks.created.length, 0)
  } finally { hooks.restore() }
})
test('pagination exposes ten profiles and locale changes retain source text with no refetch', async () => {
  let calls = 0
  const mounted = mount(population.component, { methods: { preview: async () => { calls++; return previewFixture(11) } }, connected: true, locale: 'en' })
  try {
    input(mounted.root, '#population-maximum', '20'); submit(mounted.root); await settle()
    assert.equal(mounted.root.querySelectorAll('.profiles li').length, 10)
    const next = [...mounted.root.querySelectorAll('nav button')].find(button => button.textContent === populationCopy.en.next); next.click(); await settle()
    assert.equal(mounted.root.querySelectorAll('.profiles li').length, 1)
    for (const locale of ['zh', 'ms']) { mounted.props.locale = locale; await settle(); assert.ok(mounted.root.textContent.includes(populationCopyFor(locale).limits)); assert.equal(mounted.root.querySelectorAll('.profiles li').length, 1) }
    assert.equal(calls, 1)
  } finally { mounted.cleanup() }
})
test('all surface copy keys exist in EN/ZH/MS and unknown raw errors select fixed text', () => {
  for (const locale of ['zh', 'ms']) assert.deepEqual(Object.keys(populationCopy[locale]).sort(), Object.keys(populationCopy.en).sort())
  assert.equal(populationErrorKey('raw secret traceback'), 'failed'); assert.equal(populationErrorKey('empty_selection'), 'emptySelection')
})
test('prepared Save link survives waiting; the 1000ms owned cleanup starts only after explicit activation', async () => {
  const hooks = downloadHooks(), calls = []
  const mounted = mount(population.component, { methods: { preview: async () => previewFixture(), export: async (platform, options, p) => { calls.push({ platform, options }); return exportFixture(p, platform) } }, connected: true })
  try {
    submit(mounted.root); await settle(); mounted.root.querySelector('.twitter-download').click(); await settle()
    await new Promise(resolve => setTimeout(resolve, 1050)); await settle()
    assert.equal(hooks.revoked.length, 0); assert.equal(hooks.clicked.length, 0); assert.ok(mounted.root.querySelector('a.save-link'))
    mounted.root.querySelector('a.save-link').click(); await settle()
    assert.equal(hooks.clicked.length, 1); assert.equal(calls.length, 1); assert.ok(mounted.root.textContent.includes(populationCopy.en.requested))
    await new Promise(resolve => setTimeout(resolve, 1050)); await settle()
    assert.deepEqual(hooks.revoked, ['blob:owned-1']); assert.equal(mounted.root.querySelector('a.save-link'), null)
  } finally { mounted.cleanup(); hooks.restore() }
})
test('one prepared URL is replaced and cleared on option edit, reset, disconnect and unmount', async () => {
  const hooks = downloadHooks()
  try {
    for (const action of ['edit', 'reset', 'disconnect', 'unmount']) {
      const mounted = mount(population.component, { methods: { preview: async () => previewFixture(), export: async (platform, _options, p) => exportFixture(p, platform) }, connected: true, resetVersion: 0 })
      submit(mounted.root); await settle(); mounted.root.querySelector('.twitter-download').click(); await settle()
      assert.equal(mounted.root.querySelectorAll('a.save-link').length, 1)
      const previousUrl = mounted.root.querySelector('a.save-link').getAttribute('href')
      mounted.root.querySelector('.reddit-download').click(); await settle()
      assert.ok(hooks.revoked.includes(previousUrl)); assert.equal(mounted.root.querySelectorAll('a.save-link').length, 1)
      const url = mounted.root.querySelector('a.save-link').getAttribute('href')
      if (action === 'edit') input(mounted.root, '#population-seed', '1')
      else if (action === 'reset') mounted.props.resetVersion++
      else if (action === 'disconnect') mounted.props.connected = false
      else mounted.cleanup()
      await settle(); assert.ok(hooks.revoked.includes(url)); assert.equal(mounted.root.querySelector('a.save-link'), null)
      if (action !== 'unmount') mounted.cleanup()
    }
    assert.equal(hooks.clicked.length, 0); assert.equal(hooks.created.length, 8); assert.equal(hooks.revoked.length, 8)
  } finally { hooks.restore() }
})
test('Save labels and prepared feedback follow locale without preparing again; disabled Save prevents default', async () => {
  const hooks = downloadHooks(); let calls = 0
  const mounted = mount(population.component, { methods: { preview: async () => previewFixture(), export: async (platform, _options, p) => { calls++; return exportFixture(p, platform) } }, connected: true, locale: 'en', busy: false })
  try {
    submit(mounted.root); await settle(); mounted.root.querySelector('.reddit-download').click(); await settle()
    for (const locale of ['zh', 'ms']) {
      mounted.props.locale = locale; await settle()
      assert.equal(mounted.root.querySelector('a.save-link').textContent, populationCopyFor(locale).saveReddit)
      assert.ok(mounted.root.querySelector('.feedback').textContent.includes(populationCopyFor(locale).prepared))
    }
    assert.equal(calls, 1); assert.equal(hooks.clicked.length, 0)
    mounted.props.busy = true; await settle(); const link = mounted.root.querySelector('a.save-link')
    assert.equal(link.getAttribute('aria-disabled'), 'true'); assert.equal(link.getAttribute('tabindex'), '-1'); link.click(); await settle(); assert.equal(hooks.clicked.length, 0)
    mounted.props.busy = false; await settle(); link.click(); await settle(); assert.equal(hooks.clicked.length, 1); assert.equal(calls, 1)
  } finally { mounted.cleanup(); hooks.restore() }
})
test('actual parent connects, previews explicitly, forwards status and clears population on malformed 401', async () => {
  const previousFetch = globalThis.fetch, calls = [], p = previewFixture()
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options })
    if (url.includes('/data/')) return new Response(JSON.stringify({ success: true, data: { graph_id: 'graph_1', nodes: [], edges: [], node_count: 0, edge_count: 0 } }), { headers: { 'Content-Type': 'application/json' } })
    if (url.endsWith('/preview')) return new Response(JSON.stringify({ success: true, data: p }), { headers: { 'Content-Type': 'application/json' } })
    return new Response('raw private denial', { status: 401 })
  }
  const mounted = mount(workbench.component)
  try {
    assert.ok(mounted.root.querySelector('.population-workbench fieldset').disabled); assert.equal(calls.length, 0)
    input(mounted.root, '#graph-id', 'graph_1'); input(mounted.root, '#bearer-token', 'private-token')
    mounted.root.querySelector('.connection-form').dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true })); await settle()
    assert.equal(calls.length, 1); submit(mounted.root); await settle()
    assert.equal(calls[1].url, 'http://127.0.0.1:5001/api/graph/population/graph_1/preview')
    assert.ok(mounted.root.querySelector('.connection .status').textContent.includes(populationCopy.en.ready))
    assert.equal(mounted.root.querySelectorAll('.population-workbench [aria-live]').length, 0)
    mounted.root.querySelector('.twitter-download').click(); await settle()
    assert.equal(mounted.root.querySelector('.population-workbench .profiles'), null)
    assert.ok(mounted.root.querySelector('.population-workbench fieldset').disabled)
    assert.equal(mounted.root.querySelector('#bearer-token').value, '')
  } finally { mounted.cleanup(); globalThis.fetch = previousFetch }
})
test('typed selection uses admitted checkbox labels and edits invalidate the admitted preview', async () => {
  const p = previewFixture(); p.profiles.pop(); delete p.grounding[uid(2)]; p.eligible_count = 1; p.selected_count = 1
  const calls = [], mounted = mount(population.component, { methods: { preview: async options => { calls.push(options); return p } }, connected: true, typeLabels: ['Entity', 'Person', 'Organization'] })
  try {
    const person = [...mounted.root.querySelectorAll('.type-choices label')].find(label => label.textContent === 'Person').querySelector('input')
    person.click(); input(mounted.root, '#population-maximum', '1'); submit(mounted.root); await settle()
    assert.deepEqual(calls[0], { max_agents: 1, seed: 0, types: ['Person'] }); assert.equal(mounted.root.querySelectorAll('.profiles li').length, 1)
    person.click(); await settle(); assert.equal(mounted.root.querySelector('.twitter-download'), null)
  } finally { mounted.cleanup() }
})
test('reset suppresses a delayed raw error and denied callbacks clear protected profiles', async () => {
  let rejectRequest
  const pending = mount(population.component, { methods: { preview: () => new Promise((_resolve, reject) => { rejectRequest = reject }) }, connected: true, resetVersion: 0 })
  try {
    submit(pending.root); await settle(); pending.props.resetVersion++; await settle(); rejectRequest(new Error('raw secret traceback')); await settle()
    assert.equal(pending.root.textContent.includes('raw secret traceback'), false); assert.equal(pending.root.querySelector('.profiles'), null)
  } finally { pending.cleanup() }
  const denied = mount(population.component, { methods: { preview: async () => previewFixture(), export: async () => { const error = new Error('private denial'); error.code = 'unauthorized'; throw error } }, connected: true })
  try { submit(denied.root); await settle(); denied.root.querySelector('.twitter-download').click(); await settle(); assert.equal(denied.root.querySelector('.profiles'), null); assert.ok(denied.root.textContent.includes(populationCopy.en.denied)) } finally { denied.cleanup() }
})
test('actual parent replacement cancels delayed population and leaves no stale profile result', async () => {
  const previousFetch = globalThis.fetch; let resolvePreview
  const p = previewFixture(), calls = []
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options })
    if (url.includes('/data/')) return new Response(JSON.stringify({ success: true, data: { graph_id: 'graph_1', nodes: [], edges: [], node_count: 0, edge_count: 0 } }), { headers: { 'Content-Type': 'application/json' } })
    if (url.endsWith('/preview')) return new Promise(resolve => { resolvePreview = resolve })
    return new Response(JSON.stringify({ success: false, error: { code: 'unsupported' } }), { status: 501, headers: { 'Content-Type': 'application/json' } })
  }
  const mounted = mount(workbench.component)
  try {
    input(mounted.root, '#graph-id', 'graph_1'); input(mounted.root, '#bearer-token', 'private-token'); mounted.root.querySelector('.connection-form').dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true })); await settle()
    submit(mounted.root); await settle()
    // Dispatching the already-authorized route form models a replacement action;
    // actual keyboard/browser control behavior remains Main's responsibility.
    input(mounted.root, '#question', 'replacement'); mounted.root.querySelector('.query-section form').dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true })); await settle()
    resolvePreview(new Response(JSON.stringify({ success: true, data: p }), { headers: { 'Content-Type': 'application/json' } })); await settle()
    assert.equal(mounted.root.querySelector('.population-workbench .profiles'), null); assert.equal(calls.length, 3)
    assert.equal(calls[2].url, 'http://127.0.0.1:5001/api/graph/research/graph_1')
  } finally { mounted.cleanup(); globalThis.fetch = previousFetch }
})
