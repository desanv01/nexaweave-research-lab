// Main-owned standalone follow-up UI child. Python owns hosts and real DTO witnesses.
import { chromium } from 'playwright-core'
import { createHash } from 'node:crypto'
import { lstat, realpath, readdir, mkdtemp, mkdir, rm, open } from 'node:fs/promises'
import path from 'node:path'
import { performance } from 'node:perf_hooks'
import { connectedFollowupsCopyFor } from '../src/i18n/connectedFollowups.js'

const WIRE = 7200, FRONTEND = 'http://127.0.0.1:4317'
const UUID = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/
const HASH = /^[0-9a-f]{64}$/
const STATES = ['planned', 'queued', 'generating', 'completed', 'failed', 'cancelled', 'uncertain']
const STAGES = ['connect', 'followup-plan', 'followup-start', 'followup-ready', 'followup-read',
  'followup-export', 'followup-history', 'followup-recover', 'locale', 'close']
const decoder = new TextDecoder('utf-8', { fatal: true })
const sha = bytes => createHash('sha256').update(bytes).digest('hex')
const failure = () => new Error('followup_browser_failed')
const check = value => { if (!value) throw failure() }
const plain = value => value && Object.getPrototypeOf(value) === Object.prototype
function fields(value, names) {
  check(plain(value) && Object.keys(value).sort().join('|') === names.split(' ').sort().join('|'))
}
function integer(value, maximum, minimum = 0) {
  check(Number.isSafeInteger(value) && !Object.is(value, -0) && value >= minimum && value <= maximum)
}
function uuid(value) { check(typeof value === 'string' && UUID.test(value)) }
function digest(value) { check(typeof value === 'string' && HASH.test(value)) }
function text(value, cap, minimum = 1) {
  check(typeof value === 'string' && value.length >= minimum && Buffer.byteLength(value) <= cap && !value.includes('\0'))
}
function same(a, b) {
  if (!plain(a) || !plain(b)) return false
  return Object.keys(a).sort().join('|') === Object.keys(b).sort().join('|')
    && Object.keys(a).every(key => a[key] === b[key])
}
function inside(parent, child) {
  const relative = path.relative(parent, child)
  check(relative !== '' && !relative.startsWith('..') && !path.isAbsolute(relative))
}
async function physical(absolute, directory = false) {
  check(typeof absolute === 'string' && path.isAbsolute(absolute) && path.resolve(absolute) === absolute)
  const stat = await lstat(absolute)
  check(!stat.isSymbolicLink() && (directory ? stat.isDirectory() : stat.isFile()))
  check(await realpath(absolute) === absolute)
  return stat
}

let browser, context, page, connection, parent, privateRoot, sessionRoot, downloadRoot
let temporaryEnvironment, closing, launchAttempted = false, launchReturned = false
let connected = false, closed = false, fault = false, sequence = 0, locale = ''
let commandEnd = 0, currentStage = 'connect', completedCount = 0, exportCount = 0
let stageRequests = 0, requestCount = 0, primaryError, cleanupError
const turns = new Map(), mutations = new Map()
const lifetimeEnd = performance.now() + 540000
function remaining() {
  const value = Math.floor(Math.min(commandEnd, lifetimeEnd) - performance.now())
  if (value <= 0) throw new Error('followup_browser_deadline')
  return value
}
async function bound(task, cap = 15000) {
  const time = Math.min(remaining(), cap)
  let timer
  try {
    return await Promise.race([task(), new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error('followup_browser_deadline')), time)
    })])
  } finally { clearTimeout(timer) }
}
function healthy() { check(!fault && !closed); remaining() }
async function reply(value) {
  const line = JSON.stringify(value) + '\n'
  check(Buffer.byteLength(line) <= WIRE)
  await bound(() => new Promise((resolve, reject) => {
    process.stdout.write(line, error => error ? reject(error) : resolve())
  }))
}

