// Bounded browser-only observers and the saved ontology journey.
export function installBodyObserver() {
  const fetch = globalThis.fetch, getReader = ReadableStream.prototype.getReader
  const read = ReadableStreamDefaultReader.prototype.read, then = Promise.prototype.then
  const streams = new WeakMap(), readers = new WeakMap()
  let active = null
  const refused = record => { if (record === active) { record.failed = true; record.bytes = null } }
  const watch = (promise, record, observe) => {
    Reflect.apply(then, promise, [value => {
      if (record !== active || record.failed) return
      try { observe(value) } catch { refused(record) }
    }, () => refused(record)])
  }
  globalThis.fetch = function (...args) {
    const promise = Reflect.apply(fetch, this, args), record = active
    if (record && args[0] === record.url) {
      if (args[1]?.method !== record.method || record.seen++) refused(record)
      else watch(promise, record, response => {
        const length = response.headers.get('content-length')
        if (response.url !== record.url || response.status !== 200 || response.redirected
          || response.type !== 'cors' || !/^\d+$/.test(length || '')
          || !Number.isSafeInteger(Number(length)) || Number(length) < 1 || Number(length) > record.cap
          || response.headers.get('content-encoding')
          || !/^application\/json(?:\s*;|$)/i.test(response.headers.get('content-type') || '')) throw Error()
        record.length = Number(length); record.bytes = new Uint8Array(record.length)
        streams.set(response.body, record)
      })
    }
    return promise
  }
  ReadableStream.prototype.getReader = function (...args) {
    const reader = Reflect.apply(getReader, this, args), record = streams.get(this)
    if (record === active) readers.set(reader, record)
    return reader
  }
  ReadableStreamDefaultReader.prototype.read = function (...args) {
    const promise = Reflect.apply(read, this, args), record = readers.get(this)
    if (record && record === active) watch(promise, record, chunk => {
      if (record.done) throw Error()
      if (chunk.done) {
        if (record.size !== record.length) throw Error()
        record.done = true
      } else {
        if (!(chunk.value instanceof Uint8Array) || chunk.value.byteLength > record.length - record.size) throw Error()
        record.bytes.set(chunk.value, record.size); record.size += chunk.value.byteLength
      }
    })
    return promise
  }
  globalThis.__researchBody = {
    arm(value) {
      if (active || !Number.isSafeInteger(value.id) || value.cap < 1 || value.cap > 4195328) throw Error()
      active = { ...value, seen: 0, size: 0, offset: 0, done: false, failed: false, bytes: null }
      const record = active
      record.timer = setTimeout(() => refused(record), value.end - Date.now())
    },
    state(id) { return active?.id === id ? { done: active.done, failed: active.failed, length: active.length } : { failed: true } },
    slice({ id, offset }) {
      if (active?.id !== id || active.failed || !active.done || offset !== active.offset) throw Error()
      const bytes = Array.from(active.bytes.subarray(offset, offset + 1024))
      active.offset += bytes.length
      return { id, offset, bytes }
    },
    clear() { if (active) { clearTimeout(active.timer); active.bytes = null } active = null },
  }
}

const RESPONSE_LIMIT = 263168
const PERSON = { name: 'Person', description: 'Named human',
  attributes: [{ name: 'profile_note', type: 'text', description: 'Retained profile note' }],
  examples: ['Named person in retained evidence'] }
const ORGANIZATION = { name: 'Organization', description: 'Named organization',
  attributes: [{ name: 'organization_note', type: 'text', description: 'Retained organization note' }] }
const RELATIONSHIP = { name: 'WORKS_FOR', description: 'Employment relationship',
  attributes: [{ name: 'employment_note', type: 'text', description: 'Retained employment note' }],
  source_targets: [{ source: 'Person', target: 'Organization' }, { source: 'Entity', target: 'Organization' }] }
const ONTOLOGY = { entity_types: [PERSON, ORGANIZATION], edge_types: [RELATIONSHIP] }

