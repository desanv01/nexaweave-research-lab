import { uniqueJson, WorkbenchError } from './workbench.js'
import { newPreparationId, preparationIdentity } from './simulationPreparation.js'
import { nativeLaunchIdentity, nativeLaunchPayload, nativeReadyPreparation, validateNativeLaunchResult, NATIVE_ERROR_STATUS } from './nativeLaunch.js'
import { sha256 } from './sourceLibrary.js'

const encode = new TextEncoder()
const fail = (code = 'invalid_reply') => { throw new WorkbenchError(code) }
const uuid = value => typeof value === 'string' && /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(value)
const hash = value => typeof value === 'string' && /^[0-9a-f]{64}$/.test(value)
const fields = (value, keys) => {
  const expected = keys.split(' ')
  if (!value || Object.getPrototypeOf(value) !== Object.prototype ||
      Object.keys(value).length !== expected.length || expected.some(key => !Object.hasOwn(value, key))) fail()
}
const ordered = value => Array.isArray(value) ? value.map(ordered) : value && typeof value === 'object'
  ? Object.fromEntries(Object.keys(value).sort().map(key => [key, ordered(value[key])])) : value
const canonical = value => JSON.stringify(ordered(value)).replace(/[\u007f-\uffff]/g, unit => '\\u' + unit.charCodeAt(0).toString(16).padStart(4, '0'))
const same = (a, b) => canonical(a) === canonical(b)
const copy = value => JSON.parse(JSON.stringify(value))
const digest = value => sha256(encode.encode(canonical(value)))
const preparations = new WeakMap()

// Only a member validated against the exact settled donor can enter the native validator.
export function verifiedEnsemblePreparation(member, graph) {
  const preparation = preparations.get(member)
  if (preparation && preparation.display_graph_id !== graph) fail()
  return preparation && copy(preparation)
}

export function guardWorkbenchPromise(controller, expired) {
  return promise => new Promise((resolve, reject) => {
    const aborted = () => reject(new WorkbenchError(expired() ? 'deadline' : 'cancelled'))
    if (controller.signal.aborted) { aborted(); return }
    controller.signal.addEventListener('abort', aborted, { once: true })
    Promise.resolve(promise).then(value => {
      if (controller.signal.aborted && value?.body?.cancel) {
        try { void value.body.cancel().catch(() => {}) } catch { /* discard */ }
      }
      resolve(value)
    }, reject).finally(() => controller.signal.removeEventListener('abort', aborted))
  })
}

const preparationErrors = Object.freeze({
  invalid_request: 400, not_found: 404, unauthorized: 401, origin_denied: 403,
  conflict: 409, busy: 503, tombstoned: 410, empty_selection: 422, result_too_large: 413,
  model_calls_disabled: 409, budget_denied: 409, invalid_reply: 502, internal_error: 500,
  preparation_unavailable: 503, preparation_uncertain: 503, preparation_failed: 503,
  preparation_cancelled: 503, timeout: 503, transport_failure: 503,
})

function exactSeeds(seeds, donor) {
  if (!Array.isArray(seeds) || seeds.length !== 2 || seeds[0] === seeds[1] ||
      [seeds[0], seeds[1]].some(seed => !Number.isSafeInteger(seed) || seed < 0 || seed > 4294967295 || seed === donor.options.seed)) fail('invalid_request')
  return [...seeds]
}

