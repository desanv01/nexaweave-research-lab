import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { setImmediate } from 'node:timers/promises'
import { compileScript, parse } from '@vue/compiler-sfc'
import { JSDOM } from 'jsdom'
import { guidedSurveyCopyFor } from '../src/i18n/guidedSurvey.js'

const dataModule = source => `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`

const simulationApi = dataModule(`
  export const getEnvStatus = request => globalThis.__guidedSurveyTestApi.status(request)
  export const interviewAgents = request => globalThis.__guidedSurveyTestApi.interview(request)
  export const getSimulationProfilesRealtime = id => globalThis.__guidedSurveyTestApi.profiles(id)
`)
const reportApi = dataModule(`
  const unexpected = () => { throw new Error('Unexpected report request') }
  export { unexpected as chatWithReport, unexpected as getReport, unexpected as getAgentLog }
`)

const sfc = new URL('../src/components/Step5Interaction.vue', import.meta.url)
const { descriptor } = parse(readFileSync(sfc, 'utf8'), { filename: sfc.pathname })
const compiled = compileScript(descriptor, { id: 'guided-survey', inlineTemplate: true, genDefaultAs: '__component' })
const locations = {
  vue: import.meta.resolve('vue'),
  'vue-i18n': import.meta.resolve('vue-i18n'),
  '../api/report': reportApi,
  '../api/simulation': simulationApi,
  '../utils/safeMarkdown': new URL('../utils/safeMarkdown.js', sfc).href,
  '../utils/surveyResults.js': new URL('../utils/surveyResults.js', sfc).href,
  '../i18n/guidedSurvey.js': new URL('../i18n/guidedSurvey.js', sfc).href
}
const code = compiled.content.replace(/from (['"])([^'"]+)\1/g, (match, _quote, specifier) =>
  Object.hasOwn(locations, specifier) ? `from ${JSON.stringify(locations[specifier])}` : match
)
const messages = Object.fromEntries(['en', 'zh', 'ms'].map(locale => [
  locale, JSON.parse(readFileSync(new URL(`../../locales/${locale}.json`, import.meta.url), 'utf8'))
]))

test('guided survey renders and exports the submitted actors and platform replies, with stale result fencing', async t => {
  const dom = new JSDOM('<div id="app"></div>', { url: 'http://127.0.0.1:5173/' })
  const previous = new Map()
  for (const name of ['window', 'document', 'Element', 'HTMLElement', 'SVGElement', 'Node', 'Document', 'ShadowRoot']) {
    previous.set(name, Object.getOwnPropertyDescriptor(globalThis, name))
    Object.defineProperty(globalThis, name, { configurable: true, writable: true, value: name === 'window' ? dom.window : dom.window[name] })
  }
  t.after(() => {
    delete globalThis.__guidedSurveyTestApi
    for (const [name, descriptor] of previous) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor)
      else delete globalThis[name]
    }
    dom.window.close()
  })

  const requests = []
  const profileRequests = []
  const statusRequests = []
  const statusResponse = (id, twitter, reddit) => ({
    success: true,
    data: { simulation_id: id, env_alive: true, twitter_available: twitter, reddit_available: reddit }
  })
  const liveStatus = id => statusResponse(id, true, id === 'sim_paired')
  let statusFor = liveStatus
  let finishInterview
  globalThis.__guidedSurveyTestApi = {
    async status(request) {
      statusRequests.push(request)
      return statusFor(request.simulation_id)
    },
    interview(request) {
      requests.push(request)
      return new Promise(resolve => { finishInterview = resolve })
    },
    async profiles(id) {
      profileRequests.push(id)
      const profiles = id === 'sim_paired'
        ? [{ username: 'Amina', profession: 'Planner' }, { username: 'Chen' }, { username: 'Devi' }]
        : [{ username: 'Eli', profession: 'Writer' }]
      return { success: true, data: { simulation_id: id, platform: 'reddit', profiles } }
    }
  }

  const blobs = []
  const downloads = []
  const revoked = []
  t.mock.method(URL, 'createObjectURL', blob => {
    blobs.push(blob)
    return `blob:survey-${blobs.length}`
  })
  t.mock.method(URL, 'revokeObjectURL', url => revoked.push(url))
  t.mock.method(dom.window.HTMLAnchorElement.prototype, 'click', function () {
    downloads.push({ name: this.download, href: this.href, connected: this.isConnected })
  })

  const { createApp, h, nextTick, reactive } = await import('vue')
  const { createI18n } = await import('vue-i18n')
  const component = (await import(dataModule(`${code}\nexport default __component`))).default
  const { MAX_SURVEY_EXPORT_BYTES, serializeSurveyResults } = await import('../src/utils/surveyResults.js')
  const props = reactive({ simulationId: 'sim_paired' })
  const i18n = createI18n({ legacy: false, locale: 'en', messages })
  const root = document.querySelector('#app')
  const app = createApp({ render: () => h(component, props) }).use(i18n)
  app.mount(root)
  try {
    const click = selector => root.querySelector(selector).click()
    const settle = async () => {
      await setImmediate()
      await nextTick()
    }
    const ask = async question => {
      const input = root.querySelector('.survey-input')
      input.value = question
      input.dispatchEvent(new dom.window.Event('input', { bubbles: true }))
      await settle()
    }
    const finish = async data => {
      finishInterview({ success: true, data })
      await settle()
    }
    const cards = () => [...root.querySelectorAll('.result-card')]
    const selectAll = () => click('.selection-actions .action-link')
    const submit = async () => {
      click('.survey-submit-btn')
      await settle()
    }

    await settle()
    click('.survey-pill')
    await settle()
    selectAll()
    await ask('  What changes?  ')
    await submit()
    assert.deepEqual(statusRequests, [{ simulation_id: 'sim_paired' }])
    assert.deepEqual(requests, [{
      simulation_id: 'sim_paired',
      interviews: [0, 1, 2].map(agent_id => ({ agent_id, prompt: 'What changes?' }))
    }])
    const time = '2026-11-18 07:32:10.125'
    const markdown = '**Twitter reply** <img src=x onerror=alert(1)>'
    await finish({ result: { results: {
      twitter_0: { agent_id: 0, platform: 'twitter', response: markdown, timestamp: 0 },
      reddit_0: { agent_id: '0', platform: 'reddit', response: 'Reddit reply', timestamp: time },
      twitter_1: { agent_id: 1, platform: 'reddit', response: 'Wrong platform' },
      reddit_1: { agent_id: 0, response: 'Wrong actor' },
      reddit_2: { answer: 'Reply identified by its key', unrelated: 'private field' },
      twitter_8: { response: 'Unselected actor' }
    } } })

    assert.equal(cards().length, 6)
    assert.equal(root.querySelector('.results-count').textContent, '3 responses')
    assert.equal(cards()[0].querySelector('strong').textContent, 'Twitter reply')
    assert.equal(cards()[0].querySelector('.result-role:last-child').textContent.trim(), 'twitter · 0')
    assert.equal(cards()[0].querySelector('img'), null)
    assert.match(cards()[1].textContent, /reddit.*2026-11-18 07:32:10\.125.*Reddit reply/s)
    assert.match(cards()[2].textContent, /No reply available/)
    assert.doesNotMatch(root.querySelector('.survey-results').textContent, /Wrong actor|Wrong platform|Unselected actor/)
    assert.equal(blobs.length, 0)

    await ask('Unsaved next question')
    assert.ok(cards().every(card => card.querySelector('.result-question').textContent.trim() === 'What changes?'))
    const download = root.querySelector('.results-header button')
    assert.equal(download.type, 'button')
    assert.equal(download.textContent, guidedSurveyCopyFor('en').download)
    download.click()
    assert.deepEqual(downloads, [{ name: 'nexaweave-survey-results.json', href: 'blob:survey-1', connected: true }])
    assert.equal(document.querySelector('a[download]'), null)
    assert.equal(blobs[0].type, 'application/json;charset=utf-8')
    const snapshot = JSON.parse(await blobs[0].text())
    const row = (agent_id, agent_name, profession, platform, timestamp, answer) => ({
      agent_id, agent_name, profession, platform, timestamp, answer,
      status: answer === null ? 'missing' : 'received'
    })
    assert.deepEqual(snapshot, {
      schema_version: 1,
      simulation_id: 'sim_paired',
      question: 'What changes?',
      replies: [
        row(0, 'Amina', 'Planner', 'twitter', 0, markdown),
        row(0, 'Amina', 'Planner', 'reddit', time, 'Reddit reply'),
        row(1, 'Chen', null, 'twitter', null, null),
        row(1, 'Chen', null, 'reddit', null, null),
        row(2, 'Devi', null, 'twitter', null, null),
        row(2, 'Devi', null, 'reddit', null, 'Reply identified by its key')
      ]
    })
    assert.equal(serializeSurveyResults({
      ...snapshot,
      replies: Array.from({ length: 6 }, () => ({
        ...snapshot.replies[0],
        answer: '中'.repeat(Math.ceil(MAX_SURVEY_EXPORT_BYTES / 16))
      }))
    }), null)
    assert.equal(requests.length, 1)
    assert.deepEqual(profileRequests, ['sim_paired'])
    assert.deepEqual(statusRequests, [{ simulation_id: 'sim_paired' }])

    for (const locale of ['zh', 'ms']) {
      i18n.global.locale.value = locale
      await settle()
      assert.equal(download.textContent, guidedSurveyCopyFor(locale).download)
      assert.equal(cards()[2].querySelector('.result-answer').textContent, guidedSurveyCopyFor(locale).missing)
    }

    let releaseStatus
    statusFor = id => new Promise(resolve => { releaseStatus = () => resolve(liveStatus(id)) })
    await submit()
    assert.equal(statusRequests.length, 2)
    assert.equal(requests.length, 1)
    const firstSelected = root.querySelector('.agent-checkbox input')
    firstSelected.click()
    await settle()
    firstSelected.click()
    await settle()
    releaseStatus()
    await settle()
    assert.equal(cards().length, 0)
    assert.equal(requests.length, 1)
    assert.deepEqual(revoked, ['blob:survey-1'])

    statusFor = liveStatus
    await submit()
    await finish({ result: { results: { twitter_0: { response: 'Only Twitter replied' } } } })
    assert.equal(cards().length, 6)
    const missingReddit = cards().filter(card => card.querySelector('.result-role:last-child').textContent.trim() === 'reddit')
    assert.equal(missingReddit.length, 3)
    assert.ok(missingReddit.every(card =>
      card.querySelector('.result-answer').textContent === guidedSurveyCopyFor('ms').missing))

    await submit()
    props.simulationId = 'sim_single'
    await settle()
    await finish({ result: { results: { twitter_0: { response: 'Stale simulation' } } } })
    assert.equal(cards().length, 0)
    assert.equal(root.querySelector('.agent-checkbox input').checked, false)
    assert.deepEqual(profileRequests, ['sim_paired', 'sim_single'])

    selectAll()
    await ask('Current question')
    await submit()
    await finish({ result: { results: { 0: { agent_id: 0, response: 'Numeric reply', timestamp: time } } } })
    assert.equal(cards().length, 1)
    assert.match(cards()[0].textContent, /Eli.*twitter.*Numeric reply/s)
    for (const inconclusiveStatus of [
      () => statusResponse('sim_other', false, true),
      id => statusResponse(id, true, 'false'),
      async () => { throw new Error('Status unavailable') }
    ]) {
      statusFor = inconclusiveStatus
      await submit()
      await finish({ result: { results: { 0: { agent_id: '0', response: 'Numeric reply of unknown platform', timestamp: 17 } } } })
      assert.equal(cards().length, 1)
      assert.equal(cards()[0].querySelector('.result-role:last-child').textContent.trim(),
        `${guidedSurveyCopyFor('ms').unknownPlatform} · 17`)
    }
    const priorStatusCalls = statusRequests.length
    const priorInterviews = requests.length
    click('.results-header button')
    assert.deepEqual(JSON.parse(await blobs.at(-1).text()).replies, [
      row(0, 'Eli', 'Writer', null, 17, 'Numeric reply of unknown platform')
    ])
    assert.equal(statusRequests.length, priorStatusCalls)
    assert.equal(requests.length, priorInterviews)

    await submit()
    await finish({ results: [
      { agent_id: '00', response: 'Unattested actor' },
      { agent_id: '0', platform: 'twitter', answer: 'Legacy array reply', timestamp: time }
    ] })
    assert.equal(cards().length, 1)
    assert.match(cards()[0].textContent, /twitter.*Legacy array reply/s)
    assert.equal(statusRequests.length, requests.length + 1)
  } finally {
    app.unmount()
  }
})