export function ontologyStages({ run, exchange, reconnect, restoreSource, requireTrue, same, keys, uuid, digest, integer }) {
  let saved, plan, completed
  const click = (target, timeout) => target.click({ timeout: timeout() })
  const input = (target, value, timeout) => target.fill(value, { timeout: timeout() })
  const choose = (target, value, timeout) => target.selectOption(value, { timeout: timeout() })
  const button = (scope, name) => scope.getByRole('button', { name, exact: true })
  const visible = (locator, timeout) => locator.waitFor({ state: 'visible', timeout: timeout() })
  const ingestion = method => graph => `/api/source/ingestion/${method}/${graph}`
  const executeRoute = ingestion('execute')

  async function fillAttribute(row, kind, attribute, timeout) {
    await click(button(row, 'Add attribute'), timeout)
    await input(row.locator(`input[id^="${kind}-attribute-name-"]`), attribute.name, timeout)
    await choose(row.locator(`select[id^="${kind}-attribute-type-"]`), attribute.type, timeout)
    await input(row.locator(`textarea[id^="${kind}-attribute-description-"]`), attribute.description, timeout)
  }

  async function review({ page, graph, source, request, count, timeout }) {
    keys(request, ['source_revision'])
    const scope = page.locator('.source-ingestion')
    const route = `/api/source/ontology/${graph}`
    const mode = scope.locator('#reviewed-ontology-mode')
    const load = scope.locator('#reviewed-ontology-load')
    const save = scope.locator('#reviewed-ontology-save')
    await click(mode, timeout)
    const initial = await exchange(page, route, 'GET', () => click(load, timeout), RESPONSE_LIMIT)
    requireTrue(initial.value.schema_version === 1 && initial.value.scope.project_id === source.project_id)
    integer(initial.value.project_revision, 2147483646, 1)
    requireTrue(initial.value.ontology === null || !same(initial.value.ontology, ONTOLOGY))
    for (const label of [/^Remove relationship \d+$/, /^Remove entity type \d+$/]) {
      const rows = scope.getByRole('button', { name: label })
      const total = await rows.count()
      integer(total, 10)
      for (let index = 0; index < total; index++) await click(rows.last(), timeout)
      requireTrue(await rows.count() === 0)
    }

    const entities = scope.locator('.ontology-item:has(input[id^="entity-name-"])')
    for (const [ordinal, model] of ONTOLOGY.entity_types.entries()) {
      await click(button(scope, 'Add entity type'), timeout)
      const entity = entities.nth(ordinal)
      await input(entity.locator('input[id^="entity-name-"]'), model.name, timeout)
      await input(entity.locator('textarea[id^="entity-description-"]'), model.description, timeout)
      await fillAttribute(entity, 'entity', model.attributes[0], timeout)
    }
    const first = entities.first()
    await click(button(first, 'Add example'), timeout)
    await input(first.locator('textarea:not([id])'), PERSON.examples[0], timeout)
    await click(button(scope, 'Add relationship'), timeout)
    const edge = scope.locator('.ontology-item:has(input[id^="edge-name-"])')
    await input(edge.locator('input[id^="edge-name-"]'), RELATIONSHIP.name, timeout)
    await input(edge.locator('textarea[id^="edge-description-"]'), RELATIONSHIP.description, timeout)
    await fillAttribute(edge, 'edge', RELATIONSHIP.attributes[0], timeout)
    await click(button(edge, 'Add source/target pair'), timeout)
    const pairs = edge.locator('.pair')
    for (const pair of [pairs.nth(0), pairs.nth(1)]) {
      await choose(pair.locator('select[id^="edge-pair-source-"]'), 'Person', timeout)
      await choose(pair.locator('select[id^="edge-pair-target-"]'), 'Organization', timeout)
    }
    await click(save, timeout)
    await visible(scope.locator('#reviewed-ontology-status').filter({ hasText: 'Check the names, descriptions, attributes, examples, and relationship endpoints before saving.' }), timeout)
    requireTrue(count('POST', route) === 0)
    await choose(pairs.nth(1).locator('select[id^="edge-pair-source-"]'), 'Entity', timeout)
    const write = await exchange(page, route, 'POST', () => click(save, timeout), RESPONSE_LIMIT)
    requireTrue(same(write.request, { schema_version: 1, expected_revision: initial.value.project_revision, ontology: ONTOLOGY }))
    requireTrue(same(write.value, { ...initial.value, project_revision: initial.value.project_revision + 1, ontology: ONTOLOGY }))
    await click(mode, timeout)
    await click(mode, timeout)
    const reload = await exchange(page, route, 'GET', () => click(load, timeout), RESPONSE_LIMIT)
    requireTrue(same(reload.value, write.value) && count('GET', route) === 2 && count('POST', route) === 1)
    await visible(scope.locator('#saved-ingestion-plan'), timeout)
    requireTrue(await first.locator('textarea:not([id])').inputValue({ timeout: timeout() }) === PERSON.examples[0])
    requireTrue(await pairs.nth(1).locator('select[id^="edge-pair-source-"]').inputValue({ timeout: timeout() }) === 'Entity')
    saved = reload.value
    return { reference: { source_revision: source.source_revision, project_revision: saved.project_revision },
      proof: { invalid_save_requests: 0, save_requests: 1, request_sha256: write.request_sha256, reload_exact: true } }
  }

  async function makePlan({ page, graph, source, request, count, timeout }) {
    keys(request, ['source_revision', 'project_revision'])
    requireTrue(request.project_revision === saved?.project_revision)
    const route = ingestion('plan')(graph)
    const shown = await exchange(page, route, 'POST',
      () => click(page.locator('#saved-ingestion-plan'), timeout), RESPONSE_LIMIT)
    const value = shown.value
    uuid(value.operation_id); uuid(value.episode_id); uuid(value.ontology_revision)
    digest(value.fingerprint); digest(value.execution_fingerprint)
    const payload = { schema_version: 4, ...request, operation_id: value.operation_id }
    requireTrue(same(shown.request, payload) && value.schema_version === 4 &&
      value.source_revision === source.source_revision && value.source_sha256 === source.text_sha256 &&
      value.project_revision === request.project_revision && same(value.scope, saved.scope) &&
      value.fingerprint !== value.execution_fingerprint && value.spending_authorized === false &&
      value.graph_ingestion_executed === false && value.model_calls_made === false)
    const review = page.locator('.source-ingestion .plan-review')
    await visible(review, timeout)
    for (const valueShown of [value.operation_id, value.fingerprint, value.execution_fingerprint]) {
      await visible(review.getByText(valueShown, { exact: true }), timeout)
    }
    const confirm = review.locator('.acknowledgement input')
    requireTrue(!await confirm.isChecked({ timeout: timeout() }) &&
      await review.locator('button.primary').isDisabled({ timeout: timeout() }) &&
      count('POST', route) === 1 && count('POST', executeRoute(graph)) === 0)
    plan = { payload, value }
    return { reference: { ...request, operation_id: value.operation_id },
      proof: { request_sha256: shown.request_sha256, episode_id: value.episode_id,
        fingerprint: value.fingerprint, execution_fingerprint: value.execution_fingerprint,
        unchecked_execute_disabled: true } }
  }

  function checkCompletion(value) {
    requireTrue(value.schema_version === 1 && value.operation_id === plan.value.operation_id &&
      value.episode_id === plan.value.episode_id && value.fingerprint === plan.value.execution_fingerprint &&
      same(value.scope, saved.scope) && same(value.evidence_ids, plan.value.evidence_ids) &&
      value.state === 'completed' && value.budget_state === 'settled' && value.graph_ingestion_executed === true &&
      value.receipt?.episode_id === value.episode_id && value.receipt.fingerprint === value.fingerprint &&
      same(value.receipt.evidence_ids, value.evidence_ids))
  }

  async function execute({ page, graph, request, count, timeout }) {
    keys(request, ['source_revision', 'operation_id'])
    requireTrue(request.operation_id === plan?.value.operation_id && !completed)
    const panel = page.locator('.source-ingestion .plan-review')
    const action = panel.locator('button.primary')
    const checkbox = panel.locator('.acknowledgement input')
    requireTrue(await action.isDisabled({ timeout: timeout() }) && !await checkbox.isChecked({ timeout: timeout() }))
    await checkbox.check({ timeout: timeout() })
    requireTrue(await checkbox.isChecked({ timeout: timeout() }))
    const route = executeRoute(graph)
    const sent = await exchange(page, route, 'POST', () => click(action, timeout), RESPONSE_LIMIT)
    requireTrue(same(sent.request, { ...plan.payload, fingerprint: plan.value.fingerprint,
      execution_fingerprint: plan.value.execution_fingerprint }))
    checkCompletion(sent.value)
    await visible(page.locator('.source-ingestion .outcome').getByText(request.operation_id, { exact: true }), timeout)
    requireTrue(count('POST', route) === 1)
    completed = sent.value
    return { reference: request, proof: { request_sha256: sent.request_sha256, execute_requests: 1,
      episode_id: completed.episode_id, fingerprint: completed.fingerprint, state: completed.state,
      budget_state: completed.budget_state } }
  }

  async function recover({ page, graph, request, count, timeout }) {
    keys(request, ['source_revision', 'operation_id'])
    requireTrue(request.operation_id === completed?.operation_id)
    await click(page.locator('.connection-form button[type=button]').last(), timeout)
    await reconnect()
    const scope = page.locator('.source-ingestion')
    requireTrue(await scope.locator('.attempt').count() === 0)
    const manual = scope.locator('.manual-recovery')
    await input(manual.locator('#ingestion-operation'), request.operation_id, timeout)
    const route = ingestion('operation')(graph) + '/' + request.operation_id
    const recovered = await exchange(page, route, 'GET', () => click(manual.locator('button[type=submit]'), timeout), RESPONSE_LIMIT)
    checkCompletion(recovered.value)
    requireTrue(same(recovered.value.receipt, completed.receipt) &&
      count('POST', executeRoute(graph)) === 0 && count('GET', route) === 1)
    await visible(scope.locator('.outcome').getByText(request.operation_id, { exact: true }), timeout)
    await restoreSource()
    return { reference: request, proof: { episode_id: recovered.value.episode_id,
      fingerprint: recovered.value.fingerprint, state: recovered.value.state,
      budget_state: recovered.value.budget_state, execute_requests: 0 } }
  }

  return { 'ontology-review': run(review), 'ontology-plan': run(makePlan),
    'ontology-execute': run(execute), 'ontology-recover': run(recover) }
}