async function validateDeclaration(value, donor, payload, previous) {
  fields(value, 'schema_version display_graph_id scope project_revision state idempotency_key platforms donor ensemble_id members')
  fields(value.donor, 'operation_id plan_sha256 seed artifact_sha256')
  if (value.schema_version !== 1 || value.state !== 'ready' || value.display_graph_id !== donor.display_graph_id ||
      value.project_revision !== donor.project_revision || !same(value.scope, donor.scope) || !same(value.platforms, donor.options.platforms) ||
      value.idempotency_key !== payload.idempotency_key || value.donor.seed !== donor.options.seed ||
      value.donor.operation_id !== payload.operation_id || value.donor.plan_sha256 !== payload.plan_sha256 ||
      value.donor.artifact_sha256 !== donor.receipt.artifact_sha256 || !uuid(value.ensemble_id) ||
      !Array.isArray(value.members) || value.members.length !== 2 || previous && !same(previous, value)) fail()

  const ids = new Set([value.ensemble_id, donor.operation_id])
  const artifacts = new Set([donor.receipt.artifact_sha256])
  const plans = new Set([donor.plan_sha256])
  for (const [index, member] of value.members.entries()) {
    fields(member, 'operation_id launch_id plan_sha256 seed receipt')
    if (!uuid(member.operation_id) || !uuid(member.launch_id) || !hash(member.plan_sha256) || member.seed !== payload.seeds[index]) fail()
    ids.add(member.operation_id); ids.add(member.launch_id); plans.add(member.plan_sha256)
    const receipt = member.receipt
    fields(receipt, 'simulation_id artifact_sha256 files')
    if (receipt.simulation_id !== 'sim_' + member.operation_id.replaceAll('-', '') ||
        !hash(receipt.artifact_sha256) || !Array.isArray(receipt.files) || receipt.files.length !== donor.receipt.files.length) fail()
    for (const [i, file] of receipt.files.entries()) {
      fields(file, 'name sha256 size')
      const original = donor.receipt.files[i]
      const rebound = ['state.json', 'simulation_config.json'].includes(original.name)
      if (file.name !== original.name || !hash(file.sha256) || !Number.isSafeInteger(file.size) ||
          file.size < 1 || file.size > 2097152 || (rebound ? file.sha256 === original.sha256 : !same(file, original))) fail()
    }
    artifacts.add(receipt.artifact_sha256)
    const { schema_version, display_graph_id, scope, project_revision, source, options, actors, projection_sha256, sources } = donor
    const identity = { schema_version, display_graph_id, scope, project_revision, operation_id: member.operation_id,
      source, options: { ...options, seed: member.seed }, actors, projection_sha256,
      ...(schema_version === 2 ? { sources } : {}) }
    if (member.plan_sha256 !== await digest(identity) ||
        receipt.artifact_sha256 !== await digest({ schema_version: 1, files: receipt.files })) fail()
    preparations.set(member, { ...identity, plan_sha256: member.plan_sha256, receipt, model_calls_started: false })
  }
  if (ids.size !== 6 || artifacts.size !== 3 || plans.size !== 3) fail()
  return value
}

export function ensembleReviewedToStart(member) {
  const launch = member?.launch
  const ceiling = launch?.ceiling_microusd
  return launch?.state === 'planned' && !member.attempted && launch.cancel_requested === false &&
    launch.authorization?.model_calls_enabled === true && typeof ceiling === 'string' &&
    /^[1-9][0-9]{0,18}$/.test(ceiling) && (ceiling.length < 19 || ceiling <= '9223372036854775807')
}

export function ensembleCanStart(members, index) {
  return ensembleReviewedToStart(members[index]) &&
    (index === 0 ? ensembleReviewedToStart(members[1]) : members[0]?.settled === true)
}