function commandJson(raw) {
  const value = JSON.parse(raw), stack = []
  for (let at = 0; at < raw.length; at++) {
    const part = raw[at]
    if (part === '"') {
      const start = at
      for (at++; at < raw.length; at++) {
        if (raw[at] === '\\') at++
        else if (raw[at] === '"') break
      }
      let next = at + 1
      while (/\s/.test(raw[next] || '') && next < raw.length) next++
      if (raw[next] === ':') {
        const owner = stack[stack.length - 1], key = JSON.parse(raw.slice(start, at + 1))
        check(owner instanceof Set && !owner.has(key)); owner.add(key)
      }
    } else if (part === '{' || part === '[') {
      check(stack.length < 16); stack.push(part === '{' ? new Set() : null)
    } else if (part === '}' || part === ']') stack.pop()
  }
  check(stack.length === 0)
  return value
}

// Incremental command intake never accumulates more than one wire window.
async function* commands() {
  let line = Buffer.alloc(0), count = 0
  for await (const chunk of process.stdin) {
    check(chunk.length <= WIRE)
    let start = 0
    for (let at = 0; at < chunk.length; at++) {
      if (chunk[at] !== 10) continue
      check(++count <= 64 && line.length + at - start + 1 <= WIRE)
      const bytes = Buffer.concat([line, chunk.subarray(start, at)])
      check(bytes.length > 0 && bytes[bytes.length - 1] !== 13)
      line = Buffer.alloc(0); start = at + 1
      yield commandJson(decoder.decode(bytes))
    }
    check(line.length + chunk.length - start + 1 <= WIRE)
    line = Buffer.concat([line, chunk.subarray(start)])
  }
  check(line.length === 0)
}

const followupRoute = method => `/api/connected-followup/${method}/${connection.display_graph_id}`
const reportRoute = () => `/api/connected-report/status/${connection.display_graph_id}`
const graphRoute = () => `/api/graph/data/${connection.display_graph_id}`
const scope = () => page.locator('.connected-followup')
const record = id => scope().locator(`.turn-record[data-turn-id="${id}"]`)
function identity(payload, extra = '') {
  fields(payload, 'schema_version turn_id plan_sha256' + (extra ? ' ' + extra : ''))
  check(payload.schema_version === 1); uuid(payload.turn_id); digest(payload.plan_sha256)
  const known = turns.get(payload.turn_id)
  check(known && known.plan_sha256 === payload.plan_sha256)
  return known
}
async function boundedText(locator, cap = WIRE) {
  return bound(() => locator.evaluate((node, maximum) => {
    const value = node.textContent || ''
    if (value.length > maximum) throw Error('followup_browser_failed')
    return value
  }, cap))
}
async function visible(locator) { await bound(() => locator.waitFor({ state: 'visible', timeout: remaining() })) }
async function enabled(locator) {
  await visible(locator)
  await bound(() => locator.click({ trial: true, timeout: remaining() }))
  check(await bound(() => locator.isEnabled()))
}
async function click(locator) {
  await enabled(locator)
  await bound(() => locator.click({ timeout: remaining() }))
}
async function idle() {
  await bound(() => scope().locator('fieldset').first().waitFor({ state: 'visible', timeout: remaining() }))
  await bound(() => page.waitForFunction(selector => {
    const node = document.querySelector(selector)
    return node && node.getAttribute('aria-busy') === 'false'
  }, '.connected-followup', { timeout: remaining() }))
  healthy()
}
async function english() {
  if (locale !== 'en') {
    await bound(() => page.locator('#workbench-language').selectOption('en', { timeout: remaining() }))
    locale = 'en'
  }
}
async function turnState(id) {
  const row = record(id)
  await visible(row)
  const heading = (await boundedText(row.locator('h3').first(), 256)).trim()
  const labels = connectedFollowupsCopyFor(locale).states
  const matches = STATES.filter(state => labels[state] === heading)
  check(matches.length === 1)
  const state = matches[0]
  const values = await bound(() => row.locator('dl > dd').evaluateAll(nodes => nodes.slice(0, 4).map(node => {
    const value = node.textContent || ''
    if (value.length > 128) throw Error('followup_browser_failed')
    return value
  })))
  check(values[0] === id && values[1] === turns.get(id).plan_sha256 && values[2] === parent.report_id)
  if (state === 'completed') check(await bound(() => row.locator('.followup-read').count()) === 1)
  return state
}

