// Receipt-bound reads only. This channel never starts an engine or a model.
import { WorkbenchError, uniqueJson } from './workbench.js'
import { nativeLaunchIdentity, validateNativeLaunchResult } from './nativeLaunch.js'
import { sha256 } from './sourceLibrary.js'
const encoder = new TextEncoder()
const fail = (code = 'invalid_reply') => { throw new WorkbenchError(code) }
const fields = (v, keys) => { if (!v || Object.getPrototypeOf(v) !== Object.prototype || Object.keys(v).sort().join('|') !== keys.split(' ').sort().join('|')) fail() }
const integer = (v, max, min = 0) => { if (!Number.isSafeInteger(v) || v < min || v > max) fail() }
const hash = v => { if (typeof v !== 'string' || !/^[0-9a-f]{64}$/.test(v)) fail() }
const validText = v => { if (typeof v !== 'string' || /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(v)) fail() }
const ordered = v => Array.isArray(v) ? v.map(ordered) : v && typeof v === 'object' ? Object.fromEntries(Object.keys(v).sort().map(k => [k, ordered(v[k])])) : v
const ascii = v => JSON.stringify(ordered(v)).replace(/[\u007f-\uffff]/g, c => '\\u' + c.charCodeAt(0).toString(16).padStart(4, '0'))
// Detach before the first digest await. Do not normalize raw record evidence.
function detach(v, depth = 0) {
  if (depth > 20) fail()
  if (v === null || typeof v === 'boolean') return v
  if (typeof v === 'number') { if (!Number.isFinite(v)) fail(); return v }
  if (typeof v === 'string') { validText(v); if (encoder.encode(v).length > 262144) fail(); return v }
  if (Array.isArray(v)) { if (v.length > 10000) fail(); return Array.from(v, x => detach(x, depth + 1)) }
  if (!v || Object.getPrototypeOf(v) !== Object.prototype || Object.keys(v).length > 1000) fail()
  return Object.fromEntries(Object.entries(v).map(([k, x]) => { validText(k); return [k, detach(x, depth + 1)] }))
}
export const OBSERVATION_ERROR_STATUS = Object.freeze({ invalid_request: 400, not_found: 404, unauthorized: 401, origin_denied: 403, conflict: 409, tombstoned: 410, busy: 503, result_too_large: 413, observations_unavailable: 503, evidence_invalid: 502, invalid_reply: 502, internal_error: 500 })
export function nativeObservationsPayload(value) {
  try {
    fields(value, 'schema_version launch_id launch_sha256 platform offset limit')
    if (value.schema_version !== 1 || typeof value.launch_id !== 'string' || !/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(value.launch_id) || !['twitter', 'reddit'].includes(value.platform)) fail()
    hash(value.launch_sha256); integer(value.offset, 10000); integer(value.limit, 20, 1)
    const raw = JSON.stringify(value); if (encoder.encode(raw).length > 4096) fail()
    return raw
  } catch { fail('invalid_request') }
}
export async function nativeObservationSelection(value, graph) {
  try {
    value = detach(value); fields(value, 'launch preparation')
    const launch = await validateNativeLaunchResult(value.launch, { graph, payload: nativeLaunchIdentity(value.launch), preparation: value.preparation, known: value.launch })
    if (launch.state !== 'completed' || launch.receipt?.outcome !== 'completed') fail()
    return { launch, preparation: value.preparation }
  } catch { fail() }
}
// JSON syntax/duplicate admission is independent from the byte digest. Large
// finite numeric lexemes remain literal evidence; convenience labels use strings.
export function parseObservationRecord(raw) {
  validText(raw)
  if (encoder.encode(raw).length > 8192 || /[\r\n]/.test(raw)) fail()
  let value
  try { value = uniqueJson(raw) } catch { fail() }
  if (!value || Object.getPrototypeOf(value) !== Object.prototype) fail()
  // Every numeric token must decode to a finite IEEE754 double. Unsafe finite
  // convenience values are omitted below; original evidence is never rewritten.
  // Scan only tokens outside strings after complete syntax admission.
  for (let i = 0; i < raw.length; i++) {
    if (raw[i] === '"') { for (i++; i < raw.length; i++) { if (raw[i] === '\\') i++; else if (raw[i] === '"') break } }
    else if (raw[i] === '-' || /[0-9]/.test(raw[i])) {
      const token = raw.slice(i).match(/^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?/)?.[0]
      if (!token || !Number.isFinite(Number(token))) fail()
      i += token.length - 1
    }
  }
  function inspect(v, depth) {
    if (v && typeof v === 'object') {
      if (depth > 8) fail()
      for (const [k, x] of Object.entries(v)) { validText(k); if (typeof x === 'number' && !Number.isSafeInteger(x)) v[k] = null; else inspect(x, depth + 1) }
    } else if (typeof v === 'string') validText(v)
  }
  inspect(value, 1)
  return value
}
export async function validateNativeObservationsResult(value, { graph, payload, selection, knownPage = null } = {}) {
  try {
    value = detach(value); payload = detach(payload); selection = detach(selection); if (knownPage) knownPage = detach(knownPage)
    nativeObservationsPayload(payload)
    const selected = await nativeObservationSelection(selection, graph)
    if (payload.launch_id !== selected.launch.request.run_id || payload.launch_sha256 !== selected.launch.launch_sha256 || !selected.launch.request.platforms.includes(payload.platform)) fail()
    if (encoder.encode(JSON.stringify(value)).length > 262144) fail()
    fields(value, 'schema_version launch manifest platform offset limit total_records next_offset counts records')
    if (value.schema_version !== 1 || value.platform !== payload.platform || value.offset !== payload.offset || value.limit !== payload.limit) fail()
    const launch = await validateNativeLaunchResult(value.launch, { graph, payload: nativeLaunchIdentity(selected.launch), preparation: selected.preparation, known: selected.launch })
    if (launch.state !== 'completed' || ascii(launch.receipt) !== ascii(selected.launch.receipt)) fail()
    fields(value.manifest, 'schema_version files')
    const names = launch.request.platforms.flatMap(p => [p + '_simulation.db', p + '/actions.jsonl'])
    if (value.manifest.schema_version !== 1 || !Array.isArray(value.manifest.files) || value.manifest.files.length !== names.length) fail()
    value.manifest.files.forEach((file, i) => {
      fields(file, 'name sha256 size'); hash(file.sha256); integer(file.size, 67108864)
      if (file.name !== names[i] || file.name.endsWith('/actions.jsonl') && file.size > 8388608) fail()
    })
    if (await sha256(encoder.encode(ascii(value.manifest))) !== launch.receipt.evidence_sha256) fail()
    integer(value.total_records, 10000)
    if (value.offset > value.total_records || !Array.isArray(value.records) || value.records.length !== Math.min(value.limit, value.total_records - value.offset)) fail()
    const next = value.offset + value.records.length
    if (value.next_offset !== (next < value.total_records ? next : null)) fail()
    fields(value.counts, 'event_records action_records successful_action_records failed_action_records')
    Object.values(value.counts).forEach(n => integer(n, value.total_records))
    if (value.counts.successful_action_records + value.counts.failed_action_records > value.counts.action_records) fail()
    const pageCounts = { event_records: 0, action_records: 0, successful_action_records: 0, failed_action_records: 0 }
    for (const [i, record] of value.records.entries()) {
      fields(record, 'index record_sha256 raw_json'); integer(record.index, 9999); hash(record.record_sha256)
      if (record.index !== value.offset + i) fail()
      const parsed = parseObservationRecord(record.raw_json)
      if (await sha256(encoder.encode(record.raw_json)) !== record.record_sha256) fail()
      if (typeof parsed.event_type === 'string') pageCounts.event_records++
      if (typeof parsed.action_type === 'string') { pageCounts.action_records++; if (parsed.success === true) pageCounts.successful_action_records++; if (parsed.success === false) pageCounts.failed_action_records++ }
    }
    for (const key of Object.keys(pageCounts)) {
      // The undisplayed records must be able to account for the remaining count.
      if (value.counts[key] < pageCounts[key] || value.counts[key] - pageCounts[key] > value.total_records - value.records.length) fail()
    }
    const log = value.manifest.files.find(f => f.name === value.platform + '/actions.jsonl')
    // Every undisplayed object needs at least two bytes ({}), and adjacent
    // physical records need a one-byte LF separator. Known raw lengths replace
    // that two-byte floor; optional CRLF/final newline can only add bytes.
    const minimumLogSize = value.total_records === 0 ? 0 : 3 * value.total_records - 1 + value.records.reduce((n, r) => n + encoder.encode(r.raw_json).length - 2, 0)
    if (value.total_records === 0 && log.size !== 0 || value.total_records > 0 && log.size < minimumLogSize) fail()
    if (knownPage && (knownPage.platform !== value.platform || ascii(knownPage.manifest) !== ascii(value.manifest) || knownPage.total_records !== value.total_records || ascii(knownPage.counts) !== ascii(value.counts) || ascii(knownPage.launch.receipt) !== ascii(value.launch.receipt))) fail()
    return value
  } catch { fail() }
}

