// Main-owned opt-in real /research journey. No HTTP mutation outside UI controls.
import { chromium } from 'playwright-core'
import { createHash } from 'node:crypto'
import { lstat, realpath, readdir, mkdtemp, mkdir, rm, open } from 'node:fs/promises'
import path from 'node:path'

const WIRE = 7200
const FRONTEND = 'http://127.0.0.1:4317'
const STAGES = ['connect', 'preparation-plan', 'preparation-start', 'preparation-ready', 'native-start', 'native-ready', 'report-start', 'report-read', 'pdf-read-after-restart', 'close']
const UUID = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/
const HASH = /^[0-9a-f]{64}$/
const decoder = new TextDecoder('utf-8', { fatal: true })
const sha = bytes => createHash('sha256').update(bytes).digest('hex')
const fail = () => { throw new Error('research_browser_failed') }
const requireTrue = value => { if (!value) fail() }
const same = (a, b) => {
  const sorted = v => Array.isArray(v) ? v.map(sorted) : v && typeof v === 'object' ? Object.fromEntries(Object.keys(v).sort().map(k => [k, sorted(v[k])])) : v
  return JSON.stringify(sorted(a)) === JSON.stringify(sorted(b))
}
function keys(value, required, optional = []) {
  requireTrue(value && Object.getPrototypeOf(value) === Object.prototype)
  requireTrue(required.every(k => Object.hasOwn(value, k)) && Object.keys(value).every(k => required.includes(k) || optional.includes(k)))
}
function text(value, cap, min = 1) { requireTrue(typeof value === 'string' && value.length >= min && Buffer.byteLength(value) <= cap && !value.includes('\0')) }
function integer(value, max, min = 0) { requireTrue(Number.isSafeInteger(value) && value >= min && value <= max) }
function uuid(value) { requireTrue(typeof value === 'string' && UUID.test(value)) }
function digest(value) { requireTrue(typeof value === 'string' && HASH.test(value)) }
function inside(parent, child) { const relative = path.relative(parent, child); requireTrue(relative !== '' && !relative.startsWith('..') && !path.isAbsolute(relative)) }
async function within(promise, milliseconds = 15000) {
  let timer
  try {
    return await Promise.race([promise, new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('research_browser_deadline')), milliseconds) })])
  } finally { clearTimeout(timer) }
}
async function physical(absolute, directory = false) {
  requireTrue(typeof absolute === 'string' && path.isAbsolute(absolute) && path.resolve(absolute) === absolute)
  const value = await lstat(absolute)
  requireTrue(!value.isSymbolicLink() && (directory ? value.isDirectory() : value.isFile()))
  requireTrue(await realpath(absolute) === absolute)
  return value
}

let browser, context, page, privateRoot, sessionRoot, downloadRoot, connection, sources, orderedSources, pdf
let preparation, launch, report, fault = false, timedOut = false, index = 0, stage = 'protocol', phase = 'protocol'
let launchStep = null, launchAttempted = false, launchReturned = false, temporaryEnvironment = null
let mutations = new Map()
let closing = null
const deadline = Date.now() + 540000
const clock = () => { requireTrue(!timedOut && Date.now() < deadline && !fault) }
function launchError(error) {
  if (launchStep === 'context') return 'context-create-failed'
  if (launchStep === 'page') return 'page-create-failed'
  if (error?.name === 'TimeoutError') return 'launch-timeout'
  // One private UTF8-bounded window; never copy/encode the whole exception.
  // Reserve four bytes before inspecting a codepoint, including at the edge.
  const raw = typeof error?.message === 'string' ? error.message : ''
  let message = '', bytes = 0
  for (let at = 0; at < raw.length && bytes <= WIRE - 4;) {
    const point = raw.codePointAt(at)
    const part = String.fromCodePoint(point)
    const width = point < 128 ? 1 : point < 2048 ? 2 : point < 65536 ? 3 : 4
    message += part; bytes += width; at += part.length
  }
  const targetClosed = message.includes('Target page, context or browser has been closed')
  const marker = message.indexOf('[err]')
  if (marker >= 0) message = message.slice(marker)
  if (error?.code === 'ENOENT' || message.includes("Executable doesn't exist")) return 'executable-missing'
  if (message.includes('Failed to move to new namespace') && message.includes('Operation not permitted')) return 'namespace-refused'
  if (['No usable sandbox', 'Running as root without --no-sandbox', 'Chromium sandboxing failed!'].some(literal => message.includes(literal))) return 'sandbox-refused'
  if (message.includes('error while loading shared libraries')) return 'runtime-library-missing'
  if (message.includes('chrome_crashpad_handler') && message.includes('--database is required')) return 'crashpad-database-missing'
  if (message.includes('Failed to create a ProcessSingleton')) return 'profile-lock-refused'
  if ((message.includes('pthread_create') && message.includes('Resource temporarily unavailable')) || message.includes('Cannot allocate memory')) return 'runtime-resource-refused'
  if (['EACCES', 'EPERM'].includes(error?.code) || message.includes('Permission denied')) return 'permission-refused'
  if (message.includes('Protocol error')) return 'cdp-protocol-refused'
  if (message.includes('process did exit')) {
    if (message.includes('signal=SIGTRAP')) return 'browser-process-sigtrap'
    if (message.includes('signal=SIGABRT')) return 'browser-process-sigabrt'
    if (/exitCode=1(?=[,\s]|$)/.test(message)) return 'browser-process-exit1'
  }
  if (targetClosed || message.includes('Target page, context or browser has been closed')) return 'browser-closed'
  return 'launch-unknown'
}
const hardTimer = setTimeout(() => { timedOut = true; process.stdin.destroy() }, 540000)
hardTimer.unref()