function observe() {
  page.on('pageerror', () => { fault = true })
  page.on('request', request => {
    if (!connection || !request.url().startsWith(connection.api_origin + '/api/')) return
    requestCount++; stageRequests++
    if (requestCount > 64 || stageRequests > 6) { fault = true; return }
    const url = new URL(request.url())
    const allowed = request.method() === 'GET' && url.pathname === graphRoute()
      || request.method() === 'POST' && (url.pathname === reportRoute()
        || ['plan', 'start', 'status', 'read', 'download', 'history'].some(method => url.pathname === followupRoute(method)))
    if (!allowed || url.search || url.hash) fault = true
    if (request.method() === 'POST' && /\/connected-followup\/(plan|start)\//.test(url.pathname)) {
      try {
        const raw = request.postData(); text(raw, WIRE)
        const value = JSON.parse(raw); uuid(value.turn_id)
        const key = url.pathname + '/' + value.turn_id
        mutations.set(key, (mutations.get(key) || 0) + 1)
        if (mutations.get(key) !== 1) fault = true
      } catch { fault = true }
    }
  })
}

// Passive headers/request metadata only. Server-side Python retains the real DTO.
async function exchange(route, method, action, cap, expectedRequest = null) {
  healthy()
  const url = connection.api_origin + route
  let stop, cancel
  const pending = new Promise((resolve, reject) => {
    cancel = reject
    const response = value => {
      if (value.url() !== url || value.request().method() !== method) return
      stop(); resolve(value)
    }
    const timer = setTimeout(() => { stop?.(); reject(new Error('followup_browser_deadline')) }, remaining())
    stop = () => { clearTimeout(timer); page.off('response', response) }
    page.on('response', response)
  })
  pending.catch(() => {})
  let primary
  try {
    await bound(action)
    const response = await bound(() => pending)
    const headers = response.headers()
    check(response.status() === 200 && /^application\/json(?:\s*;|$)/i.test(headers['content-type'] || '')
      && /^\d+$/.test(headers['content-length'] || '') && !headers['content-encoding'])
    integer(Number(headers['content-length']), cap, 1)
    const request = response.request()
    check(request.url() === url && request.method() === method)
    check(request.headers()['authorization'] === `Bearer ${connection.bearer_token}`)
    const raw = request.postData()
    let sent = null
    if (raw !== null) { text(raw, WIRE); sent = JSON.parse(raw); check(plain(sent)) }
    if (expectedRequest) check(same(sent, expectedRequest))
    healthy()
    return sent
  } catch (error) { primary = error; throw error }
  finally {
    try { stop?.(); cancel?.(primary) }
    catch (error) { cleanupError = error; if (!primary) throw error }
  }
}

async function recoverParent() {
  await bound(() => page.locator('#api-origin').fill(connection.api_origin, { timeout: remaining() }))
  await bound(() => page.locator('#graph-id').fill(connection.display_graph_id, { timeout: remaining() }))
  await bound(() => page.locator('#bearer-token').fill(connection.bearer_token, { timeout: remaining() }))
  await exchange(graphRoute(), 'GET', () => click(page.locator('.connection-form button[type=submit]')), 2098176)
  await enabled(page.locator('.graph-refresh'))
  check(await bound(() => page.locator('#bearer-token').inputValue()) === '')
  check((await boundedText(page.locator('.graph-overview h2 .mono'), 128)) === connection.display_graph_id)
  await bound(() => page.locator('#report-recovery-id').fill(parent.report_id, { timeout: remaining() }))
  await bound(() => page.locator('#report-recovery-hash').fill(parent.plan_sha256, { timeout: remaining() }))
  await exchange(reportRoute(), 'POST', () => click(page.locator('.connected-reports .report-recover')),
    262272, { schema_version: 1, report_id: parent.report_id, plan_sha256: parent.plan_sha256 })
  const parentRow = page.locator(`.connected-reports .report-record[data-report-id="${parent.report_id}"]`)
  await enabled(parentRow.locator('.report-followup'))
  check((await boundedText(parentRow.locator('dl > dd').nth(1), 64)) === parent.plan_sha256)
  await click(parentRow.locator('.report-followup'))
  await enabled(scope().locator('.followup-review'))
  await idle()
}