export function createNativeObservationsChannel({ fetchImpl, deadlineMs, connection, denied }) {
  let epoch = 0, active = null
  function clear() { epoch++; active?.abort(); active = null }
  async function page(payload, selection, knownPage) {
    const current = connection()
    if (!current) fail('disconnected')
    // A separate finite epoch covers both transport and asynchronous hashes.
    clear()
    const life = epoch, controller = new AbortController(); active = controller
    let timedOut = false, reader, responseBody
    const timer = setTimeout(() => { timedOut = true; controller.abort() }, deadlineMs)
    const owned = () => { if (life !== epoch || controller.signal.aborted) fail(timedOut ? 'deadline' : 'cancelled') }
    const guarded = promise => new Promise((resolve, reject) => {
      const aborted = () => reject(new WorkbenchError(timedOut ? 'deadline' : 'cancelled'))
      if (controller.signal.aborted) { aborted(); return }
      controller.signal.addEventListener('abort', aborted, { once: true })
      Promise.resolve(promise).then(value => {
        if (controller.signal.aborted && value?.body?.cancel) { try { void value.body.cancel().catch(() => {}) } catch { /* discarded */ } }
        resolve(value)
      }, reject).finally(() => controller.signal.removeEventListener('abort', aborted))
    })
    try {
      const raw = nativeObservationsPayload(payload)
      payload = JSON.parse(raw); selection = detach(selection); if (knownPage) knownPage = detach(knownPage)
      const admitted = await guarded(nativeObservationSelection(selection, current.graph)); owned()
      if (payload.launch_id !== admitted.launch.request.run_id || payload.launch_sha256 !== admitted.launch.launch_sha256 || !admitted.launch.request.platforms.includes(payload.platform)) fail('invalid_request')
      const response = await guarded(fetchImpl(`${current.origin}/api/native-observations/page/${current.graph}`, { method: 'POST', headers: { Authorization: `Bearer ${current.token}`, 'Content-Type': 'application/json' }, body: raw, signal: controller.signal, redirect: 'error', credentials: 'omit', cache: 'no-store', referrerPolicy: 'no-referrer' }))
      responseBody = response.body; owned()
      if ([401, 403].includes(response.status)) { denied(); fail(response.status === 401 ? 'unauthorized' : 'origin_denied') }
      if ([404, 501, 503].includes(response.status) && !/^application\/json(?:\s*;|$)/i.test(response.headers.get('content-type') || '')) fail('observations_unavailable')
      if (response.redirected || response.type === 'opaqueredirect' || !/^application\/json(?:\s*;|$)/i.test(response.headers.get('content-type') || '') || !response.body?.getReader) fail()
      const cap = 262272, length = response.headers.get('content-length')
      if (length && (!/^\d+$/.test(length) || Number(length) > cap)) fail('result_too_large')
      reader = response.body.getReader(); const decoder = new TextDecoder('utf-8', { fatal: true }); let size = 0, body = ''
      while (true) {
        const chunk = await guarded(reader.read()); owned(); if (chunk.done) break
        size += chunk.value.byteLength; if (size > cap) fail('result_too_large')
        body += decoder.decode(chunk.value, { stream: true })
      }
      body += decoder.decode(); owned()
      const value = uniqueJson(body)
      if (!response.ok || value?.success !== true) {
        fields(value, 'success error'); fields(value.error, 'code')
        if (value.success !== false || OBSERVATION_ERROR_STATUS[value.error.code] !== response.status) fail()
        fail(value.error.code)
      }
      fields(value, 'success data')
      const result = await guarded(validateNativeObservationsResult(value.data, { graph: current.graph, payload, selection: admitted, knownPage })); owned()
      return result
    } catch (e) {
      if (e instanceof WorkbenchError && ['unauthorized', 'origin_denied'].includes(e.code)) throw e
      owned()
      if (e instanceof WorkbenchError) throw e
      if (e instanceof SyntaxError || e instanceof TypeError && reader) fail()
      fail('transport_failure')
    } finally {
      clearTimeout(timer); controller.abort()
      if (reader) { try { void reader.cancel().catch(() => {}) } catch { /* discarded */ } reader.releaseLock() }
      else if (responseBody) { try { void responseBody.cancel().catch(() => {}) } catch { /* discarded */ } }
      if (active === controller) active = null
    }
  }
  return { page, clear }
}