export function createNativeEnsembleChannel({ fetchImpl, deadlineMs, connection, donorFor, denied }) {
  let active = null, admission = null
  const declarations = new Map()
  function cancel() { active?.abort(); active = null }
  function disconnect() { cancel(); admission = null }
  function find(ready) {
    const current = connection()
    if (!current) fail('disconnected')
    const donor = donorFor(ready?.operation_id)
    if (!donor || donor.state !== 'ready' || !donor.model_calls_started || !same(ready, donor)) fail('scope_changed')
    const key = current.origin + '/' + current.graph + '/' + donor.operation_id
    const record = declarations.get(key)
    if (record && !same(record.donor, donor)) fail('scope_changed')
    return { current, donor, key, record }
  }
  function pendingSeeds(ready) {
    try { return find(ready).record?.payload.seeds.slice() ?? null } catch { return null }
  }
  function snapshot(record) {
    return copy({
      ensembleId: record.reply.ensemble_id,
      members: record.reply.members.map((member, i) => ({
        ...member, launch: record.launches[i], attempted: record.attempted[i], settled: i === 0 && record.firstSettled,
      })),
    })
  }
  async function request(context, path, payload, consume, beforeDispatch) {
    if (active) fail('busy')
    const { current, donor } = context
    const controller = new AbortController()
    active = controller
    let expired = false, reader, responseBody
    const timer = setTimeout(() => { expired = true; controller.abort() }, deadlineMs)
    const guarded = guardWorkbenchPromise(controller, () => expired)
    const owned = () => {
      if (active !== controller || controller.signal.aborted || connection() !== current) fail(expired ? 'deadline' : 'cancelled')
      if (donorFor(donor.operation_id) !== donor) fail('scope_changed')
    }
    const native = path.startsWith('/api/native-launch/')
    try {
      owned()
      await guarded(nativeReadyPreparation(donor, current.graph))
      owned()
      beforeDispatch?.()
      const response = await guarded(fetchImpl(current.origin + path, {
        method: 'POST', headers: { Authorization: `Bearer ${current.token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify(payload), signal: controller.signal, redirect: 'error', credentials: 'omit',
        cache: 'no-store', referrerPolicy: 'no-referrer',
      }))
      responseBody = response.body
      owned()
      if (response.status === 401 || response.status === 403) {
        denied()
        fail(response.status === 401 ? 'unauthorized' : 'origin_denied')
      }
      const json = /^application\/json(?:\s*;|$)/i.test(response.headers.get('content-type') || '')
      if (!json && [404, 501, 503].includes(response.status)) fail(native ? 'native_launch_unavailable' : 'preparation_unavailable')
      if (!json || response.redirected || response.type === 'opaqueredirect' || !responseBody?.getReader) fail()
      const cap = native ? 65664 : 263168
      const length = response.headers.get('content-length')
      if (length !== null && (!/^\d+$/.test(length) || Number(length) > cap)) fail('result_too_large')
      reader = responseBody.getReader()
      const decoder = new TextDecoder('utf-8', { fatal: true })
      let size = 0, body = ''
      while (true) {
        const { done, value } = await guarded(reader.read())
        owned()
        if (done) break
        size += value.byteLength
        if (size > cap) fail('result_too_large')
        try { body += decoder.decode(value, { stream: true }) } catch { fail() }
      }
      let value
      try { value = uniqueJson(body + decoder.decode()) } catch { fail() }
      if (response.status !== 200 || value?.success !== true) {
        fields(value, 'success error'); fields(value.error, 'code')
        const codes = native ? NATIVE_ERROR_STATUS : preparationErrors
        if (value.success !== false || !Object.hasOwn(codes, value.error.code) || codes[value.error.code] !== response.status) fail()
        const error = new WorkbenchError(value.error.code)
        error.replyConfirmed = true
        throw error
      }
      fields(value, 'success data')
      const commit = await guarded(consume(value.data))
      owned()
      commit()
    } catch (error) {
      if (error instanceof WorkbenchError && ['unauthorized', 'origin_denied'].includes(error.code)) throw error
      owned()
      if (error instanceof WorkbenchError) throw error
      fail('transport_failure')
    } finally {
      clearTimeout(timer); controller.abort()
      if (active === controller) active = null
      if (reader) { try { void reader.cancel().catch(() => {}) } catch { /* discard */ } reader.releaseLock() }
      else if (responseBody) { try { void responseBody.cancel().catch(() => {}) } catch { /* discard */ } }
    }
  }

  async function declare(seeds, ready) {
    const context = find(ready)
    const { current, donor, key } = context
    let { record } = context
    seeds = exactSeeds(seeds, donor)
    if (!record) {
      if (declarations.size >= 100) fail('busy')
      record = { donor, payload: { ...preparationIdentity(donor), idempotency_key: newPreparationId(), seeds },
        reply: null, launches: [null, null], attempted: [false, false], firstSettled: false }
      declarations.set(key, record)
    } else if (!same(seeds, record.payload.seeds)) fail('conflict')

    await request(context, `/api/preparation/${current.graph}/seed_ensemble`, record.payload, async value => {
      const reply = await validateDeclaration(value, donor, record.payload, record.reply)
      return () => { record.reply = reply; admission = { record, current } }
    })
    return snapshot(record)
  }

  async function launch(action, index, ready) {
    const context = find(ready)
    const { current, record } = context
    if (!record?.reply || admission?.record !== record || admission.current !== current ||
        ![0, 1].includes(index) || !['plan', 'start', 'status'].includes(action)) fail('invalid_request')
    const member = record.reply.members[index]
    const known = record.launches[index]
    const planning = action === 'plan', starting = action === 'start'
    if (planning ? known : !known || starting && !ensembleCanStart(snapshot(record).members, index)) fail('conflict')
    const payload = planning ? { schema_version: 1, launch_id: member.launch_id,
      preparation: { operation_id: member.operation_id, plan_sha256: member.plan_sha256 } } : nativeLaunchIdentity(known)
    nativeLaunchPayload(payload, planning)
    try {
      await request(context, `/api/native-launch/${action}/${current.graph}`, payload, async value => {
        const result = await validateNativeLaunchResult(value, { graph: current.graph, payload, preparation: member, known, planning })
        const running = ['planned', 'queued', 'starting', 'running', 'uncertain']
        if (known && running.includes(result.state) &&
            (!running.includes(known.state) || running.indexOf(result.state) < running.indexOf(known.state))) fail()
        return () => {
          record.launches[index] = result
          if (index === 0 && action === 'status') {
            record.firstSettled = record.attempted[0] && result.state === 'completed' && result.receipt?.outcome === 'completed' &&
              !result.cancel_requested && (!result.cleanup.known || !result.cleanup.pending && !result.cleanup.owner_thread_alive)
          }
        }
      }, starting ? () => { record.attempted[index] = true } : undefined)
    } catch (error) {
      // The authoritative seed member transaction returns busy before claiming.
      if (starting && error.code === 'busy' && error.replyConfirmed) {
        record.attempted[index] = false
        if (index === 1) { record.firstSettled = false; error.refreshFirst = true }
      }
      if (starting) error.attempted = record.attempted[index]
      throw error
    }
    return snapshot(record)
  }
  return { declare, launch, pendingSeeds, cancel, disconnect }
}