async function connect(payload) {
  fields(payload, 'connection report profile_root')
  fields(payload.connection, 'api_origin display_graph_id bearer_token')
  fields(payload.report, 'report_id plan_sha256')
  check(!connected && !browser && !sessionRoot)
  check(/^http:\/\/127\.0\.0\.1:[1-9][0-9]{0,4}$/.test(payload.connection.api_origin))
  integer(Number(new URL(payload.connection.api_origin).port), 65535, 1)
  check(/^[A-Za-z0-9_-]{1,128}$/.test(payload.connection.display_graph_id))
  check(/^[\x21-\x7e]{1,505}$/.test(payload.connection.bearer_token))
  uuid(payload.report.report_id); digest(payload.report.plan_sha256)
  check(process.platform === 'linux' && process.versions.node === '24.14.1')
  check(!process.env.DEBUG && !process.env.PWDEBUG && !process.env.NODE_OPTIONS && !process.env.BROWSER_ARGS)
  for (const name of ['NEXAWEAVE_RESEARCH_BROWSER_NODE', 'NEXAWEAVE_RESEARCH_BROWSER_CHROME']) {
    await bound(() => physical(process.env[name]))
    if (name.endsWith('_NODE')) check(await bound(() => realpath(process.env[name])) === await bound(() => realpath(process.execPath)))
  }
  await bound(() => physical(payload.profile_root, true))
  check((await bound(() => readdir(payload.profile_root))).length === 0)
  connection = payload.connection; parent = payload.report; privateRoot = payload.profile_root
  sessionRoot = await bound(() => mkdtemp(path.join(privateRoot, 'followup-')))
  inside(privateRoot, sessionRoot)
  for (const name of ['downloads', 'home', 'cache', 'config', 'temp']) {
    await bound(() => mkdir(path.join(sessionRoot, name), { mode: 0o700 }))
  }
  downloadRoot = path.join(sessionRoot, 'downloads')
  const temporaryDirectory = path.join(sessionRoot, 'temp'), environment = {}
  for (const name of ['PATH', 'LANG', 'LC_ALL', 'TZ', 'SYSTEMROOT']) if (process.env[name]) environment[name] = process.env[name]
  Object.assign(environment, { HOME: path.join(sessionRoot, 'home'), USERPROFILE: path.join(sessionRoot, 'home'),
    XDG_CACHE_HOME: path.join(sessionRoot, 'cache'), XDG_CONFIG_HOME: path.join(sessionRoot, 'config'),
    TMPDIR: temporaryDirectory, TMP: temporaryDirectory, TEMP: temporaryDirectory, BROWSER: 'none', CI: 'true' })
  temporaryEnvironment = ['TMPDIR', 'TMP', 'TEMP'].map(key => ({ key, present: Object.hasOwn(process.env, key), value: process.env[key] }))
  for (const saved of temporaryEnvironment) process.env[saved.key] = temporaryDirectory
  launchAttempted = true
  browser = await bound(() => chromium.launch({ executablePath: process.env.NEXAWEAVE_RESEARCH_BROWSER_CHROME,
    headless: true, chromiumSandbox: true, downloadsPath: downloadRoot, env: environment, timeout: remaining(),
    args: ['--no-first-run', '--no-default-browser-check', '--disable-sync', '--no-startup-window'] }))
  launchReturned = true
  context = await bound(() => browser.newContext({ acceptDownloads: true }))
  context.setDefaultTimeout(15000); context.setDefaultNavigationTimeout(15000)
  page = await bound(() => context.newPage()); observe()
  await bound(() => page.goto(FRONTEND + '/research', { waitUntil: 'domcontentloaded', timeout: remaining() }))
  await english(); await recoverParent(); connected = true
  return { display_graph_id: connection.display_graph_id, report_id: parent.report_id,
    plan_sha256: parent.plan_sha256, token_cleared: true }
}

