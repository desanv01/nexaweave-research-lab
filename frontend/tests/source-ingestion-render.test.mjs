// Real SFC module/template regression sources. No worker runtime execution.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { webcrypto } from 'node:crypto'
import { JSDOM } from 'jsdom'
import { parse, compileScript } from '@vue/compiler-sfc'
import { copyFor } from '../src/i18n/workbench.js'
import { ingestionFingerprint, ingestionIdentities } from '../src/api/sourceIngestion.js'
import { sha256 } from '../src/api/sourceLibrary.js'
Object.defineProperty(globalThis, 'crypto', { value: webcrypto, configurable: true })
// Drain the WebCrypto work the plan actually starts before inspecting Vue state.
const pendingDigests = new Set(), actualDigest = webcrypto.subtle.digest.bind(webcrypto.subtle)
webcrypto.subtle.digest = (...args) => {
  const work = actualDigest(...args); pendingDigests.add(work)
  return work.finally(() => pendingDigests.delete(work))
}
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
const ingestion = await compile('../src/components/workbench/SourceIngestion.vue')
function mount(initial) {
  const props = reactive({ connected: true, busy: false, resetVersion: 0, locale: 'en', ...initial }), root = document.createElement('div'); document.body.append(root)
  const app = createApp({ setup: () => () => h(ingestion.component, props) }); app.mount(root)
  return { root, props, cleanup() { app.unmount(); root.remove() } }
}
async function settle() { for (let i = 0; i < 18; i++) { await Promise.resolve(); await nextTick() } }
async function cryptoSettle() {
  const deadline = Date.now() + 2000
  await settle()
  while (pendingDigests.size) {
    const left = deadline - Date.now()
    assert.ok(left > 0, 'bounded WebCrypto admission did not complete')
    let timer
    try {
      await Promise.race([Promise.all([...pendingDigests]), new Promise((_resolve, reject) => {
        timer = setTimeout(() => reject(new Error('bounded WebCrypto admission did not complete')), left)
      })])
    } finally { clearTimeout(timer) }
    await settle()
  }
}
const cp = copyFor('en').ingestion, enc = new TextEncoder()
const project = '22222222-2222-4222-8222-222222222222', revision = '55555555-5555-4555-8555-555555555555', evidence = '77777777-7777-4777-8777-777777777777'
const scope = { schema_version: 1, workspace_id: revision, project_id: project, graph_id: evidence, run_id: null, branch_id: null, layer: 'source' }
async function inspected(text = '<img src=x> 中😀') {
  const digest = await sha256(enc.encode(text))
  return { schema_version: 1, binary_retained: false, graph_ingestion_executed: false, source: { project_id: project, source_revision: revision, source_name: '<script>source</script>', text_sha256: digest, byte_length: enc.encode(text).length, codepoint_length: Array.from(text).length, recorded_at: '2026-10-02T00:00:00Z' }, text, offset_unit: 'unicode_codepoint', passages: [{ evidence_id: evidence, start: 0, end: Array.from(text).length, page: null, excerpt_sha256: digest }] }
}
async function planFor(payload, source) {
  const ids = await ingestionIdentities(scope, payload.operation_id)
  return { schema_version: 1, scope, operation_id: payload.operation_id, episode_id: ids.episode_id, fingerprint: await ingestionFingerprint(scope, source, payload), evidence_ids: [evidence], graph_ingestion_executed: false, model_calls_made: false, actual_usage_microusd: null, source_revision: revision, source_sha256: source.source.text_sha256, source_byte_length: source.source.byte_length, source_codepoint_length: source.source.codepoint_length, ontology_revision: payload.ontology.revision, eligibility_codepoint_limit: 32768, spending_authorized: false }
}
const click = (root, text) => [...root.querySelectorAll('button')].find(b => b.textContent === text)?.click()
function input(root, selector, value) { const el = root.querySelector(selector); el.value = value; el.dispatchEvent(new dom.window.Event(el.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true })) }
function plan(root) { root.querySelector('form').dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true })) }
function acknowledge(root) { const box = root.querySelector('.acknowledgement input'); box.checked = true; box.dispatchEvent(new dom.window.Event('change', { bubbles: true })) }
test('cold inspection eligibility controls literal text and localized nested state keys', async () => {
  for (const locale of ['zh', 'ms']) { const localized = copyFor(locale).ingestion; assert.deepEqual(Object.keys(localized).sort(), Object.keys(cp).sort()); assert.deepEqual(Object.keys(localized.graphStates), Object.keys(cp.graphStates)); assert.deepEqual(Object.keys(localized.budgetStates), Object.keys(cp.budgetStates)); assert.deepEqual(Object.keys(localized.validation), Object.keys(cp.validation)) }
  let plans = 0
  const m = mount({ methods: { plan: async () => { plans++ } } })
  try {
    assert.equal(plans, 0); assert.ok(m.root.textContent.includes(cp.inspectFirst)); assert.ok(m.root.querySelector('fieldset').disabled)
    m.props.inspected = await inspected(); await settle(); assert.equal(plans, 0); assert.equal(m.root.querySelector('fieldset').disabled, false)
    assert.ok(m.root.textContent.includes('<script>source</script>')); assert.equal(m.root.querySelector('script,img,a'), null)
    m.props.inspected = { ...await inspected(), passages: [] }; await settle(); assert.ok(m.root.querySelector('fieldset').disabled); assert.ok(m.root.textContent.includes(cp.ineligible))
    m.props.inspected = await inspected('a'.repeat(32769)); await settle(); assert.ok(m.root.querySelector('fieldset').disabled)
    m.props.locale = 'zh'; await settle(); assert.ok(m.root.textContent.includes(copyFor('zh').ingestion.title)); assert.equal(plans, 0)
  } finally { m.cleanup() }
})
test('editable entity attributes relationships invalidate plan; explicit reviewed single execute freezes snapshot', async () => {
  let plans = 0, posts = 0, plannedPayload, postedPayload
  const source = await inspected(), m = mount({ inspected: source, methods: { plan: async (p, s) => { plans++; plannedPayload = p; return planFor(p, s) }, execute: async (p, known) => { posts++; postedPayload = p; return { operation_id: p.operation_id, state: 'completed', budget_state: 'settled', ceiling_microusd: 100, ...known } } } })
  try {
    click(m.root, cp.addAttribute); await settle(); assert.equal(m.root.querySelectorAll('.attribute').length, 1)
    const attr = m.root.querySelector('.attribute'); input(attr, 'input', 'role'); input(attr, 'textarea', 'Declared role')
    click(m.root, cp.addPair); await settle(); assert.equal(m.root.querySelectorAll('.pair').length, 2)
    const pair = m.root.querySelectorAll('.pair')[1]; input(pair, 'select', 'Organization'); await settle()
    plan(m.root); await cryptoSettle(); assert.equal(plans, 1); assert.equal(posts, 0); assert.ok(m.root.querySelector('.plan-review'))
    input(m.root, '#entity-description-0', 'Edited description'); await settle(); assert.equal(m.root.querySelector('.plan-review'), null)
    plan(m.root); await cryptoSettle(); assert.equal(plans, 2); assert.equal(posts, 0)
    assert.equal(document.activeElement, m.root.querySelector('.plan-review')); assert.ok(m.root.textContent.includes(plannedPayload.operation_id))
    click(m.root, cp.execute); await settle(); assert.equal(posts, 0)
    acknowledge(m.root); await settle(); click(m.root, cp.execute); click(m.root, cp.execute); await settle(); assert.equal(posts, 1); assert.deepEqual(postedPayload, plannedPayload)
    assert.ok(m.root.querySelector('fieldset').disabled); assert.ok(m.root.querySelector('.acknowledgement input').disabled)
    click(m.root, cp.execute); plan(m.root); await settle(); assert.equal(posts, 1); assert.equal(plans, 2)
  } finally { m.cleanup() }
})
test('uncertain execute remains status-only; completed graph and uncertain admission are distinct', async () => {
  let posts = 0, gets = 0, knownPlan
  const m = mount({ inspected: await inspected(), methods: { plan: async (p, s) => knownPlan = await planFor(p, s), execute: async () => { posts++; throw { code: 'outcome_unknown', message: 'secret stack' } }, status: async (p, known) => { gets++; assert.equal(p.operation_id, knownPlan.operation_id); assert.deepEqual(known, knownPlan); return { ...knownPlan, state: 'completed', budget_state: 'uncertain', ceiling_microusd: 300 } } } })
  try {
    plan(m.root); await cryptoSettle(); acknowledge(m.root); await settle(); click(m.root, cp.execute); await settle()
    assert.equal(posts, 1); assert.ok(m.root.textContent.includes(cp.noRetry)); assert.ok(!m.root.textContent.includes('secret stack'))
    assert.equal([...m.root.querySelectorAll('button')].find(b => b.textContent === cp.checkStatus).disabled, false)
    click(m.root, cp.checkStatus); await settle(); assert.equal(gets, 1); assert.equal(posts, 1)
    const outcome = m.root.querySelector('.outcome'); assert.ok(outcome.textContent.includes(cp.graphStates.completed)); assert.ok(outcome.textContent.includes(cp.budgetStates.uncertain)); assert.ok(outcome.textContent.includes(cp.unknown))
    m.props.locale = 'ms'; await settle(); assert.equal(posts, 1); assert.equal(gets, 1); assert.ok(m.root.textContent.includes(copyFor('ms').ingestion.budgetStates.uncertain))
    input(m.root, '#ingestion-operation', knownPlan.operation_id); m.root.querySelector('.manual-recovery').dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true })); await settle(); assert.equal(gets, 2); assert.equal(posts, 1)
  } finally { m.cleanup() }
})
test('late plan/execute/status cannot restore disconnected reset or changed-source state', async () => {
  for (const reset of ['disconnect', 'reset', 'source']) {
    let release, calls = 0
    const source = await inspected(), m = mount({ inspected: source, methods: { plan: async (p, s) => { calls++; return new Promise(resolve => { release = () => planFor(p, s).then(resolve) }) } } })
    try {
      plan(m.root); await settle(); assert.equal(calls, 1)
      if (reset === 'disconnect') m.props.connected = false
      else if (reset === 'reset') m.props.resetVersion++
      else m.props.inspected = await inspected('New source text')
      await settle(); release(); await cryptoSettle(); assert.equal(m.root.querySelector('.plan-review'), null); assert.equal(m.root.querySelector('.attempt'), null); assert.equal(m.root.querySelector('#ingestion-operation').value, '')
    } finally { m.cleanup() }
  }
  for (const method of ['execute', 'status']) {
    let release, known
    const m = mount({ inspected: await inspected(), methods: { plan: async (p, s) => known = await planFor(p, s), execute: method === 'execute' ? async () => new Promise(r => { release = r }) : async () => { throw { code: 'uncertain' } }, status: async () => new Promise(r => { release = r }) } })
    try {
      plan(m.root); await cryptoSettle(); acknowledge(m.root); await settle(); click(m.root, cp.execute); await settle()
      if (method === 'status') { click(m.root, cp.checkStatus); await settle() }
      m.props.resetVersion++; await settle(); release({ ...known, state: 'completed', budget_state: 'settled', ceiling_microusd: 100 }); await settle()
      assert.equal(m.root.querySelector('.outcome'), null); assert.equal(m.root.querySelector('.attempt'), null)
    } finally { m.cleanup() }
  }
})
test('manual recovery does not require a source, never submits, and 404 is unknown', async () => {
  let calls = 0
  const m = mount({ methods: { status: async p => { calls++; assert.equal(p.operation_id, revision); throw { code: 'not_found' } } } })
  try {
    input(m.root, '#ingestion-operation', 'INVALID'); m.root.querySelector('.manual-recovery').dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true })); await settle(); assert.equal(calls, 0)
    input(m.root, '#ingestion-operation', revision); m.root.querySelector('.manual-recovery').dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true })); await settle(); assert.equal(calls, 1); assert.ok(m.root.textContent.includes(cp.notFound)); assert.equal(m.root.querySelector('.selected-source'), null)
    m.props.connected = false; await settle(); assert.equal(m.root.querySelector('#ingestion-operation').value, '')
  } finally { m.cleanup() }
})
test('plan keyboard dismissal returns focus; live status and responsive control styles are authored', async () => {
  const m = mount({ inspected: await inspected(), methods: { plan: planFor } })
  try {
    plan(m.root); await cryptoSettle(); m.root.querySelector('.plan-review').dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); await settle()
    assert.equal(m.root.querySelector('.plan-review'), null); assert.equal(document.activeElement.textContent, cp.plan)
    assert.ok(m.root.querySelector('[role="status"][aria-live="polite"][aria-atomic="true"]'))
    for (const button of m.root.querySelectorAll('button')) assert.ok(['button', 'submit'].includes(button.getAttribute('type')))
    for (const field of m.root.querySelectorAll('input,textarea,select')) assert.ok(field.closest('label'))
  } finally { m.cleanup() }
  const source = readFileSync(new URL('../src/components/workbench/SourceIngestion.vue', import.meta.url), 'utf8')
  for (const rule of ['min-height:44px', ':focus-visible', 'flex-wrap:wrap', 'overflow-wrap:anywhere', 'prefers-reduced-motion']) assert.ok(source.includes(rule))
  for (const forbidden of ['v-html', 'localStorage', 'sessionStorage', 'setInterval', 'clipboard']) assert.ok(!source.includes(forbidden))
})
function assertFieldError(root, id, expected) {
  const field = root.querySelector(`#${id}`)
  assert.equal(field.getAttribute('aria-invalid'), 'true')
  assert.equal(field.getAttribute('aria-describedby'), `${id}-error`)
  assert.equal(root.querySelector(`#${id}-error`).textContent, expected)
  const link = root.querySelector(`.error-summary a[href="#${id}"]`)
  assert.ok(link); assert.ok(link.textContent.includes(expected))
  return { field, link }
}
test('invalid explicit Plan gives localized linked inline errors, keyboard focus and zero requests until corrected', async () => {
  for (const locale of ['en', 'zh', 'ms']) {
    const localized = copyFor(locale).ingestion
    let plans = 0, executes = 0
    const m = mount({ locale, inspected: await inspected(), methods: { plan: async (p, s) => { plans++; return planFor(p, s) }, execute: async () => { executes++ } } })
    try {
      const field = m.root.querySelector('#entity-name-0'); field.focus()
      input(m.root, '#entity-name-0', '1bad'); await settle()
      assert.equal(m.root.querySelector('.error-summary'), null); assert.equal(document.activeElement, field)
      plan(m.root); await settle(); assert.equal(plans, 0); assert.equal(executes, 0)
      const summary = m.root.querySelector('.error-summary')
      assert.equal(document.activeElement, summary); assert.equal(summary.getAttribute('tabindex'), '-1')
      assert.ok(summary.textContent.includes(localized.validationSummary)); assert.ok(summary.textContent.includes(localized.validationHint))
      const error = assertFieldError(m.root, 'entity-name-0', localized.validation.name)
      error.link.focus(); assert.equal(document.activeElement, error.link)
      // Native anchor activation invokes the same click handler for keyboard Enter.
      error.link.click(); await settle(); assert.equal(document.activeElement, field)
      assert.equal(field.value, '1bad')
      input(m.root, '#entity-name-0', 'AnotherName'); await settle()
      assert.equal(document.activeElement, field); assert.equal(plans, 0)
      input(m.root, '#entity-name-0', 'Person'); await settle()
      assert.equal(m.root.querySelector('.error-summary'), null); assert.equal(field.getAttribute('aria-invalid'), null); assert.equal(field.getAttribute('aria-describedby'), null)
      plan(m.root); await cryptoSettle(); assert.equal(plans, 1); assert.equal(executes, 0); assert.ok(m.root.querySelector('.plan-review'))
    } finally { m.cleanup() }
  }
})
test('duplicate and reserved names identify the actual field and keep entered values', async () => {
  for (const [id, invalid, corrected, code] of [['entity-name-1', 'Person', 'Organization', 'duplicate'], ['entity-name-0', 'Entity', 'Person', 'reserved'], ['entity-name-0', 'not allowed', 'Person', 'name']]) {
    let plans = 0
    const m = mount({ inspected: await inspected(), methods: { plan: async (p, s) => { plans++; return planFor(p, s) } } })
    try {
      input(m.root, `#${id}`, invalid); plan(m.root); await settle()
      const { field } = assertFieldError(m.root, id, cp.validation[code]); assert.equal(field.value, invalid); assert.equal(plans, 0)
      input(m.root, `#${id}`, corrected); plan(m.root); await cryptoSettle(); assert.equal(plans, 1)
    } finally { m.cleanup() }
  }
  const m = mount({ inspected: await inspected(), methods: { plan: planFor } })
  try {
    click(m.root, cp.addAttribute); await settle()
    input(m.root, '#entity-attribute-name-0', 'UUID'); input(m.root, '#entity-attribute-description-0', 'Reserved test')
    plan(m.root); await settle(); assertFieldError(m.root, 'entity-attribute-name-0', cp.validation.reserved)
    input(m.root, '#entity-attribute-name-0', 'role'); await settle()
    click(m.root, cp.addAttribute); await settle()
    input(m.root, '#entity-attribute-name-1', 'role'); input(m.root, '#entity-attribute-description-1', 'Second role')
    plan(m.root); await settle(); assertFieldError(m.root, 'entity-attribute-name-1', cp.validation.duplicate)
    // Surviving attributes retain their ID and error linkage after a sibling removal.
    click(m.root, cp.removeAttribute + ' 1'); await settle()
    assert.ok(m.root.querySelector('#entity-attribute-name-1')); assert.equal(m.root.querySelector('#entity-attribute-name-0'), null)
    assert.equal(m.root.querySelector('#entity-attribute-name-1').getAttribute('aria-invalid'), null)
    plan(m.root); await cryptoSettle(); assert.ok(m.root.querySelector('.plan-review'))
  } finally { m.cleanup() }
})
test('renamed or removed entities identify broken pair controls and correction permits Plan without silent reassignment', async () => {
  let plans = 0
  const m = mount({ inspected: await inspected(), methods: { plan: async (p, s) => { plans++; return planFor(p, s) } } })
  try {
    const sourceId = m.root.querySelector('.pair select').id
    const targetId = m.root.querySelectorAll('.pair select')[1].id
    input(m.root, '#entity-name-0', 'Human'); await settle(); plan(m.root); await settle()
    const problem = assertFieldError(m.root, sourceId, cp.validation.pair)
    problem.link.click(); await settle(); assert.equal(document.activeElement.id, sourceId); assert.equal(plans, 0)
    input(m.root, `#${sourceId}`, 'Human'); plan(m.root); await cryptoSettle(); assert.equal(plans, 1)
    click(m.root, cp.removeEntity + ' 2'); await settle(); assert.equal(m.root.querySelector('.plan-review'), null)
    plan(m.root); await settle(); assertFieldError(m.root, targetId, cp.validation.pair); assert.equal(plans, 1)
    input(m.root, `#${targetId}`, 'Human'); plan(m.root); await cryptoSettle(); assert.equal(plans, 2)
    click(m.root, cp.addPair); await settle()
    const duplicateSource = m.root.querySelectorAll('.pair')[1].querySelector('select').id
    plan(m.root); await settle(); assertFieldError(m.root, duplicateSource, cp.validation.duplicatePair); assert.equal(plans, 2)
    click(m.root, cp.removePair + ' 2'); await settle(); plan(m.root); await cryptoSettle(); assert.equal(plans, 3)
  } finally { m.cleanup() }
})
test('description limits count Unicode codepoints and validation summaries clear on reset', async () => {
  let plans = 0
  const m = mount({ inspected: await inspected(), methods: { plan: async (p, s) => { plans++; assert.equal(Array.from(p.ontology.entity_types[0].description).length, 500); return planFor(p, s) } } })
  try {
    input(m.root, '#entity-description-0', '😀'.repeat(501)); plan(m.root); await settle()
    assertFieldError(m.root, 'entity-description-0', cp.validation.description); assert.equal(plans, 0)
    input(m.root, '#entity-description-0', '😀'.repeat(500)); plan(m.root); await cryptoSettle(); assert.equal(plans, 1)
    input(m.root, '#entity-description-0', '   '); plan(m.root); await settle(); assertFieldError(m.root, 'entity-description-0', cp.validation.description); assert.equal(plans, 1)
    m.props.locale = 'zh'; await settle(); assertFieldError(m.root, 'entity-description-0', copyFor('zh').ingestion.validation.description)
    m.props.resetVersion++; await settle(); assert.equal(m.root.querySelector('.error-summary'), null); assert.equal(m.root.querySelector('[aria-invalid="true"]'), null)
    const ids = [...m.root.querySelectorAll('input,textarea,select')].map(el => el.id)
    assert.ok(ids.every(Boolean)); assert.equal(new Set(ids).size, ids.length)
  } finally { m.cleanup() }
})