async function* commands() {
  let line = Buffer.alloc(0), count = 0
  for await (const chunk of process.stdin) {
    // Parent sends one bounded command and waits for its reply before the next.
    requireTrue(chunk.length <= WIRE)
    let start = 0
    for (let i = 0; i < chunk.length; i++) {
      if (chunk[i] !== 10) continue
      requireTrue(line.length + i - start + 1 <= WIRE && ++count <= STAGES.length)
      const bytes = Buffer.concat([line, chunk.subarray(start, i)])
      requireTrue(bytes.length > 0 && bytes[bytes.length - 1] !== 13)
      line = Buffer.alloc(0); start = i + 1
      phase = 'protocol'
      yield JSON.parse(decoder.decode(bytes))
    }
    requireTrue(line.length + chunk.length - start + 1 <= WIRE)
    line = Buffer.concat([line, chunk.subarray(start)])
  }
  requireTrue(line.length === 0 && !timedOut)
}
async function reply(value) {
  const line = JSON.stringify(value) + '\n'
  requireTrue(Buffer.byteLength(line) <= WIRE)
  await new Promise((resolve, reject) => process.stdout.write(line, error => error ? reject(error) : resolve()))
}
function observe(target) {
  target.on('pageerror', () => { fault = true })
  target.on('request', request => {
    if (!connection || !request.url().startsWith(connection.api_origin + '/api/')) return
    const url = new URL(request.url())
    if (request.method() !== 'POST' || !/\/(plan|start)(?:\/|$)/.test(url.pathname)) return
    const key = request.method() + ' ' + url.pathname
    mutations.set(key, (mutations.get(key) || 0) + 1)
    // A duplicate plan/start is an acceptance failure, never a retry strategy.
    if (mutations.get(key) !== 1) fault = true
  })
}
async function enabled(locator) {
  await locator.waitFor({ state: 'visible', timeout: 15000 })
  // Trial performs actionability checks without dispatching a click.
  await locator.click({ trial: true, timeout: 15000 })
  requireTrue(await locator.isEnabled())
}
async function settled(target = page) {
  await target.waitForFunction(() => {
    const button = document.querySelector('.graph-refresh')
    return button && !button.disabled
  }, null, { timeout: 15000 })
  clock()
}
async function exchange(target, route, method, action, cap, status = 200) {
  clock()
  const methods = Array.isArray(method) ? method : [method]
  const pending = target.waitForResponse(response => response.url() === connection.api_origin + route && methods.includes(response.request().method()), { timeout: 15000 })
  // Attach rejection handlers before a fallible action, so failed clicks cannot
  // leave an unhandled waiter or permit a second dispatch.
  const waiting = pending.then(response => ({ response }), () => ({ failed: true }))
  await action()
  const result = await waiting
  requireTrue(!result.failed && result.response.status() === status)
  if (status !== 200) return { status }
  const response = result.response, headers = response.headers()
  // This fixture is a trusted local Werkzeug host with explicit plain framing.
  // Refuse missing length/compression before asking Playwright for body bytes.
  requireTrue(/^\d+$/.test(headers['content-length'] || '') && !headers['content-encoding'])
  const length = Number(headers['content-length']); integer(length, cap, 1)
  requireTrue(/^application\/json(?:\s*;|$)/i.test(headers['content-type'] || ''))
  const body = await within(response.body())
  requireTrue(body.length === length && body.length <= cap)
  const envelope = JSON.parse(decoder.decode(body))
  requireTrue(envelope && envelope.success === true && envelope.data && typeof envelope.data === 'object')
  const raw = response.request().postData()
  if (raw !== null) requireTrue(Buffer.byteLength(raw) <= WIRE)
  const request = raw === null ? null : JSON.parse(raw)
  clock()
  return { value: envelope.data, request, request_sha256: raw === null ? null : sha(raw) }
}
const graphRoute = () => `/api/graph/data/${connection.graph_id}`
const sourceRoute = (name, revision) => `/api/source/${name}/${connection.graph_id}${revision ? '/' + revision : ''}`
const prepRoute = method => `/api/preparation/${connection.graph_id}/${method}`
const nativeRoute = method => `/api/native-launch/${method}/${connection.graph_id}`
const reportRoute = method => `/api/connected-report/${method}/${connection.graph_id}`
function prepReference(value) { uuid(value.operation_id); digest(value.plan_sha256); return { operation_id: value.operation_id, plan_sha256: value.plan_sha256 } }
function launchReference(value) { uuid(value.request.run_id); digest(value.launch_sha256); return { launch_id: value.request.run_id, launch_sha256: value.launch_sha256 } }
function reportReference(value) { uuid(value.report_id); digest(value.plan_sha256); return { report_id: value.report_id, plan_sha256: value.plan_sha256 } }
function sourceProof() { return { source_revisions: orderedSources.map(s => s.source_revision), source_sha256: orderedSources.map(s => s.text_sha256) } }
function boundPreparation(value) {
  requireTrue(value.schema_version === 2 && value.source.source_revision === pdf.source_revision)
  requireTrue(value.sources?.length === 4 && same(value.sources.map(s => s.source_revision), sourceProof().source_revisions))
  requireTrue(same(value.sources.map(s => s.source_sha256), sourceProof().source_sha256))
  requireTrue(value.sources.every((s, i) => s.source_name === orderedSources[i].source_name))
  requireTrue(value.source.source_sha256 === orderedSources[0].text_sha256)
  if (preparation) requireTrue(same(prepReference(value), prepReference(preparation)))
}
function boundLaunch(value) {
  requireTrue(same({ operation_id: value.preparation.operation_id, plan_sha256: value.preparation.plan_sha256 }, prepReference(preparation)))
  requireTrue(value.preparation.simulation_id === preparation.receipt.simulation_id && value.preparation.artifact_sha256 === preparation.receipt.artifact_sha256)
  if (launch) requireTrue(same(launchReference(value), launchReference(launch)))
}
function boundReport(value) {
  // V2 is the selection request; the current public report DTO remains V1.
  requireTrue(value.schema_version === 1 && same(value.binding.source_set.source_revisions, sourceProof().source_revisions))
  requireTrue(value.binding.source.source_revision === pdf.source_revision && value.binding.source.source_sha256 === orderedSources[0].text_sha256)
  requireTrue(value.binding.native.run_id === launch.request.run_id && value.binding.native.evidence_sha256 === launch.receipt.evidence_sha256)
  requireTrue(value.binding.preparation.operation_id === preparation.operation_id && value.binding.preparation.plan_sha256 === preparation.plan_sha256)
  if (report) requireTrue(same(reportReference(value), reportReference(report)))
}
async function prepareBrowser(command) {
  keys(command, ['stage', 'connection', 'sources', 'profile_root', 'pdf'])
  keys(command.connection, ['api_origin', 'graph_id', 'token'])
  requireTrue(/^http:\/\/127\.0\.0\.1:[1-9][0-9]{0,4}$/.test(command.connection.api_origin))
  requireTrue(new URL(command.connection.api_origin).port && Number(new URL(command.connection.api_origin).port) <= 65535)
  requireTrue(/^[A-Za-z0-9_-]{1,128}$/.test(command.connection.graph_id))
  text(command.connection.token, 505)
  requireTrue(command.connection.token.trim() === command.connection.token && /^[\x21-\x7e]+$/.test(command.connection.token))
  requireTrue(Array.isArray(command.sources) && command.sources.length === 4)
  for (const item of command.sources) { keys(item, ['source_revision', 'source_name']); uuid(item.source_revision); text(item.source_name, 1024) }
  requireTrue(new Set(command.sources.map(s => s.source_revision)).size === 4)
  keys(command.pdf, ['source_revision', 'size', 'sha256']); uuid(command.pdf.source_revision); integer(command.pdf.size, 2097152, 1); digest(command.pdf.sha256)
  requireTrue(command.sources.some(s => s.source_revision === command.pdf.source_revision))
  requireTrue(process.platform === 'linux' && process.versions.node === '24.14.1' && !process.env.DEBUG && !process.env.PWDEBUG && !process.env.NODE_OPTIONS)
  for (const selector of ['NEXAWEAVE_RESEARCH_BROWSER_NODE', 'NEXAWEAVE_RESEARCH_BROWSER_CHROME']) {
    requireTrue(typeof process.env[selector] === 'string' && path.isAbsolute(process.env[selector]))
    const resolved = await realpath(process.env[selector]); await physical(resolved)
    if (selector.endsWith('_NODE')) requireTrue(resolved === await realpath(process.execPath))
  }
  await physical(command.profile_root, true)
  requireTrue((await readdir(command.profile_root)).length === 0)
  privateRoot = command.profile_root
  sessionRoot = await mkdtemp(path.join(privateRoot, 'research-')); inside(privateRoot, sessionRoot)
  const directories = ['downloads', 'home', 'cache', 'config', 'temp']
  for (const name of directories) await mkdir(path.join(sessionRoot, name), { mode: 0o700 })
  downloadRoot = path.join(sessionRoot, 'downloads')
  const environment = {}
  for (const key of ['PATH', 'LANG', 'LC_ALL', 'TZ', 'SYSTEMROOT']) if (process.env[key]) environment[key] = process.env[key]
  Object.assign(environment, { HOME: path.join(sessionRoot, 'home'), USERPROFILE: path.join(sessionRoot, 'home'), XDG_CACHE_HOME: path.join(sessionRoot, 'cache'), XDG_CONFIG_HOME: path.join(sessionRoot, 'config'), TMPDIR: path.join(sessionRoot, 'temp'), TMP: path.join(sessionRoot, 'temp'), TEMP: path.join(sessionRoot, 'temp'), BROWSER: 'none', CI: 'true' })
  connection = command.connection; sources = command.sources; pdf = command.pdf
  phase = 'browser-launch'
  const temporaryDirectory = path.join(sessionRoot, 'temp')
  await physical(temporaryDirectory, true)
  // Playwright's own automatic profile/artifacts use Node's os.tmpdir(), not
  // the env passed only to Chrome. Bind this private driver process until close.
  temporaryEnvironment = ['TMPDIR', 'TMP', 'TEMP'].map(key => ({ key, present: Object.hasOwn(process.env, key), value: process.env[key] }))
  for (const saved of temporaryEnvironment) process.env[saved.key] = temporaryDirectory
  const startupDeadline = Date.now() + 30000
  const startupRemaining = () => { const remaining = startupDeadline - Date.now(); requireTrue(remaining > 0); return remaining }
  launchStep = 'launch'; launchAttempted = true
  browser = await chromium.launch({
    executablePath: process.env.NEXAWEAVE_RESEARCH_BROWSER_CHROME,
    channel: 'chrome',
    headless: true, chromiumSandbox: true,
    downloadsPath: downloadRoot, env: environment, timeout: 30000,
    args: ['--no-first-run', '--no-default-browser-check', '--disable-sync', '--no-startup-window'],
  })
  launchReturned = true; launchStep = 'context'
  const contextBudget = startupRemaining()
  context = await within(browser.newContext({ acceptDownloads: true }), contextBudget)
  context.setDefaultTimeout(15000); context.setDefaultNavigationTimeout(30000)
  launchStep = 'page'
  const pageBudget = Math.min(15000, startupRemaining())
  page = await within(context.newPage(), pageBudget); observe(page)
  phase = 'page-load'
  await page.goto(FRONTEND + '/research', { waitUntil: 'domcontentloaded' })
  await page.locator('#workbench-language').selectOption('en')
}
async function connectForm(target, token = connection.token, expectedStatus = 200) {
  phase = 'graph-connect'
  await target.locator('#api-origin').fill(connection.api_origin)
  await target.locator('#graph-id').fill(connection.graph_id)
  await target.locator('#bearer-token').fill(token)
  const received = await exchange(target, graphRoute(), expectedStatus === 403 ? ['OPTIONS', 'GET'] : 'GET', () => target.locator('.connection-form button[type=submit]').click(), 2098176, expectedStatus)
  await target.waitForFunction(() => document.querySelector('#bearer-token')?.value === '', null, { timeout: 15000 })
  requireTrue(await target.locator('#bearer-token').inputValue() === '')
  if (expectedStatus === 200) {
    await settled(target)
    requireTrue(received.value.graph_id === connection.graph_id)
  } else {
    await target.locator('.connection-form button[type=submit]').waitFor({ state: 'visible' })
    requireTrue(await target.locator('.graph-refresh').count() === 0)
  }
  return received
}
async function hashDownload(download, name, cap) {
  requireTrue(await download.failure() === null)
  const saved = path.join(downloadRoot, name); inside(downloadRoot, saved)
  await download.saveAs(saved)
  await physical(saved)
  const file = await open(saved, 'r')
  try {
    const before = await file.stat(); requireTrue(before.isFile()); integer(before.size, cap, 1)
    const hashing = createHash('sha256'), buffer = Buffer.alloc(32768); let total = 0
    while (true) {
      const { bytesRead } = await file.read(buffer, 0, Math.min(buffer.length, cap + 1 - total), null)
      if (bytesRead === 0) break
      total += bytesRead; requireTrue(total <= cap); hashing.update(buffer.subarray(0, bytesRead))
    }
    const after = await file.stat(); requireTrue(total === before.size && after.size === before.size && after.mtimeMs === before.mtimeMs && after.ino === before.ino)
    return { size: total, sha256: hashing.digest('hex') }
  } finally { await file.close(); await download.delete() }
}
async function readPdf() {
  phase = 'pdf-metadata'
  await page.locator('#source-original-revision').fill(pdf.source_revision)
  const panel = page.locator('.source-library .source-form').filter({ has: page.locator('#source-original-revision') })
  const lookupButton = panel.locator('button').nth(0)
  await enabled(lookupButton)
  const metadata = await exchange(page, sourceRoute('original-metadata', pdf.source_revision), 'GET', () => lookupButton.click(), 4195328)
  requireTrue(metadata.value.source.source_revision === pdf.source_revision && metadata.value.binary.byte_length === pdf.size && metadata.value.binary.sha256 === pdf.sha256)
  phase = 'pdf-download'
  // Download is conditional on asynchronous metadata admission and Vue render.
  // Its fixed second position cannot alias the always-present Lookup button.
  const downloadButton = panel.locator('button').nth(1)
  await enabled(downloadButton)
  const pending = page.waitForEvent('download', { timeout: 15000 }).then(download => ({ download }), () => ({ failed: true }))
  const content = await exchange(page, sourceRoute('original', pdf.source_revision), 'GET', () => downloadButton.click(), 4195328)
  requireTrue(content.value.source.source_revision === pdf.source_revision && content.value.binary.byte_length === pdf.size && content.value.binary.sha256 === pdf.sha256)
  const result = await pending; requireTrue(!result.failed)
  const proof = await hashDownload(result.download, stage === 'connect' ? 'original-before.pdf' : 'original-after.pdf', 2097152)
  requireTrue(proof.size === pdf.size && proof.sha256 === pdf.sha256)
  return proof
}
async function connect(command) {
  await prepareBrowser(command); await connectForm(page)
  phase = 'source-catalog'
  const catalog = await exchange(page, sourceRoute('library'), 'GET', () => page.locator('.source-library > .source-actions button').first().click(), 4195328)
  requireTrue(Array.isArray(catalog.value.sources) && catalog.value.sources.length >= 4 && catalog.value.sources.length <= 20 && catalog.value.has_more === false)
  for (const item of catalog.value.sources) {
    const expected = sources.find(s => s.source_revision === item.source_revision)
    if (expected) requireTrue(item.source_name === expected.source_name)
    uuid(item.source_revision); digest(item.text_sha256); uuid(item.project_id)
  }
  requireTrue(new Set(catalog.value.sources.map(s => s.source_revision)).size === catalog.value.sources.length && new Set(catalog.value.sources.map(s => s.project_id)).size === 1)
  // The real library also contains the seed. Retain every catalog/UI row while
  // deriving only the explicitly supplied four-document preparation selection.
  const selectedSources = catalog.value.sources.filter(s => sources.some(expected => expected.source_revision === s.source_revision))
  requireTrue(selectedSources.length === 4 && sources.every(expected => selectedSources.some(s => s.source_revision === expected.source_revision)))
  orderedSources = [selectedSources.find(s => s.source_revision === pdf.source_revision), ...selectedSources.filter(s => s.source_revision !== pdf.source_revision)]
  phase = 'source-inspect'
  const item = page.locator('.source-list > li').filter({ hasText: pdf.source_revision })
  await item.waitFor({ state: 'visible', timeout: 15000 })
  requireTrue(await item.count() === 1)
  await enabled(item.locator('button').first())
  const inspected = await exchange(page, sourceRoute('item', pdf.source_revision), 'GET', () => item.locator('button').first().click(), 4195328)
  requireTrue(inspected.value.source.source_revision === pdf.source_revision && inspected.value.source.text_sha256 === orderedSources[0].text_sha256)
  await enabled(page.locator('.preparation-workbench .review'))
  const original = await readPdf()
  return { reference: { display_graph_id: connection.graph_id, project_id: orderedSources[0].project_id, source_revisions: sourceProof().source_revisions }, proof: { ...sourceProof(), token_cleared: true, pdf: original } }
}
function commandRequest(command, required = false) {
  keys(command, ['stage'], ['request'])
  if (required) requireTrue(command.request && Object.getPrototypeOf(command.request) === Object.prototype)
  else requireTrue(command.request === undefined || command.request === null)
  return command.request
}
async function preparationPlan(command) {
  const request = commandRequest(command, true)
  keys(request, ['schema_version', 'operation_id', 'source_revision', 'source_revisions', 'options']); uuid(request.operation_id)
  requireTrue(request.schema_version === 2 && request.source_revision === pdf.source_revision && same(request.source_revisions, sourceProof().source_revisions))
  const options = request.options
  keys(options, ['types', 'max_agents', 'seed', 'platforms', 'max_rounds', 'simulation_requirement'])
  integer(options.max_agents, 100, 1); integer(options.seed, 4294967295); integer(options.max_rounds, 24, 1); text(options.simulation_requirement, 32768)
  requireTrue([['twitter'], ['reddit'], ['twitter', 'reddit']].some(p => same(p, options.platforms)))
  requireTrue(options.types === null || Array.isArray(options.types) && options.types.length > 0 && options.types.length <= 50 && new Set(options.types).size === options.types.length)
  const scope = page.locator('.preparation-workbench')
  const extras = request.source_revisions.slice(1)
  const choices = await scope.locator('.preparation-source-checkbox').evaluateAll(nodes => nodes.map(n => ({ revision: n.value, checked: n.checked })))
  requireTrue(extras.length === 3 && choices.every(choice => !choice.checked))
  requireTrue(extras.every(revision => choices.filter(choice => choice.revision === revision).length === 1))
  requireTrue(same(choices.filter(choice => extras.includes(choice.revision)).map(choice => choice.revision), extras))
  for (const revision of request.source_revisions.slice(1)) await scope.locator(`.preparation-source-checkbox[value="${revision}"]`).check()
  requireTrue(same(await scope.locator('.preparation-source-checkbox:checked').evaluateAll(nodes => nodes.map(n => n.value)), extras))
  if (options.types !== null) for (const label of options.types) {
    requireTrue(/^[A-Za-z_][A-Za-z0-9_]{0,63}$/.test(label))
    await scope.locator(`.type-choices input[value="${label}"]`).check()
  }
  await scope.locator('#preparation-maximum').fill(String(options.max_agents))
  await scope.locator('#preparation-seed').fill(String(options.seed))
  await scope.locator('#preparation-rounds').fill(String(options.max_rounds))
  await scope.getByLabel('Twitter', { exact: true }).setChecked(options.platforms.includes('twitter'))
  await scope.getByLabel('Reddit', { exact: true }).setChecked(options.platforms.includes('reddit'))
  await scope.locator('#preparation-requirement').fill(options.simulation_requirement)
  const observed = await exchange(page, prepRoute('plan'), 'POST', () => scope.locator('.review').click(), 263168)
  requireTrue(observed.request.schema_version === 2 && observed.request.source_revision === pdf.source_revision && same(observed.request.source_revisions, request.source_revisions) && same(observed.request.options, options))
  requireTrue(observed.value.state === 'planned'); boundPreparation(observed.value)
  await enabled(scope.locator('.start')); preparation = observed.value
  return { reference: prepReference(preparation), proof: { ...sourceProof(), request_sha256: observed.request_sha256, planned: true } }
}
async function preparationStart(command) {
  commandRequest(command)
  const observed = await exchange(page, prepRoute('start'), 'POST', () => page.locator('.preparation-workbench .start').click(), 263168)
  requireTrue(same(prepReference(observed.value), prepReference(preparation))); boundPreparation(observed.value)
  requireTrue(!['failed', 'cancelled', 'uncertain'].includes(observed.value.state)); preparation = observed.value
  await enabled(page.locator('.preparation-workbench .refresh'))
  return { reference: prepReference(preparation), proof: { dispatches: 1, state: preparation.state } }
}
async function preparationReady(command) {
  commandRequest(command)
  const observed = await exchange(page, prepRoute('status'), 'POST', () => page.locator('.preparation-workbench .refresh').click(), 263168)
  boundPreparation(observed.value); requireTrue(observed.value.state === 'ready' && observed.value.receipt)
  preparation = observed.value; digest(preparation.receipt.artifact_sha256)
  await enabled(page.locator('.native-launch .review'))
  return { reference: { ...prepReference(preparation), simulation_id: preparation.receipt.simulation_id, artifact_sha256: preparation.receipt.artifact_sha256 }, proof: sourceProof() }
}
async function nativeStart(command) {
  const request = commandRequest(command, true)
  keys(request, ['schema_version', 'launch_id', 'preparation']); uuid(request.launch_id)
  requireTrue(request.schema_version === 1 && same(request.preparation, prepReference(preparation)))
  const scope = page.locator('.native-launch')
  const planned = await exchange(page, nativeRoute('plan'), 'POST', () => scope.locator('.review').click(), 65664)
  boundLaunch(planned.value); requireTrue(planned.value.state === 'planned' && same(planned.request.preparation, request.preparation))
  launch = planned.value; await enabled(scope.locator('.start'))
  const started = await exchange(page, nativeRoute('start'), 'POST', () => scope.locator('.start').click(), 65664)
  boundLaunch(started.value); requireTrue(!['failed', 'cancelled', 'uncertain'].includes(started.value.state)); launch = started.value
  await enabled(scope.locator('.refresh'))
  return { reference: launchReference(launch), proof: { preparation: prepReference(preparation), state: launch.state, dispatches: 1 } }
}
async function nativeReady(command) {
  commandRequest(command)
  const scope = page.locator('.native-launch')
  const observed = await exchange(page, nativeRoute('status'), 'POST', () => scope.locator('.refresh').click(), 65664)
  boundLaunch(observed.value); requireTrue(observed.value.state === 'completed' && observed.value.receipt?.outcome === 'completed')
  launch = observed.value; digest(launch.receipt.evidence_sha256)
  await enabled(scope.locator('.use-report')); await scope.locator('.use-report').click()
  await enabled(page.locator('.connected-reports .report-review'))
  requireTrue(await page.locator('.report-source-checkbox').count() === 0)
  requireTrue(await page.locator('.report-prepared-sources li').count() === 4)
  return { reference: { ...launchReference(launch), evidence_sha256: launch.receipt.evidence_sha256 }, proof: { ...sourceProof(), preparation: prepReference(preparation) } }
}
async function reportStart(command) {
  const request = commandRequest(command, true)
  keys(request, ['schema_version', 'report_id', 'launch_id', 'launch_sha256', 'requirement', 'output_language', 'native_windows', 'source_revisions']); uuid(request.report_id)
  requireTrue(request.schema_version === 2 && request.launch_id === launch.request.run_id && request.launch_sha256 === launch.launch_sha256 && same(request.source_revisions, sourceProof().source_revisions))
  text(request.requirement, 16000); requireTrue(['en', 'zh', 'ms'].includes(request.output_language))
  const scope = page.locator('.connected-reports')
  await scope.locator('#report-requirement').fill(request.requirement)
  await scope.locator('#report-output-language').selectOption(request.output_language)
  if (request.native_windows !== null) {
    requireTrue(Array.isArray(request.native_windows) && request.native_windows.length > 0 && request.native_windows.length <= 2)
    requireTrue(new Set(request.native_windows.map(w => w.platform)).size === request.native_windows.length)
    for (const window of request.native_windows) { keys(window, ['platform', 'offset', 'count']); requireTrue(launch.request.platforms.includes(window.platform)); integer(window.offset, 9999); integer(window.count, 1000, 1); requireTrue(window.offset + window.count <= 10000) }
    await scope.locator('#report-coverage').selectOption('selected')
    for (const platform of launch.request.platforms) {
      const row = scope.locator('.window').filter({ has: page.locator(`#report-offset-${platform}`) })
      const window = request.native_windows.find(w => w.platform === platform)
      await row.locator('input[type=checkbox]').setChecked(!!window)
      if (window) { await row.locator(`#report-offset-${platform}`).fill(String(window.offset)); await row.locator(`#report-count-${platform}`).fill(String(window.count)) }
    }
  } else await scope.locator('#report-coverage').selectOption('complete')
  const planned = await exchange(page, reportRoute('plan'), 'POST', () => scope.locator('.report-review').click(), 262272)
  boundReport(planned.value); requireTrue(planned.value.state === 'planned')
  for (const key of ['schema_version', 'launch_id', 'launch_sha256', 'requirement', 'output_language', 'native_windows', 'source_revisions']) requireTrue(same(planned.request[key], request[key]))
  report = planned.value; await enabled(scope.locator('.report-start'))
  const started = await exchange(page, reportRoute('start'), 'POST', () => scope.locator('.report-start').click(), 262272)
  boundReport(started.value); requireTrue(!['failed', 'cancelled', 'uncertain'].includes(started.value.state)); report = started.value
  await enabled(scope.locator('.report-refresh'))
  return { reference: reportReference(report), proof: { ...sourceProof(), native_evidence_sha256: launch.receipt.evidence_sha256, dispatches: 1 } }
}
async function reportDownload(kind, expected) {
  const scope = page.locator('.connected-reports')
  await scope.locator('.download-controls select').first().selectOption(kind)
  const observed = await exchange(page, reportRoute('download'), 'POST', () => scope.locator('.report-download').click(), 4194304)
  const artifact = observed.value.artifact
  requireTrue(observed.value.report_id === report.report_id && observed.value.plan_sha256 === report.plan_sha256 && artifact.name === expected.name && artifact.sha256 === expected.sha256)
  await enabled(scope.locator('.report-save'))
  const waiting = page.waitForEvent('download', { timeout: 15000 }).then(download => ({ download }), () => ({ failed: true }))
  await scope.locator('.report-save').click(); const received = await waiting; requireTrue(!received.failed)
  const downloaded = await hashDownload(received.download, kind + '-verified.bin', 2097152)
  requireTrue(downloaded.size === expected.size && downloaded.sha256 === expected.sha256)
  return downloaded
}
async function reportRead(command) {
  commandRequest(command)
  const scope = page.locator('.connected-reports')
  const refreshed = await exchange(page, reportRoute('status'), 'POST', () => scope.locator('.report-refresh').click(), 262272)
  boundReport(refreshed.value); requireTrue(refreshed.value.state === 'completed' && refreshed.value.receipt && refreshed.value.manifest)
  report = refreshed.value; digest(report.receipt_sha256)
  await enabled(scope.locator('.report-read'))
  const read = await exchange(page, reportRoute('read'), 'POST', () => scope.locator('.report-read').click(), 4194304)
  boundReport(read.value.report); requireTrue(read.value.report.receipt_sha256 === report.receipt_sha256)
  await scope.locator('.narrative').waitFor({ state: 'visible' })
  const reportFile = report.manifest.files.find(f => f.name === 'full_report.md')
  const nativeFile = report.manifest.files.find(f => f.name === 'native_evidence.json')
  requireTrue(reportFile && nativeFile)
  requireTrue(sha(Buffer.from(read.value.content, 'utf8')) === reportFile.sha256)
  const narrative = await reportDownload('report', reportFile)
  const native = await reportDownload('native_evidence', nativeFile)
  return { reference: { ...reportReference(report), receipt_sha256: report.receipt_sha256 }, proof: { ...sourceProof(), native_evidence_sha256: launch.receipt.evidence_sha256, report_download: narrative, native_evidence_download: native } }
}
async function pdfAfterRestart(command) {
  commandRequest(command)
  await page.locator('.connection-form button[type=button]').last().click()
  await enabled(page.locator('.connection-form button[type=submit]'))
  // This is a real UI GET with a deliberately wrong credential, not an API call.
  const invalid = connection.token === 'research-browser-invalid' ? 'research-browser-invalid-2' : 'research-browser-invalid'
  await connectForm(page, invalid, 401)
  await connectForm(page)
  const original = await readPdf()
  // Use a second ordinary frontend origin to observe actual server refusal.
  // Do not override Origin headers, disable CORS, or ignore browser security.
  const denied = await context.newPage(); observe(denied)
  try {
    phase = 'page-load'
    await denied.goto('http://localhost:4317/research', { waitUntil: 'domcontentloaded' })
    await denied.locator('#workbench-language').selectOption('en')
    await connectForm(denied, connection.token, 403)
  } finally { await denied.close() }
  requireTrue(await page.locator('#bearer-token').inputValue() === '')
  return { reference: { source_revision: pdf.source_revision }, proof: { pdf: original, auth_denied: true, origin_denied: true, token_cleared: true } }
}
async function cleanup() {
  if (closing) return closing
  closing = (async () => {
    const errors = [], cleanupDeadline = Date.now() + 15000
    const remaining = () => Math.max(1, cleanupDeadline - Date.now())
    try {
      // Reserve browser-close time inside the SAME overall 15-second budget.
      // A context error must never skip the independent browser close attempt.
      if (context) {
        try { await within(context.close(), Math.min(5000, remaining())); context = null }
        catch (error) { errors.push(new Error('research_browser_context_close_failed', { cause: error })) }
      }
      if (browser) {
        try { await within(browser.close(), Math.max(1, Math.min(8000, remaining() - 2000))); browser = null }
        catch (error) { errors.push(new Error('research_browser_browser_close_failed', { cause: error })) }
      }
      // Launch rejection without a returned handle cannot prove Chrome closure.
      // Preserve its private files for the parent's existing owned-tree cleanup.
      if (launchAttempted && !launchReturned) errors.push(new Error('research_browser_launch_closure_unconfirmed'))
      if (!errors.length && !context && !browser && sessionRoot) {
        try {
          requireTrue(Date.now() < cleanupDeadline)
          await within((async () => {
            await physical(privateRoot, true); await physical(sessionRoot, true); inside(privateRoot, sessionRoot)
            await rm(sessionRoot, { recursive: true, force: false })
          })(), Math.max(1, remaining() - 1000))
          sessionRoot = null
        } catch (error) { errors.push(new Error('research_browser_private_cleanup_failed', { cause: error })) }
      }
    } finally {
      if (temporaryEnvironment) {
        for (const saved of temporaryEnvironment) {
          try { if (saved.present) process.env[saved.key] = saved.value; else delete process.env[saved.key] }
          catch (error) { errors.push(new Error('research_browser_temp_restore_failed', { cause: error })) }
        }
        temporaryEnvironment = null
      }
      connection = null
    }
    // Keep ordered primary/secondary errors internally; protocol emits only its
    // existing stable cleanup error and the separate primary stage failure.
    if (errors.length) throw new AggregateError(errors, 'research_browser_cleanup_failed')
    requireTrue(!context && !browser && !sessionRoot && Date.now() < cleanupDeadline)
    return { reference: {}, proof: { context_closed: true, browser_closed: true, private_tree_removed: true } }
  })()
  return closing
}
const handlers = { connect, 'preparation-plan': preparationPlan, 'preparation-start': preparationStart, 'preparation-ready': preparationReady, 'native-start': nativeStart, 'native-ready': nativeReady, 'report-start': reportStart, 'report-read': reportRead, 'pdf-read-after-restart': pdfAfterRestart, close: async command => { commandRequest(command); return cleanup() } }
try {
  for await (const command of commands()) {
    stage = STAGES[index] || 'protocol'; phase = stage === 'close' ? 'close' : 'protocol'
    requireTrue(command?.stage === stage); clock()
    const result = await handlers[stage](command)
    clock(); await reply({ stage, ok: true, ...result }); index++
    if (stage === 'close') break
  }
  requireTrue(index === STAGES.length)
} catch (error) {
  process.exitCode = 1
  const failure = { stage, ok: false, error: 'research_browser_failed', phase }
  if (phase === 'browser-launch') failure.launch_error = launchError(error)
  try { await reply(failure) } catch { /* stdout unavailable */ }
} finally {
  clearTimeout(hardTimer)
  try { await cleanup() } catch { process.exitCode = 1; try { await reply({ stage: 'close', ok: false, error: 'research_browser_cleanup_failed', phase: 'close' }) } catch { /* stdout unavailable */ } }
  process.stdin.destroy()
}