async function history(payload) {
  fields(payload, 'schema_version report_id report_plan_sha256 before_ordinal')
  check(payload.schema_version === 1 && payload.report_id === parent.report_id
    && payload.report_plan_sha256 === parent.plan_sha256 && payload.before_ordinal === null)
  await english()
  await exchange(followupRoute('history'), 'POST', () => click(scope().locator('.history .actions button').first()), 262272, payload)
  await idle()
  const head = (await boundedText(scope().locator('.history .mono'), 256)).trim().match(/[0-9a-f]{64}$/)?.[0]
  digest(head)
  const totalText = await boundedText(scope().locator('.history > p').first(), 256)
  const total = Number(totalText.match(/:\s*(\d+)\s*$/)?.[1]); integer(total, 1000)
  check(total === completedCount)
  return { report_id: parent.report_id, total_completed: total, head_sha256: head }
}
async function plan(payload) {
  fields(payload, 'question output_language expected_history_sha256')
  check(turns.size < 2 && completedCount === turns.size)
  text(payload.question, 16000)
  check([...payload.question].length <= 4000 && payload.question.trim() !== ''
    && payload.output_language === (turns.size === 0 ? 'en' : 'ms'))
  digest(payload.expected_history_sha256)
  await english()
  await bound(() => page.locator('#followup-question').fill(payload.question, { timeout: remaining() }))
  await bound(() => page.locator('#followup-language').selectOption(payload.output_language, { timeout: remaining() }))
  const before = await history({ schema_version: 1, report_id: parent.report_id,
    report_plan_sha256: parent.plan_sha256, before_ordinal: null })
  check(before.head_sha256 === payload.expected_history_sha256)
  const sent = await exchange(followupRoute('plan'), 'POST', () => click(scope().locator('.followup-review')), 262272)
  fields(sent, 'schema_version turn_id report_id report_plan_sha256 question output_language expected_history_sha256')
  uuid(sent.turn_id)
  check(sent.schema_version === 1 && !turns.has(sent.turn_id) && sent.report_id === parent.report_id
    && sent.report_plan_sha256 === parent.plan_sha256 && sent.question === payload.question
    && sent.output_language === payload.output_language && sent.expected_history_sha256 === before.head_sha256)
  await idle()
  const turnId = await bound(() => page.locator('#followup-recovery-id').inputValue())
  const planHash = await bound(() => page.locator('#followup-recovery-hash').inputValue())
  check(turnId === sent.turn_id); digest(planHash)
  turns.set(turnId, { plan_sha256: planHash, started: false, completed: false, question_sha256: sha(sent.question), output_language: sent.output_language })
  check(await turnState(turnId) === 'planned')
  await enabled(record(turnId).locator('.followup-start'))
  return { turn_id: turnId, plan_sha256: planHash, question_sha256: sha(sent.question), output_language: sent.output_language }
}
async function operation(payload, method) {
  const known = identity(payload)
  await english()
  if (method === 'start') {
    check(!known.started && !known.completed)
    known.started = true
  } else if (method === 'status') check(known.started && !known.completed)
  else check(method === 'read' && known.completed)
  const selector = { start: '.followup-start', status: '.followup-refresh', read: '.followup-read' }[method]
  await exchange(followupRoute(method), 'POST', () => click(record(payload.turn_id).locator(selector)),
    method === 'read' ? 4194304 : 262272, payload)
  await idle()
  const state = await turnState(payload.turn_id)
  if (method === 'status') {
    check(state === 'completed'); known.completed = true; completedCount++
  } else if (method === 'start') check(['queued', 'generating', 'completed'].includes(state))
  const result = { turn_id: payload.turn_id, plan_sha256: payload.plan_sha256, state }
  if (method === 'read') {
    const answer = scope().locator('.answer')
    await visible(answer)
    check((await boundedText(answer.locator('p.mono'), 36)) === payload.turn_id)
    const display = answer.locator('.markdown, pre.literal')
    await visible(display)
    const shown = await bound(() => display.evaluate(node => {
      const value = node.textContent || ''
      return value.length > 0 && value.length <= 16384
    }))
    check(shown); result.rendered = true
  }
  return result
}

async function hashDownload(download, expectedName) {
  check(await bound(() => download.failure()) === null)
  check(download.suggestedFilename() === expectedName)
  const saved = path.join(downloadRoot, `export-${exportCount}-${expectedName}`)
  inside(downloadRoot, saved)
  await bound(() => download.saveAs(saved)); await bound(() => physical(saved))
  const file = await bound(() => open(saved, 'r'))
  let primary
  try {
    const before = await bound(() => file.stat()); integer(before.size, 2097152, 1)
    check(before.isFile())
    const hashing = createHash('sha256'), buffer = Buffer.alloc(32768)
    let total = 0
    while (true) {
      const value = await bound(() => file.read(buffer, 0, Math.min(buffer.length, 2097153 - total), null))
      if (!value.bytesRead) break
      total += value.bytesRead; check(total <= 2097152); hashing.update(buffer.subarray(0, value.bytesRead))
    }
    const after = await bound(() => file.stat())
    check(total === before.size && after.size === before.size && after.ino === before.ino && after.mtimeMs === before.mtimeMs)
    return { size: total, sha256: hashing.digest('hex') }
  } catch (error) { primary = error; throw error }
  finally {
    const errors = []
    try { await bound(() => file.close()) } catch (error) { errors.push(error) }
    try { await bound(() => download.delete()) } catch (error) { errors.push(error) }
    if (errors.length) { cleanupError = new AggregateError(errors, 'followup_browser_cleanup_failed'); if (!primary) throw cleanupError }
  }
}
async function exportTurn(payload) {
  const known = identity(payload, 'kind')
  check(known.completed && ['answer', 'conversation'].includes(payload.kind) && ++exportCount <= 8)
  await english()
  const row = record(payload.turn_id)
  await bound(() => row.locator(`select[id="followup-artifact-${payload.turn_id}"]`).selectOption(payload.kind, { timeout: remaining() }))
  await exchange(followupRoute('download'), 'POST', () => click(row.locator('.followup-download')), 4194304, payload)
  await idle()
  const name = payload.kind === 'answer' ? 'answer.md' : 'conversation.json'
  const anchor = row.locator('.followup-save')
  await enabled(anchor)
  check(await bound(() => anchor.getAttribute('download')) === name)
  const href = await bound(() => anchor.getAttribute('href'))
  check(typeof href === 'string' && href.startsWith('blob:' + FRONTEND + '/'))
  const waiting = page.waitForEvent('download', { timeout: remaining() })
  waiting.catch(() => {})
  await click(anchor)
  const proof = await hashDownload(await bound(() => waiting), name)
  return { turn_id: payload.turn_id, plan_sha256: payload.plan_sha256, kind: payload.kind, name, ...proof }
}
async function recover(payload) {
  const known = identity(payload, 'reload')
  check(typeof payload.reload === 'boolean' && known.completed)
  if (payload.reload) {
    await bound(() => page.reload({ waitUntil: 'domcontentloaded', timeout: remaining() }))
    locale = ''; await english(); await recoverParent()
  } else await english()
  await bound(() => page.locator('#followup-recovery-id').fill(payload.turn_id, { timeout: remaining() }))
  await bound(() => page.locator('#followup-recovery-hash').fill(payload.plan_sha256, { timeout: remaining() }))
  await exchange(followupRoute('status'), 'POST', () => click(scope().locator('form.recovery button[type=submit]')),
    262272, { schema_version: 1, turn_id: payload.turn_id, plan_sha256: payload.plan_sha256 })
  await idle()
  const state = await turnState(payload.turn_id)
  check(state === 'completed' && await bound(() => record(payload.turn_id).locator('.followup-start').count()) === 0)
  return { turn_id: payload.turn_id, plan_sha256: payload.plan_sha256, state, recovered: true }
}
async function changeLocale(payload) {
  fields(payload, 'locale'); check(['en', 'zh', 'ms'].includes(payload.locale))
  const before = await bound(() => scope().locator('.turn-record').evaluateAll(nodes => nodes.map(node => node.getAttribute('data-turn-id'))))
  check(before.length <= 2)
  await bound(() => page.locator('#workbench-language').selectOption(payload.locale, { timeout: remaining() }))
  locale = payload.locale
  check(await bound(() => page.locator('.workbench').getAttribute('lang')) === locale)
  check(await bound(() => scope().getAttribute('lang')) === locale)
  const after = await bound(() => scope().locator('.turn-record').evaluateAll(nodes => nodes.map(node => node.getAttribute('data-turn-id'))))
  check(JSON.stringify(before) === JSON.stringify(after))
  for (const id of before) await turnState(id)
  await enabled(scope().locator('form.recovery button[type=submit]'))
  return { locale, lang: locale }
}

async function cleanup() {
  if (closing) return closing
  closing = (async () => {
    const errors = []
    try {
      if (context) {
        try { await bound(() => context.close(), 5000); context = null }
        catch (error) { errors.push(error) }
      }
      if (browser) {
        try { await bound(() => browser.close(), 8000); browser = null }
        catch (error) { errors.push(error) }
      }
      if (launchAttempted && !launchReturned) errors.push(failure())
      if (!errors.length && !context && !browser && sessionRoot) {
        try {
          await bound(() => physical(privateRoot, true)); await bound(() => physical(sessionRoot, true))
          inside(privateRoot, sessionRoot)
          await bound(() => rm(sessionRoot, { recursive: true, force: false })); sessionRoot = null
        } catch (error) { errors.push(error) }
      }
    } finally {
      if (temporaryEnvironment) {
        for (const saved of temporaryEnvironment) {
          try { if (saved.present) process.env[saved.key] = saved.value; else delete process.env[saved.key] }
          catch (error) { errors.push(error) }
        }
        temporaryEnvironment = null
      }
      connection = null; turns.clear()
    }
    if (errors.length) throw new AggregateError(errors, 'followup_browser_cleanup_failed')
    check(!context && !browser && !sessionRoot)
    closed = true
    return { context_closed: true, browser_closed: true, private_tree_removed: true }
  })()
  return closing
}
const handlers = {
  connect, 'followup-plan': plan, 'followup-start': payload => operation(payload, 'start'),
  'followup-ready': payload => operation(payload, 'status'), 'followup-read': payload => operation(payload, 'read'),
  'followup-export': exportTurn, 'followup-history': history, 'followup-recover': recover, locale: changeLocale,
  close: async payload => { fields(payload, ''); check(turns.size === 2 && completedCount === 2); return cleanup() },
}
const hardTimer = setTimeout(() => { fault = true; process.stdin.destroy() }, 540000)
hardTimer.unref()
try {
  for await (const command of commands()) {
    fields(command, 'schema_version sequence stage timeout_ms payload')
    integer(command.sequence, 64, 1); integer(command.timeout_ms, 15000, 1)
    check(command.schema_version === 1 && command.sequence === sequence + 1 && STAGES.includes(command.stage) && plain(command.payload))
    check(!closed && (sequence === 0 ? command.stage === 'connect' : connected && command.stage !== 'connect'))
    sequence = command.sequence; currentStage = command.stage; stageRequests = 0
    commandEnd = Math.min(performance.now() + command.timeout_ms, lifetimeEnd)
    healthy()
    const result = await handlers[currentStage](command.payload)
    check(plain(result) && (!fault || currentStage === 'close'))
    await reply({ schema_version: 1, sequence, stage: currentStage, ok: true, result })
    if (currentStage === 'close') break
  }
  check(closed)
} catch (error) {
  primaryError = error; process.exitCode = 1
  try {
    await reply({ schema_version: 1, sequence, stage: currentStage, ok: false,
      error: error?.message === 'followup_browser_deadline' ? 'followup_browser_deadline' : 'followup_browser_failed' })
  } catch { /* Broken/expired protocol cannot become success. Python owns fallback. */ }
} finally {
  clearTimeout(hardTimer)
  try { await cleanup() } catch (error) { cleanupError = error; process.exitCode = 1 }
  process.stdin.destroy()
}
